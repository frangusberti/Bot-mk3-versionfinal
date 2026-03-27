"""
stage3_curriculum_retrain.py v2 -- High-Precision Curriculum Retrain.

Implements a 3-phase cost curriculum to break the 100% HOLD trap.
- Phase 1: 0-500k | Cost ~0.4 bps (Discovery)
- Phase 2: 500k-1M | Cost ~2.5 bps (Transition)
- Phase 3: 1M-2M   | Cost ~11.0 bps (Real Reality)

Features:
- Run ID versioning & metadata logging.
- Dual-Evaluation: Current Phase vs Real Cost (11bps).
- Multi-metric reporting: Hold rate, distributions, entropy, trades.
- Automated flagging: Churn, Persistence, Death-under-real.
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
from grpc_env import GrpcTradingEnv

# --- CONFIGURATION & VERSIONING ---
RUN_ID = f"curriculum_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
DATASET_ID = "stage2_train"
EVAL_DATASET_ID = "stage2_eval"
LOG_DIR = f"python/runs_train/stage3/{RUN_ID}"
os.makedirs(LOG_DIR, exist_ok=True)

METADATA = {
    "run_id": RUN_ID,
    "curriculum_enabled": True,
    "dataset_id": DATASET_ID,
    "eval_dataset_id": EVAL_DATASET_ID,
    "phases": {
        "1": {"range": [0, 500000], "costs": {"maker": 0.1, "taker": 0.2, "slip": 0.0}},
        "2": {"range": [500000, 1000000], "costs": {"maker": 0.5, "taker": 1.0, "slip": 0.5}},
        "3": {"range": [1000000, 2000000], "costs": {"maker": 2.0, "taker": 5.0, "slip": 1.0}},
    },
    "fixed_hyperparams": {
        "learning_rate": 1e-4,
        "ent_coef": 0.02,
        "batch_size": 128,
        "n_steps": 2048,
    }
}

# --- SYSTEM SETTINGS ---
p = psutil.Process(os.getpid())
p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
avail_cores = list(range(psutil.cpu_count()))
if len(avail_cores) > 2:
    p.cpu_affinity(avail_cores[:-2])
    print(f"[{RUN_ID}] Low priority set. Affinity restricted to {len(avail_cores)-2} cores.")

# --- CALLBACKS ---

class CurriculumV2Callback(BaseCallback):
    """Orchestrates phases, dual-evaluation, and flagging metrics."""
    def __init__(self, metadata, verbose=0):
        super().__init__(verbose)
        self.meta = metadata
        self.current_phase = 1
        self.progression_log = []
        self.eval_freq = 500000 # Checkpoints at 500k, 1M, 2M (plus starts)
        self.last_eval_step = 0
        
        # Initial save of metadata
        with open(f"{LOG_DIR}/metadata.json", "w") as f:
            json.dump(self.meta, f, indent=2)

    def _on_step(self) -> bool:
        # 1. Phase Control
        total_steps = self.num_timesteps
        if total_steps >= 1000000 and self.current_phase < 3:
            self._switch_phase(3)
        elif total_steps >= 500000 and self.current_phase < 2:
            self._switch_phase(2)

        # 2. Checkpoint / Evaluation
        if total_steps >= self.last_eval_step + self.eval_freq or total_steps == 1:
            self.run_dual_evaluation()
            self.last_eval_step = total_steps
            # Save progress periodically
            with open(f"{LOG_DIR}/progression.json", "w") as f:
                json.dump(self.progression_log, f, indent=2)
            # Save model checkpoint
            self.model.save(f"{LOG_DIR}/model_{total_steps}")
            self.model.save("python/runs_train/stage3/latest_model.zip")
            
        return True

    def _switch_phase(self, new_phase):
        print(f"\n[{RUN_ID}] >>> CURRICULUM SWITCH: Phase {self.current_phase} -> Phase {new_phase} at {self.num_timesteps} steps")
        self.current_phase = new_phase
        costs = self.meta["phases"][str(new_phase)]["costs"]
        
        env = self.training_env.envs[0].unwrapped
        env.rl_config.maker_fee = costs["maker"]
        env.rl_config.taker_fee = costs["taker"]
        env.rl_config.slip_bps = costs["slip"]
        print(f"[{RUN_ID}] Env Updated: Maker={costs['maker']}, Taker={costs['taker']}, Slip={costs['slip']}")

    def run_dual_evaluation(self):
        steps = self.num_timesteps
        print(f"\n[{RUN_ID}] Running Dual-Evaluation at {steps} steps...")
        
        # A. Eval under Current Phase Costs
        phase_costs = self.meta["phases"][str(self.current_phase)]["costs"]
        res_current = self._evaluate_model("current_phase", phase_costs)
        
        # B. Eval under Real Reality Costs (11 bps)
        real_costs = self.meta["phases"]["3"]["costs"]
        res_real = self._evaluate_model("real_world", real_costs)
        
        entry = {
            "step": steps,
            "phase": self.current_phase,
            "eval_current": res_current,
            "eval_real": res_real
        }
        self.progression_log.append(entry)
        
        # C. Flagging Logic
        self._check_flags(res_current, res_real)

    def _evaluate_model(self, label, costs):
        # Create temporary eval env
        env = GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id=EVAL_DATASET_ID,
            symbol="BTCUSDT",
            maker_fee=costs["maker"],
            taker_fee=costs["taker"],
            slip_bps=costs["slip"]
        )
        
        obs, info = env.reset()
        initial_equity = info.get("equity", 10000.0)
        done = False
        det_actions = []
        sto_actions = []
        rewards = []
        equities = [initial_equity]
        entropies = []
        cumulative_trades = 0
        
        # Run 3000 steps (full eval window)
        for _ in range(3000):
            obs_tensor = torch.as_tensor(obs).unsqueeze(0).to(self.model.policy.device)
            with torch.no_grad():
                dist = self.model.policy.get_distribution(obs_tensor)
                ent = dist.entropy().cpu().numpy()[0]
                sto_act = dist.sample().cpu().numpy()[0]
            
            det_act, _ = self.model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(int(det_act))
            
            det_actions.append(int(det_act))
            sto_actions.append(int(sto_act))
            rewards.append(reward)
            entropies.append(ent)
            cumulative_trades += info.get("trades_executed", 0)
            
            if "equity" in info:
                equities.append(info["equity"])
            
            if terminated or truncated:
                break
        
        env.close()
        
        # Calculate stats
        action_counts = {}
        for a in det_actions:
            action_counts[a] = action_counts.get(a, 0) + 1
        
        total_actions = len(det_actions) if len(det_actions) > 0 else 1
        hold_rate = (action_counts.get(0, 0) / total_actions) * 100
        
        final_equity = equities[-1]
        pnl_pct = ((final_equity / initial_equity) - 1) * 100
        
        print(f"  [{label}] HOLD: {hold_rate:6.2f}% | Trades: {cumulative_trades:4} | PnL: {pnl_pct:6.2f}% | FinalEq: {final_equity:.2f}")
        
        return {
            "hold_rate": float(hold_rate),
            "trades": int(cumulative_trades),
            "det_dist": {str(k): float(v/total_actions) for k, v in action_counts.items()},
            "entropy": float(np.mean(entropies)),
            "mean_reward": float(np.mean(rewards)),
            "equity_final": float(final_equity),
            "initial_equity": float(initial_equity)
        }

    def _check_flags(self, res_cur, res_real):
        steps = self.num_timesteps
        
        # 1. Pure Churn (P1)
        if self.current_phase == 1 and res_cur["trades"] > 500 and res_cur["equity_final"] < 9500:
             print(f"[!] FLAG: Phase 1 CHURN detected (High trades/Negative return).")
             
        # 2. Persistence Trap
        if steps >= 500000 and res_cur["hold_rate"] > 99.9:
             print(f"[!] FLAG: Persistence Trap. 100% HOLD even under reduced costs.")
             
        # 3. Death under Real
        if res_cur["equity_final"] > 10000.0 and res_real["equity_final"] < 9800:
             print(f"[!] FLAG: Catastrophic Performance Divergence (Real costs kill Phase gains).")

def train():
    # 1. Determine if we are resuming
    # Use a fixed latest_model.zip for easy resumption
    checkpoint_p = f"python/runs_train/stage3/latest_model.zip"
    
    # Phase 1 Initial Env (Cost logic handles phase switching in callback)
    start_costs = METADATA["phases"]["1"]["costs"]
    env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id=DATASET_ID, 
        symbol="BTCUSDT",
        maker_fee=start_costs["maker"],
        taker_fee=start_costs["taker"],
        slip_bps=start_costs["slip"]
    )

    if os.path.exists(checkpoint_p):
        print(f"[{RUN_ID}] Resuming from checkpoint: {checkpoint_p}")
        model = PPO.load(checkpoint_p, env=env)
        # Ensure correct hyperparams are maintained
        model.ent_coef = METADATA["fixed_hyperparams"]["ent_coef"]
        model.learning_rate = METADATA["fixed_hyperparams"]["learning_rate"]
    else:
        print(f"[{RUN_ID}] Starting fresh training...")
        model = PPO(
            "MlpPolicy",
            env,
            verbose=1,
            learning_rate=METADATA["fixed_hyperparams"]["learning_rate"],
            n_steps=METADATA["fixed_hyperparams"]["n_steps"],
            batch_size=METADATA["fixed_hyperparams"]["batch_size"],
            n_epochs=10,
            gamma=0.99,
            ent_coef=METADATA["fixed_hyperparams"]["ent_coef"],
            tensorboard_log=LOG_DIR,
            device="cpu"
        )

    print(f"[{RUN_ID}] Starting Stage 3 Curriculum Retrain (2M steps total)...")
    try:
        model.learn(
            total_timesteps=2000000,
            callback=CurriculumV2Callback(METADATA),
            progress_bar=True,
            reset_num_timesteps=False # CRITICAL for resuming
        )
        model.save(f"{LOG_DIR}/final_model")
    except Exception as e:
        print(f"\n[CRITICAL ERROR] Training failed: {e}")
    finally:
        env.close()

if __name__ == "__main__":
    train()
