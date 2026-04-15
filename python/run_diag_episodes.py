"""Diagnostic eval: per-episode stats to understand short episodes + PnL=0."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv
from ppo_masking_pilot import CONFIG, mask_fn

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

print("[DIAG] Running 10k steps with per-episode tracking...")
obs = eval_venv.reset()

episodes = []
ep_steps = 0
ep_trades = 0
ep_last_reason = ""
ep_last_pos_qty = 0.0
ep_last_pos_side = "FLAT"
ep_last_rpnl = 0.0
ep_last_fees = 0.0
ep_last_equity = 10000.0

for i in range(10000):
    masks = eval_venv.env_method("action_masks")
    action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
    obs, reward, done, info = eval_venv.step(action)
    info0 = info[0]
    ep_steps += 1
    ep_trades += info0.get("trades_executed", 0)
    ep_last_reason = info0.get("reason", "")
    ep_last_pos_qty = info0.get("position_qty", 0.0)
    ep_last_pos_side = info0.get("position_side", "FLAT")
    ep_last_rpnl = info0.get("realized_pnl", 0.0)
    ep_last_fees = info0.get("fees_paid", 0.0)
    ep_last_equity = info0.get("equity", 10000.0)

    if done[0]:
        episodes.append({
            "steps": ep_steps,
            "trades": ep_trades,
            "reason": ep_last_reason,
            "pos_qty_at_done": ep_last_pos_qty,
            "pos_side_at_done": ep_last_pos_side,
            "realized_pnl": round(ep_last_rpnl, 6),
            "fees_paid": round(ep_last_fees, 6),
            "equity_final": round(ep_last_equity, 4),
        })
        ep_steps = 0
        ep_trades = 0

# Summary
n = len(episodes)
if n == 0:
    print("[DIAG] No episodes completed in 10k steps!")
else:
    lengths = [e["steps"] for e in episodes]
    lengths_sorted = sorted(lengths)
    median_len = lengths_sorted[len(lengths_sorted)//2]
    avg_len = sum(lengths) / n

    reasons = {}
    pos_open_at_done = 0
    has_rpnl = 0
    for e in episodes:
        r = e["reason"]
        reasons[r] = reasons.get(r, 0) + 1
        if abs(e["pos_qty_at_done"]) > 1e-9:
            pos_open_at_done += 1
        if abs(e["realized_pnl"]) > 1e-9:
            has_rpnl += 1

    print(f"\n{'='*50}")
    print(f"EPISODE DIAGNOSTIC (10k steps)")
    print(f"{'='*50}")
    print(f"Total episodes:          {n}")
    print(f"Avg length:              {avg_len:.1f} steps")
    print(f"Median length:           {median_len} steps")
    print(f"Min / Max:               {min(lengths)} / {max(lengths)}")
    print(f"\n--- Done Reasons ---")
    for r, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {r}: {cnt} ({cnt/n*100:.1f}%)")
    print(f"\n--- Position at Done ---")
    print(f"  With open position:    {pos_open_at_done} ({pos_open_at_done/n*100:.1f}%)")
    print(f"  Flat at done:          {n - pos_open_at_done} ({(n-pos_open_at_done)/n*100:.1f}%)")
    print(f"\n--- PnL ---")
    print(f"  Episodes with rpnl!=0: {has_rpnl}")
    print(f"  Episodes with rpnl=0:  {n - has_rpnl}")
    print(f"{'='*50}")

    # Show first 5 episodes for detail
    print("\n--- First 5 episodes detail ---")
    for i, e in enumerate(episodes[:5]):
        print(f"  Ep{i}: steps={e['steps']}, trades={e['trades']}, reason={e['reason']}, "
              f"pos_qty={e['pos_qty_at_done']:.6f}, rpnl={e['realized_pnl']:.6f}, "
              f"fees={e['fees_paid']:.6f}, equity={e['equity_final']:.2f}")

    with open(os.path.join(out_dir, "diag_episodes.json"), "w") as f:
        json.dump(episodes, f, indent=2)
    print(f"\nSaved {n} episode records to diag_episodes.json")
