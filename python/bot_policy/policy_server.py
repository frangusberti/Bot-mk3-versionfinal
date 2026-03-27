import os
import time
import json
import logging
import argparse
from typing import List, Optional
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn
from policies import HoldPolicy, HeuristicPolicy, SB3PPOPolicy

# Logging Setup
os.makedirs("data/policy_logs", exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("policy_server")

class InferRequest(BaseModel):
    symbol: str
    ts_ms: int
    mode: str
    decision_interval_ms: int
    obs: List[float]
    risk: dict
    portfolio: dict
    meta: Optional[dict] = {}

class InferResponse(BaseModel):
    action: str
    confidence: float
    reason: str
    policy_version: str
    latency_ms: float
    log_prob: float
    value: float

app = FastAPI(title="ScalpBot Policy Server")

# Global State
class ServerState:
    def __init__(self):
        self.config = {}
        self.policy = None
        self.metrics = {
            "requests": 0,
            "errors": 0,
            "avg_latency": 0.0,
            "total_latency": 0.0
        }
        self.load_config()

    def load_config(self):
        config_path = "python/bot_policy/config/policy_config.json"
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                self.config = json.load(f)
            logger.info(f"Config loaded: {self.config}")
        else:
            self.config = {
                "policy_type": "hold",
                "cooldown_ms": 2000
            }
            logger.warning("Config not found, using defaults")
            
        p_type = self.config.get("policy_type", "hold")
        if p_type == "heuristic":
            self.policy = HeuristicPolicy()
        elif p_type == "sb3_ppo":
            self.policy = SB3PPOPolicy(self.config.get("model_path"))
        else:
            self.policy = HoldPolicy()
        logger.info(f"Policy initialized: {p_type}")

state = ServerState()

def log_decision(data):
    date_str = time.strftime("%Y%m%d")
    log_path = os.path.join(state.config.get("log_dir", "data/policy_logs"), f"policy_{date_str}.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(data) + "\n")

@app.get("/health")
def health():
    return {
        "status": "OK",
        "version": "1.0.0",
        "policy_type": state.config.get("policy_type"),
        "last_error": None
    }

class ProfileResponse(BaseModel):
    schema_version: int
    obs_dim: int
    policy_type: str
    model_path: Optional[str]

@app.get("/profile", response_model=ProfileResponse)
def get_profile():
    schema_v = state.config.get("schema_version", 1)
    obs_dim = 148 if schema_v >= 6 else (118 if schema_v == 5 else (76 if schema_v >= 4 else (70 if schema_v == 3 else 0)))
    
    if state.policy is not None and hasattr(state.policy, "model") and getattr(state.policy, "model", None) is not None:
        if hasattr(state.policy.model, "observation_space"):
            obs_dim = state.policy.model.observation_space.shape[0]
            
    return ProfileResponse(
        schema_version=state.config.get("schema_version", 1),
        obs_dim=obs_dim,
        policy_type=state.config.get("policy_type", "hold"),
        model_path=state.config.get("model_path")
    )

@app.post("/infer", response_model=InferResponse)
async def infer(cmd: InferRequest):
    start_ts = time.perf_counter()
    state.metrics["requests"] += 1
    
    schema_v = state.config.get("schema_version", 1)
    is_valid = True
    reason_invalid = ""
    
    if schema_v >= 3 and len(cmd.obs) >= 70:
        if schema_v >= 6:
            offset = 74
        elif schema_v == 5:
            offset = 59
        elif schema_v == 4:
            offset = 38
        else:
            offset = 35
        critical_idx_map = {
            "mid_price": 0,
            "spread_bps": 2,
            "obi_top1": 14,
            "microprice": 16
        }
        for name, idx in critical_idx_map.items():
            mask_idx = offset + idx
            if mask_idx < len(cmd.obs) and cmd.obs[mask_idx] == 0.0:
                is_valid = False
                reason_invalid = f"Critical feature warming up or missing: {name}"
                logger.warning(f"Preventing inference for {cmd.symbol}: {reason_invalid}")
                break

    if not is_valid:
        action, conf, reason, log_prob, value = "HOLD", 0.0, reason_invalid, 0.0, 0.0
    else:
        try:
            action, conf, reason, log_prob, value = state.policy.infer(
                cmd.symbol, 
                cmd.obs, 
                cmd.portfolio, 
                cmd.risk, 
                state.config
            )
        except Exception as e:
            logger.error(f"Inference error for {cmd.symbol}: {e}")
            state.metrics["errors"] += 1
            action, conf, reason, log_prob, value = "HOLD", 0.0, f"error: {str(e)}", 0.0, 0.0

    end_ts = time.perf_counter()
    latency_ms = (end_ts - start_ts) * 1000
    
    # Update metrics
    state.metrics["total_latency"] += latency_ms
    state.metrics["avg_latency"] = state.metrics["total_latency"] / state.metrics["requests"]

    # Log Decision
    log_data = {
        "ts": int(time.time() * 1000),
        "symbol": cmd.symbol,
        "action": action,
        "reason": reason,
        "latency_ms": round(latency_ms, 3),
        "equity": cmd.portfolio.get("equity"),
        "is_flat": cmd.portfolio.get("is_flat"),
        "log_prob": log_prob,
        "value": value,
        "obs": [round(x, 8) for x in cmd.obs[:6]],
    }
    log_decision(log_data)

    return InferResponse(
        action=action,
        confidence=conf,
        reason=reason,
        policy_version=state.config.get("policy_type", "unknown"),
        latency_ms=round(latency_ms, 3),
        log_prob=log_prob,
        value=value
    )

class ReloadRequest(BaseModel):
    model_path: Optional[str] = None
    policy_type: Optional[str] = None

@app.post("/reload")
def reload(req: ReloadRequest):
    # 1. Load from disk first (base config)
    state.load_config()
    
    # 2. Override with request params
    if req.model_path:
        state.config["model_path"] = req.model_path
        logger.info(f"Overriding model_path to {req.model_path}")
        
    if req.policy_type:
        state.config["policy_type"] = req.policy_type
        
    # 3. Re-init Policy
    p_type = state.config.get("policy_type", "hold")
    try:
        if p_type == "heuristic":
            state.policy = HeuristicPolicy()
        elif p_type == "sb3_ppo":
            path = state.config.get("model_path")
            if not path or not os.path.exists(path):
                raise ValueError(f"Model path {path} invalid")
            
            # Governance Check: Feature Profile
            try:
                # 1. Infer Model ID from path (assuming models/live/MODEL_ID/file.zip)
                # or check for a metadata.json in the same dir?
                model_dir = os.path.dirname(path)
                model_id = os.path.basename(model_dir)
                registry_path = "models/registry" # Hardcoded assumption or config?
                meta_path = os.path.join(registry_path, f"{model_id}.json")
                
                if os.path.exists(meta_path):
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                        model_profile = meta.get("feature_profile", "unknown")
                        
                    required_profile = state.config.get("feature_profile", "Rich")
                    
                    # Fuzzy match? "Rich" vs "Rich"
                    if model_profile != "unknown" and model_profile != required_profile:
                         raise ValueError(f"Profile Mismatch! Config requires {required_profile}, model trained with {model_profile}.")
                    logger.info(f"Governance passed: Profile {model_profile} matches {required_profile}")
                else:
                    logger.warning(f"No metadata found at {meta_path}, skipping profile check.")
                    
            except Exception as e:
                logger.error(f"Governance check failed: {e}")
                raise e

            state.policy = SB3PPOPolicy(path)
        else:
            state.policy = HoldPolicy()
            
        logger.info(f"Policy Reloaded: {p_type}")
        return {"status": "reloaded", "config": state.config}
        
    except Exception as e:
        logger.error(f"Failed to reload policy: {e}")
        # Rollback logic? Ideally we keep the old policy if new fails.
        # But we already overwrote state.policy with partial?
        # Actually we didn't overwrite state.config on disk, just in memory.
        # But state.policy is overwritten only if init succeeds?
        # No, 'state.policy = ...' lines above.
        # If SB3PPOPolicy raises, state.policy remains what it was?
        # No, Python assignment happens after RHS evaluation.
        # So yes, it is atomic-ish.
        return HTTPException(status_code=500, detail=f"Failed to reload: {str(e)}")

@app.get("/metrics")
def metrics():
    return state.metrics

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=50055)
    args = parser.parse_args()
    
    logger.info(f"Starting Policy Server on port {args.port}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
