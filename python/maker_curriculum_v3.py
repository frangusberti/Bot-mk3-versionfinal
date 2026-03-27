"""
maker_curriculum_v3.py — Maker Curriculum V3 (Discovery & Annealing)
Solves the reward sparsity trap via:
1. Stage A: Optimistic Discovery (Fill on Touch) + TiB Bonus
2. Stage B: Realism Annealing (Semi-Optimistic Queue) + Reduced TiB Bonus
3. Stage C: Realism (Conservative Queue) + 0 TiB Bonus
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
RUN_ID = f"maker_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DATASET_ID = "stage2_train"
EVAL_DATASET_ID = "stage2_eval"
LOG_DIR = f"python/runs_train/maker_v3/{RUN_ID}"
os.makedirs(LOG_DIR, exist_ok=True)

ACTION_LABELS = [
    "HOLD", "POST_BID", "JOIN_BID", "POST_ASK", "JOIN_ASK", "CANCEL_ALL", "TAKER_EXIT",
    "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL", "REDUCE_25", "REDUCE_50", "REDUCE_100",
]

# Real World Costs
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

# Curriculum Schedule
# Step thresholds: [Stage A -> B, Stage B -> C]
PHASES = {
    "STAGE_A": {
        "end_step": 250000,
        "fill_model": bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC,
        "tib_bonus": 0.20, # 0.2 bps per step
    },
    "STAGE_B": {
        "end_step": 600000,
        "fill_model": bot_pb2.MAKER_FILL_MODEL_SEMI_OPTIMISTIC,
        "tib_bonus": 0.05, # 0.05 bps per step
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

# --- CURRICULUM CALLBACK ---

class MakerCurriculumCallback(BaseCallback):
    def __init__(self, verbose=0):
        super().__init__(verbose)
        self.current_phase = "STAGE_A"
        self.diagnostics_path = f"{LOG_DIR}/v3_diagnostics.txt"
        
    def _on_step(self) -> bool:
        steps = self.num_timesteps
        
        # Determine Phase
        new_phase = self.current_phase
        if steps > PHASES["STAGE_B"]["end_step"]:
            new_phase = "STAGE_C"
        elif steps > PHASES["STAGE_A"]["end_step"]:
            new_phase = "STAGE_B"
            
        if new_phase != self.current_phase:
            print(f"\n[PHASE TRANSITION] {self.current_phase} -> {new_phase} at {steps} steps")
            self.current_phase = new_phase
            self.update_env_config()
            
        # Audit every 50k steps
        if steps == 1 or steps % 50000 == 0:
            self.run_audit()
            
        return True

    def update_env_config(self):
        """Update training environment with current phase parameters."""
        cfg = PHASES[self.current_phase]
        
        # SB3 training_env is a VecEnv.
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
        
        # Correctly set the attribute on the vectorized environment
        self.training_env.set_attr("rl_config", new_rl_config)
        
        # Optional: Close and let it reopen on next reset if needed, 
        # but GrpcTradingEnv applies rl_config on every reset anyway.
        # self.training_env.env_method("close") 

    def run_audit(self):
        steps = self.num_timesteps
        cfg = PHASES[self.current_phase]
        print(f"\n[{RUN_ID}] Auditing {self.current_phase} at {steps} steps...")
        
        # Use a fresh env for audit with CURRENT phase config
        env = GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id=EVAL_DATASET_ID,
            symbol="BTCUSDT",
            maker_fee=COSTS["maker"],
            taker_fee=COSTS["taker"],
            slip_bps=COSTS["slip"],
            fill_model=cfg["fill_model"],
            reward_tib_bonus_bps=0.0 # Audit PnL should NOT include TiB bonus
        )
        
        obs, info = env.reset()
        initial_equity = info.get("equity", 10000.0)
        
        det_actions = []
        metrics = {"maker_fills": 0, "toxic_fills": 0, "trades": 0}
        equities = [initial_equity]

        for _ in range(2000):
            act, _ = self.model.predict(obs, deterministic=True)
            det_actions.append(int(act))
            obs, reward, terminated, truncated, info = env.step(int(act))
            
            metrics["maker_fills"] += info.get("maker_fills", 0)
            metrics["toxic_fills"] += info.get("toxic_fills", 0)
            metrics["trades"] += info.get("trades_executed", 0)
            
            if "equity" in info: equities.append(info["equity"])
            if terminated or truncated: break
            
        env.close()
        pnl = (equities[-1] / initial_equity - 1) * 100
        
        def get_dist(acts):
            counts = {i: acts.count(i) for i in range(13)}
            return {ACTION_LABELS[i]: counts[i] / max(1, len(acts)) for i in range(13)}

        det_dist = get_dist(det_actions)
        dominant = max(det_dist, key=det_dist.get)
        
        print(f"Audit: PnL={pnl:.2f}% | Fills={metrics['maker_fills']} | Dominant={dominant}")
        
        # Save Report
        with open(f"{LOG_DIR}/audit_{steps}.json", "w") as f:
            json.dump({
                "step": steps,
                "phase": self.current_phase,
                "pnl": pnl,
                "metrics": metrics,
                "det_dist": det_dist,
                "final_equity": equities[-1]
            }, f, indent=2)

def train():
    # Initial Env (Stage A)
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

    print(f"[{RUN_ID}] Starting Maker Curriculum V3...")
    try:
        model.learn(
            total_timesteps=1000000,
            callback=MakerCurriculumCallback(),
            progress_bar=True
        )
        model.save(f"{LOG_DIR}/v3_model_final")
    except Exception as e:
        print(f"\n[TRAINING INTERRUPTED] {e}")
    finally:
        env.close()

if __name__ == "__main__":
    train()
