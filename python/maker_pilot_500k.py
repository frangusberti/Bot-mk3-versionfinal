"""
maker_pilot_500k.py — Phase 1 Maker Pilot (500k Steps)
Strict diagnostics, early-stop gates, and Maker behavior validation.
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

# --- CONFIGURATION ---
RUN_ID = f"maker_pilot_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DATASET_ID = "stage2_train"
EVAL_DATASET_ID = "stage2_eval"
LOG_DIR = f"python/runs_train/maker_pilot/{RUN_ID}"
os.makedirs(LOG_DIR, exist_ok=True)

ACTION_LABELS = [
    "HOLD", "POST_BID", "JOIN_BID", "POST_ASK", "JOIN_ASK", "CANCEL_ALL", "TAKER_EXIT",
    "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL", "REDUCE_25", "REDUCE_50", "REDUCE_100",
]

# Real World Costs
COSTS = {
    "maker": 2.0,  # 2.0 bps
    "taker": 5.0,  # 5.0 bps
    "slip": 1.0,   # 1 bps
}

HYPERPARAMS = {
    "learning_rate": 2e-4,
    "ent_coef": 0.05,
    "batch_size": 256,
    "n_steps": 2048,
}

# --- SYSTEM SETTINGS ---
p = psutil.Process(os.getpid())
if sys.platform == 'win32':
    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)

# --- DIAGNOSTIC CALLBACK ---

class MakerPilotCallback(BaseCallback):
    def __init__(self, verbose=0):
        super().__init__(verbose)
        self.stop_triggered = False
        self.diagnostics_path = f"{LOG_DIR}/pilot_diagnostics.txt"
        
    def _on_step(self) -> bool:
        steps = self.num_timesteps
        # Audit at 1, 100k, 250k, 500k
        if steps == 1 or steps % 100000 == 0 or steps == 250000 or steps == 500000:
            self.run_detailed_audit()
            
        # Early Stop Check at 250k
        if steps == 250000:
            if not self.check_continuation_criteria():
                print(f"\n[CRITICAL] Early stop gate triggered at 250k steps. Zero maker fills.")
                self.stop_triggered = True
                return False
                
        return True

    def run_detailed_audit(self):
        steps = self.num_timesteps
        print(f"\n[{RUN_ID}] Running Detailed Audit at {steps} steps...")
        
        env = GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id=EVAL_DATASET_ID,
            symbol="BTCUSDT",
            maker_fee=COSTS["maker"],
            taker_fee=COSTS["taker"],
            slip_bps=COSTS["slip"]
        )
        
        obs, info = env.reset()
        initial_equity = info.get("equity", 10000.0)
        
        # Track Deterministic vs Stochastic
        det_actions = []
        stoch_actions = []
        rewards = []
        equities = [initial_equity]
        
        metrics = {
            "trades": 0,
            "maker_fills": 0,
            "toxic_fills": 0,
            "stale_expiries": 0,
            "cancels": 0
        }

        # Run Audit Episode
        for _ in range(2000):
            # Deterministic
            det_act, _ = self.model.predict(obs, deterministic=True)
            det_actions.append(int(det_act))
            
            # Stochastic (for dist comparison)
            stoch_act, _ = self.model.predict(obs, deterministic=False)
            stoch_actions.append(int(stoch_act))
            
            # Step with Deterministic for PnL evaluation
            obs, reward, terminated, truncated, info = env.step(int(det_act))
            
            rewards.append(reward)
            metrics["trades"] += info.get("trades_executed", 0)
            metrics["maker_fills"] += info.get("maker_fills", 0)
            metrics["toxic_fills"] += info.get("toxic_fills", 0)
            metrics["stale_expiries"] += info.get("stale_expiries", 0)
            metrics["cancels"] += 1 if int(det_act) == 5 else 0 # CANCEL_ALL
            
            if "equity" in info: equities.append(info["equity"])
            if terminated or truncated: break
            
        env.close()
        
        # PnL & Stats
        pnl = (equities[-1] / initial_equity - 1) * 100
        total_steps = len(det_actions)
        
        def get_dist(acts):
            counts = {i: acts.count(i) for i in range(13)}
            return {ACTION_LABELS[i]: counts[i] / max(1, len(acts)) for i in range(13)}

        det_dist = get_dist(det_actions)
        stoch_dist = get_dist(stoch_actions)
        
        # --- Terminal Report ---
        print(f"--- Pilot Audit Checkpoint ({steps} steps) ---")
        print(f"PnL: {pnl:.2f}% | Equity: {equities[-1]:.2f}")
        print(f"Fills: Maker={metrics['maker_fills']} | Toxic={metrics['toxic_fills']} | Stale={metrics['stale_expiries']}")
        print(f"Dominant (Det): {max(det_dist, key=det_dist.get)} ({det_dist[max(det_dist, key=det_dist.get)]*100:.1f}%)")
        print(f"Entropy (Approx): {getattr(self.model.policy, 'log_std', torch.tensor(0.0)).mean().item():.2f}")
        
        # Save JSON
        report = {
            "step": steps,
            "pnl": pnl,
            "metrics": metrics,
            "det_dist": det_dist,
            "stoch_dist": stoch_dist,
            "mean_reward": np.mean(rewards),
            "final_equity": equities[-1]
        }
        
        with open(f"{LOG_DIR}/audit_{steps}.json", "w") as f:
            json.dump(report, f, indent=2)
            
        # Append to log
        with open(self.diagnostics_path, "a") as f:
            f.write(f"\nAudit at {steps} steps:\n")
            f.write(f"PnL: {pnl:.2f}%, Fills: {metrics['maker_fills']}, Dominant: {max(det_dist, key=det_dist.get)}\n")

    def check_continuation_criteria(self) -> bool:
        """Requirement 5: stop if zero maker fills and TAKER_EXIT dominant at 250k."""
        # Load the 250k audit
        try:
            with open(f"{LOG_DIR}/audit_250000.json", "r") as f:
                data = json.load(f)
            
            fills = data["metrics"]["maker_fills"]
            dominant = max(data["det_dist"], key=data["det_dist"].get)
            
            if fills == 0 and dominant == "TAKER_EXIT":
                return False
            return True
        except Exception as e:
            print(f"Error checking continuation: {e}")
            return True

def train():
    env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id=DATASET_ID, 
        symbol="BTCUSDT",
        maker_fee=COSTS["maker"],
        taker_fee=COSTS["taker"],
        slip_bps=COSTS["slip"]
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

    print(f"[{RUN_ID}] Launching 500k Maker Pilot...")
    try:
        model.learn(
            total_timesteps=500000,
            callback=MakerPilotCallback(),
            progress_bar=True
        )
        model.save(f"{LOG_DIR}/pilot_model_final")
    except Exception as e:
        print(f"\n[PILOT INTERRUPTED] {e}")
    finally:
        env.close()

if __name__ == "__main__":
    train()
