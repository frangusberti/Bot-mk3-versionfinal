"""
maker_v3_validation.py — Maker 1.0M Validation Run
Evaluates:
1. Behavioral Success (Posting)
2. Economic Success (Fills & PnL)

Curriculum:
1. Stage A: 0-250k (Optimistic)
2. Stage B: 250k-600k (Semi-Optimistic)
3. Stage C: 600k-1M (Conservative)
"""

import os
import sys
import psutil
import time
import json
import torch
import numpy as np
from datetime import datetime

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from grpc_env import GrpcTradingEnv
import bot_pb2

# --- CONFIGURATION ---
RUN_ID = f"maker_v3_val_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DATASET_ID = "stage2_train"
EVAL_DATASET_ID = "stage2_eval"
LOG_DIR = f"python/runs_train/maker_v3/{RUN_ID}"
os.makedirs(LOG_DIR, exist_ok=True)

ACTION_LABELS = [
    "HOLD", "POST_BID", "JOIN_BID", "POST_ASK", "JOIN_ASK", "CANCEL_ALL", "TAKER_EXIT",
    "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL", "REDUCE_25", "REDUCE_50", "REDUCE_100",
]

COSTS = {
    "maker": 2.0,
    "taker": 5.0,
    "slip": 1.0,
}

HYPERPARAMS = {
    "learning_rate": 2e-4,
    "ent_coef": 0.05,
    "batch_size": 256,
    "n_steps": 2048,
}

# 1.0M Validation Curriculum
PHASES = {
    "STAGE_A": {
        "end_step": 250000,
        "fill_model": bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC,
        "tib_bonus": 0.20,
    },
    "STAGE_B": {
        "end_step": 600000,
        "fill_model": bot_pb2.MAKER_FILL_MODEL_SEMI_OPTIMISTIC,
        "tib_bonus": 0.05,
    },
    "STAGE_C": {
        "end_step": 1000000,
        "fill_model": bot_pb2.MAKER_FILL_MODEL_CONSERVATIVE,
        "tib_bonus": 0.0,
    }
}

# --- SYSTEM SETTINGS ---
p = psutil.Process(os.getpid())
if sys.platform == 'win32':
    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)

