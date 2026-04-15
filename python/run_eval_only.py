"""Quick eval-only run using saved masking pilot model."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv
from ppo_masking_pilot import CONFIG, mask_fn, run_masked_eval, print_scorecard

out_dir = "python/runs_train/masking_pilot"
model_path = os.path.join(out_dir, "model_masking.zip")
venv_path = os.path.join(out_dir, "venv_masking.pkl")

def make_eval_env():
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_eval",
        symbol="BTCUSDT",
        **CONFIG
    )
    return ActionMasker(env, mask_fn)

eval_venv = DummyVecEnv([make_eval_env])
eval_venv = VecNormalize.load(venv_path, eval_venv)
eval_venv.training = False
eval_venv.norm_reward = False

model = MaskablePPO.load(model_path, env=eval_venv, device="cpu")

print("[EVAL-ONLY] Running 10k steps...")
report = run_masked_eval(model, eval_venv, steps=10000)

with open(os.path.join(out_dir, "report_masking_pilot_extended.json"), "w") as f:
    json.dump(report, f, indent=2)

print_scorecard(report)

# Print extended metrics
print("\n--- EXTENDED METRICS ---")
for k in ["net_pnl_after_fees", "realized_pnl_total", "realized_pnl_cumulative",
           "fees_paid_total", "equity_final", "avg_pnl_per_trade",
           "avg_win_hold_ms", "avg_loss_hold_ms", "close_with_pos",
           "close_flat", "thesis_decay_total"]:
    print(f"  {k}: {report.get(k, 'N/A')}")
