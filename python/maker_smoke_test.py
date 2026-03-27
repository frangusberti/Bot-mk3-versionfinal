"""
maker_smoke_test.py — Validate the new Maker-First action space and rewards.

Performs a 50k step training run and a detailed evaluation audit.
"""

import os
import sys
import psutil
import time
import json
import uuid
import torch
import numpy as np
import pandas as pd
from datetime import datetime

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
import bot_pb2
from grpc_env import GrpcTradingEnv

# --- CONFIGURATION ---
RUN_ID = f"maker_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
DATASET_ID = "stage2_train"
EVAL_DATASET_ID = "stage2_eval"
LOG_DIR = f"python/runs_train/maker_smoke/{RUN_ID}"
os.makedirs(LOG_DIR, exist_ok=True)

ACTION_LABELS = [
    "HOLD", "POST_BID", "JOIN_BID", "POST_ASK", "JOIN_ASK", "CANCEL_ALL", "TAKER_EXIT",
    "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL", "REDUCE_25", "REDUCE_50", "REDUCE_100",
]

# Real World Costs for Smoke Test
COSTS = {
    "maker": 2.0,  # 2.0 bps
    "taker": 5.0,  # 5.0 bps
    "slip": 1.0,   # 1 bps
}

HYPERPARAMS = {
    "learning_rate": 2e-4,
    "ent_coef": 0.05, # Higher entropy for exploration in smoke test
    "batch_size": 256,
    "n_steps": 2048,
}

# --- SYSTEM SETTINGS ---
p = psutil.Process(os.getpid())
if sys.platform == 'win32':
    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
else:
    p.nice(10)

# --- AUDIT CALLBACK ---

class MakerAuditCallback(BaseCallback):
    def __init__(self, verbose=0):
        super().__init__(verbose)
        self.best_mean_reward = -np.inf

    def _on_step(self) -> bool:
        if self.num_timesteps % 10000 == 0 or self.num_timesteps == 1:
            self.run_audit()
        return True

    def run_audit(self):
        steps = self.num_timesteps
        print(f"\n[{RUN_ID}] Running Audit at {steps} steps...")
        
        env = GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id=EVAL_DATASET_ID,
            symbol="BTCUSDT",
            maker_fee=COSTS["maker"],
            taker_fee=COSTS["taker"],
            slip_bps=COSTS["slip"],
            fill_model=bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
        )
        
        obs, info = env.reset()
        initial_equity = info.get("equity", -1.0)
        
        if abs(initial_equity - 10000.0) > 5.0:
            print(f"[AUDIT_BUG] initial_equity={initial_equity:.2f} (expected ~10000). Env state may be stale!")
        else:
            print(f"[AUDIT_OK] initial_equity={initial_equity:.2f} [OK]")

        done = False
        det_actions = []
        rewards = []
        equities = [initial_equity]
        
        # Extended Info Metrics
        cumulative_trades = 0
        cumulative_maker_fills = 0
        cumulative_toxic_fills = 0
        cumulative_stale_expiries = 0
        
        for i in range(2000): # 2000 steps for smoke audit
            det_act, _ = self.model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(int(det_act))
            
            det_actions.append(int(det_act))
            rewards.append(reward)
            cumulative_trades += info.get("trades_executed", 0)
            cumulative_maker_fills += info.get("maker_fills", 0)
            cumulative_toxic_fills += info.get("toxic_fills", 0)
            cumulative_stale_expiries += info.get("stale_expiries", 0)
            
            if "equity" in info:
                equities.append(info["equity"])
                if i % 10 == 0:
                    pos = info.get("position_qty", 0)
                    pnl = info.get("realized_pnl", 0)
                    fees = info.get("fees_paid", 0)
                    print(f"  [DEBUG] Step {i}: act={int(det_act)} eq={info['equity']:.2f} pos={pos:.3f} pnl={pnl:.2f} fees={fees:.2f} trades={cumulative_trades}")
            
            if terminated or truncated:
                print(f"  [DEBUG] Terminated at step {i} | reason: {info.get('reason', 'UNKNOWN')}")
                break
        
        env.close()
        
        # --- Report requested metrics ---
        total_acts = len(det_actions) if len(det_actions) > 0 else 1
        action_counts = {i: det_actions.count(i) for i in range(13)}
        dist = {ACTION_LABELS[i]: action_counts[i] / total_acts for i in range(13)}
        
        hold_rate = dist["HOLD"] * 100
        post_bid = dist["POST_BID"] * 100
        join_bid = dist["JOIN_BID"] * 100
        post_ask = dist["POST_ASK"] * 100
        join_ask = dist["JOIN_ASK"] * 100
        cancel_all = dist["CANCEL_ALL"] * 100
        taker_exit = dist["TAKER_EXIT"] * 100
        
        pnl = (equities[-1] / initial_equity - 1) * 100
        
        print(f"--- Smoke Test Audit ({steps} steps) ---")
        print(f"PnL: {pnl:.2f}% | Trades: {cumulative_trades} | HOLD: {hold_rate:.1f}%")
        print(f"Maker Fills: {cumulative_maker_fills} | Toxic Fills: {cumulative_toxic_fills} | Stale: {cumulative_stale_expiries}")
        print(f"Maker Usage: BID(Post/Join): {post_bid:.1f}/{join_bid:.1f}% | ASK(Post/Join): {post_ask:.1f}/{join_ask:.1f}%")
        print(f"Control Usage: CANCEL_ALL: {cancel_all:.1f}% | TAKER_EXIT: {taker_exit:.1f}%")
        
        # Save results
        report = {
            "step": steps,
            "pnl": pnl,
            "trades": cumulative_trades,
            "maker_fills": cumulative_maker_fills,
            "toxic_fills": cumulative_toxic_fills,
            "stale_expiries": cumulative_stale_expiries,
            "hold_rate": hold_rate,
            "action_dist": dist,
            "mean_reward": np.mean(rewards),
            "std_reward": np.std(rewards),
            "final_equity": equities[-1]
        }
        
        with open(f"{LOG_DIR}/audit_{steps}.json", "w") as f:
            json.dump(report, f, indent=2)

def train():
    env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id=DATASET_ID, 
        symbol="BTCUSDT",
        maker_fee=COSTS["maker"],
        taker_fee=COSTS["taker"],
        slip_bps=COSTS["slip"],
        fill_model=bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
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

    print(f"[{RUN_ID}] Starting Maker Smoke Test (50k steps)...")
    try:
        model.learn(
            total_timesteps=50000,
            callback=MakerAuditCallback(),
            progress_bar=True
        )
        model.save(f"{LOG_DIR}/smoke_model")
    except Exception as e:
        print(f"\n[ERROR] {e}")
    finally:
        env.close()

if __name__ == "__main__":
    train()