class MakerValidationCallback(BaseCallback):
    def __init__(self, verbose=0):
        super().__init__(verbose)
        self.current_phase = "STAGE_A"
        self.diagnostics_path = f"{LOG_DIR}/validation_report.txt"
        with open(self.diagnostics_path, "w") as f:
            f.write(f"Maker 1.0M Validation Run: {RUN_ID}\n")
            f.write("="*50 + "\n")
        
    def _on_step(self) -> bool:
        steps = self.num_timesteps
        
        # Phase Transition Logic
        new_phase = self.current_phase
        if steps > PHASES["STAGE_B"]["end_step"]:
            new_phase = "STAGE_C"
        elif steps > PHASES["STAGE_A"]["end_step"]:
            new_phase = "STAGE_B"
            
        if new_phase != self.current_phase:
            print(f"\n[PHASE TRANSITION] {self.current_phase} -> {new_phase} at {steps} steps")
            self.current_phase = new_phase
            self.update_env_config()
            
        # Audit every 50k steps + Specific Checkpoints
        if steps == 1 or steps % 50000 == 0:
            self.run_audit()
            
        return True

    def update_env_config(self):
        old_phase = self.current_phase
        cfg = PHASES[self.current_phase]

        # Map proto fill_model int to human name for logging
        _fill_model_names = {
            bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC: "OPTIMISTIC",
            bot_pb2.MAKER_FILL_MODEL_SEMI_OPTIMISTIC: "SEMI_OPTIMISTIC",
            bot_pb2.MAKER_FILL_MODEL_CONSERVATIVE: "CONSERVATIVE",
        }
        old_fill_name = _fill_model_names.get(
            PHASES.get(old_phase, {}).get("fill_model", -1), "UNKNOWN"
        )
        new_fill_name = _fill_model_names.get(cfg["fill_model"], "UNKNOWN")

        new_rl_config = bot_pb2.RLConfig(
            fill_model=cfg["fill_model"],
            reward_tib_bonus_bps=cfg["tib_bonus"],
            initial_equity=10000.0,
            maker_fee=COSTS["maker"],
            taker_fee=COSTS["taker"],
            slip_bps=COSTS["slip"],
            feature_profile="Rich",
            decision_interval_ms=1000
        )
        self.training_env.set_attr("rl_config", new_rl_config)

        # --- A3 Fix: force reset so new fill_model takes effect immediately ---
        # set_attr only updates the Python attribute; the server-side EpisodeHandle
        # still uses the old fill_model until the episode ends. Force a reset now.
        print(f"[PHASE_CHANGE] {old_phase} -> {self.current_phase}")
        print(f"  fill_model: {old_fill_name} -> {new_fill_name}")
        print(f"  tib_bonus: {PHASES.get(old_phase, {}).get('tib_bonus', '?')} -> {cfg['tib_bonus']}")

        try:
            obs = self.training_env.reset()
            # SB3 VecEnv reset() does not return episode_id directly,
            # but the next gRPC Reset will use the new rl_config.
            # Log confirmation via the env's internal episode_id attribute.
            try:
                ep_ids = self.training_env.get_attr("episode_id")
                print(f"  new_episode_ids after reset: {ep_ids}")
            except Exception:
                print(f"  new_episode_ids: (not retrievable via get_attr)")
            print(f"  [CONFIRMED] New fill_model={new_fill_name} active from next step.")
        except Exception as e:
            print(f"  [WARN] training_env.reset() failed after phase change: {e}")
            print(f"  New fill_model will take effect at next natural episode boundary.")

    def run_audit(self):
        steps = self.num_timesteps
        cfg = PHASES[self.current_phase]
        print(f"\n[{RUN_ID}] Auditing Checkpoint: {steps} ({self.current_phase})...")
        
        env = GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id=EVAL_DATASET_ID,
            symbol="BTCUSDT",
            maker_fee=COSTS["maker"],
            taker_fee=COSTS["taker"],
            slip_bps=COSTS["slip"],
            fill_model=cfg["fill_model"],
            reward_tib_bonus_bps=0.0 # No bonuses in audit
        )
        
        obs, info = env.reset()
        initial_equity = info.get("equity", -1.0)

        # --- A1 Fix: Explicit equity assert — catches stale server state ---
        if abs(initial_equity - 10000.0) > 5.0:
            print(f"[AUDIT_BUG] initial_equity={initial_equity:.2f} (expected ~10000). "
                  f"Server state may be stale. Audit at step {steps} is UNRELIABLE.")
        else:
            print(f"[AUDIT_OK] initial_equity={initial_equity:.2f} [OK]")

        det_actions = []
        metrics = {
            "maker_fills": 0, 
            "toxic_fills": 0, 
            "stale_expiries": 0,
            "cancel_count": 0,
            "trades": 0,
            "active_orders_sum": 0,
            "steps": 0
        }
        equities = [initial_equity]

        for _ in range(5000): # Longer audit window for validation
            act, _ = self.model.predict(obs, deterministic=True)
            det_actions.append(int(act))
            obs, reward, terminated, truncated, info = env.step(int(act))
            
            metrics["maker_fills"] += info.get("maker_fills", 0)
            metrics["toxic_fills"] += info.get("toxic_fills", 0)
            metrics["stale_expiries"] += info.get("stale_expiries", 0)
            metrics["cancel_count"] += info.get("cancel_count", 0)
            metrics["trades"] += info.get("trades_executed", 0)
            metrics["active_orders_sum"] += info.get("active_order_count", 0)
            metrics["steps"] += 1
            
            if "equity" in info: equities.append(info["equity"])
            if terminated or truncated: break
            
        env.close()
        
        realized_pnl = info.get("realized_pnl", 0.0)
        final_equity = equities[-1]
        pnl_pct = (final_equity / initial_equity - 1) * 100
        
        def get_dist(acts):
            counts = {i: acts.count(i) for i in range(13)}
            return {ACTION_LABELS[i]: counts[i] / max(1, len(acts)) for i in range(13)}

        det_dist = get_dist(det_actions)
        dominant = max(det_dist, key=det_dist.get)
        avg_active_orders = metrics["active_orders_sum"] / max(1, metrics["steps"])
        
        # Diagnosis Logic
        behavioral_success = dominant in ["POST_BID", "JOIN_BID", "POST_ASK", "JOIN_ASK"]
        economic_success = metrics["maker_fills"] > 0
        
        warning = ""
        if steps >= 500000 and economic_success == False and behavioral_success == True:
            warning = "[WARNING] Policy prefers Maker actions but has ZERO fills. Flagged as 'Posting Preference only'."

        report_entry = f"""
Checkpoint: {steps} steps
Phase: {self.current_phase}
Dominant Action: {dominant}
Behavioral Success: {behavioral_success}
Economic Success: {economic_success}
Maker Fills: {metrics['maker_fills']}
Toxic Fills: {metrics['toxic_fills']}
Stale Expiries: {metrics['stale_expiries']}
Cancel Count: {metrics['cancel_count']}
Avg Active Orders: {avg_active_orders:.2f}
Realized PnL: {realized_pnl:.4f}
Final Equity: {final_equity:.2f} ({pnl_pct:.2f}%)
{warning}
--------------------------------------------------
"""
        print(report_entry)
        with open(self.diagnostics_path, "a") as f:
            f.write(report_entry)

        with open(f"{LOG_DIR}/audit_{steps}.json", "w") as f:
            json.dump({
                "step": steps,
                "phase": self.current_phase,
                "dominant": dominant,
                "behavioral_success": behavioral_success,
                "economic_success": economic_success,
                "metrics": metrics,
                "avg_active_orders": avg_active_orders,
                "realized_pnl": realized_pnl,
                "pnl_pct": pnl_pct,
                "det_dist": det_dist,
                "final_equity": final_equity,
                "initial_equity": initial_equity,
                "warning": warning,
            }, f, indent=2)

def train():
    # Start with Stage A
    env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id=DATASET_ID, 
        symbol="BTCUSDT",
        maker_fee=COSTS["maker"],
        taker_fee=COSTS["taker"],
        slip_bps=COSTS["slip"],
        fill_model=PHASES["STAGE_A"]["fill_model"],
        reward_tib_bonus_bps=PHASES["STAGE_A"]["tib_bonus"]
    )

    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        learning_rate=HYPERPARAMS["learning_rate"],
        n_steps=HYPERPARAMS["n_steps"],
        batch_size=HYPERPARAMS["batch_size"],
        n_epochs=10,
        ent_coef=HYPERPARAMS["ent_coef"],
        tensorboard_log=LOG_DIR,
        device="cpu"
    )

    print(f"[{RUN_ID}] Starting 1.0M Maker Validation Run...")
    try:
        model.learn(
            total_timesteps=1000000,
            callback=MakerValidationCallback(),
            progress_bar=True
        )
        model.save(f"{LOG_DIR}/validation_model_final")
    except Exception as e:
        print(f"\n[TRAINING INTERRUPTED] {e}")
    finally:
        env.close()

if __name__ == "__main__":
    train()
