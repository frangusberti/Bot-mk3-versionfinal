"""Diagnostic eval v2: random_start + mark-to-market at done."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv
from ppo_masking_pilot import CONFIG, mask_fn

# Override: enable random start for eval diversity
EVAL_CONFIG = {**CONFIG, "random_start_offset": True}

out_dir = "python/runs_train/masking_pilot"
model_path = os.path.join(out_dir, "model_masking.zip")
venv_path = os.path.join(out_dir, "venv_masking.pkl")

INITIAL_EQUITY = 10000.0

def make_eval_env():
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_eval",
        symbol="BTCUSDT",
        **EVAL_CONFIG
    )
    return ActionMasker(env, mask_fn)

eval_venv = DummyVecEnv([make_eval_env])
eval_venv = VecNormalize.load(venv_path, eval_venv)
eval_venv.training = False
eval_venv.norm_reward = False

model = MaskablePPO.load(model_path, env=eval_venv, device="cpu")

print("[DIAG-v2] Running 10k steps: random_start=True + mark-to-market...")
obs = eval_venv.reset()

episodes = []
ep_steps = 0
ep_trades = 0
ep_maker_fills = 0
ep_last_reason = ""
ep_last_pos_qty = 0.0
ep_last_pos_side = "FLAT"
ep_last_rpnl = 0.0
ep_last_fees = 0.0
ep_last_equity = INITIAL_EQUITY

total_trades_all = 0
total_maker_fills_all = 0

for i in range(10000):
    masks = eval_venv.env_method("action_masks")
    action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
    obs, reward, done, info = eval_venv.step(action)
    info0 = info[0]
    ep_steps += 1
    ep_trades += info0.get("trades_executed", 0)
    ep_maker_fills += info0.get("resting_fill_count", 0)
    ep_last_reason = info0.get("reason", "")
    ep_last_pos_qty = info0.get("position_qty", 0.0)
    ep_last_pos_side = info0.get("position_side", "FLAT")
    ep_last_rpnl = info0.get("realized_pnl", 0.0)
    ep_last_fees = info0.get("fees_paid", 0.0)
    ep_last_equity = info0.get("equity", INITIAL_EQUITY)

    if done[0]:
        # Mark-to-market: terminal PnL = equity_final - initial
        # equity already includes unrealized PnL
        terminal_pnl = ep_last_equity - INITIAL_EQUITY
        net_pnl_after_fees = terminal_pnl  # equity is already net of fees

        episodes.append({
            "steps": ep_steps,
            "trades": ep_trades,
            "maker_fills": ep_maker_fills,
            "reason": ep_last_reason,
            "pos_qty_at_done": round(ep_last_pos_qty, 6),
            "pos_side_at_done": ep_last_pos_side,
            "realized_pnl": round(ep_last_rpnl, 4),
            "fees_paid": round(ep_last_fees, 4),
            "equity_final": round(ep_last_equity, 4),
            "terminal_pnl_mtm": round(terminal_pnl, 4),
            "net_pnl_after_fees": round(net_pnl_after_fees, 4),
        })
        total_trades_all += ep_trades
        total_maker_fills_all += ep_maker_fills
        ep_steps = 0
        ep_trades = 0
        ep_maker_fills = 0

# Summary
n = len(episodes)
if n == 0:
    print("[DIAG] No episodes completed!")
else:
    lengths = [e["steps"] for e in episodes]
    lengths_sorted = sorted(lengths)
    median_len = lengths_sorted[len(lengths_sorted)//2]
    avg_len = sum(lengths) / n

    reasons = {}
    pos_open_at_done = 0
    for e in episodes:
        r = e["reason"]
        reasons[r] = reasons.get(r, 0) + 1
        if abs(e["pos_qty_at_done"]) > 1e-9:
            pos_open_at_done += 1

    total_terminal_pnl = sum(e["terminal_pnl_mtm"] for e in episodes)
    total_rpnl = sum(e["realized_pnl"] for e in episodes)
    total_fees = sum(e["fees_paid"] for e in episodes)
    avg_terminal_per_ep = total_terminal_pnl / n

    print(f"\n{'='*55}")
    print(f"EPISODE DIAGNOSTIC v2 (random_start + MTM)")
    print(f"{'='*55}")
    print(f"Total episodes:           {n}")
    print(f"Avg length:               {avg_len:.1f} steps")
    print(f"Median length:            {median_len} steps")
    print(f"Min / Max:                {min(lengths)} / {max(lengths)}")

    print(f"\n--- Done Reasons ---")
    for r, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {r}: {cnt} ({cnt/n*100:.1f}%)")

    print(f"\n--- Position at Done ---")
    print(f"  With open position:     {pos_open_at_done} ({pos_open_at_done/n*100:.1f}%)")
    print(f"  Flat at done:           {n - pos_open_at_done} ({(n-pos_open_at_done)/n*100:.1f}%)")

    print(f"\n--- PnL (Mark-to-Market) ---")
    print(f"  Total terminal PnL:     ${total_terminal_pnl:.2f}")
    print(f"  Avg terminal PnL/ep:    ${avg_terminal_per_ep:.2f}")
    print(f"  Total realized PnL:     ${total_rpnl:.2f}")
    print(f"  Total fees paid:        ${total_fees:.2f}")
    print(f"  Net PnL after fees:     ${total_terminal_pnl:.2f}")

    print(f"\n--- Activity ---")
    print(f"  Total trades:           {total_trades_all}")
    print(f"  Total maker fills:      {total_maker_fills_all}")
    print(f"{'='*55}")

    # First 5 episodes
    print("\n--- First 5 episodes ---")
    for i, e in enumerate(episodes[:5]):
        print(f"  Ep{i}: len={e['steps']}, trades={e['trades']}, reason={e['reason']}, "
              f"pos={e['pos_qty_at_done']:.4f}, mtm=${e['terminal_pnl_mtm']:.2f}, "
              f"rpnl=${e['realized_pnl']:.2f}, fees=${e['fees_paid']:.2f}")

    with open(os.path.join(out_dir, "diag_v2.json"), "w") as f:
        json.dump({"summary": {
            "total_episodes": n, "avg_len": avg_len, "median_len": median_len,
            "total_terminal_pnl": total_terminal_pnl, "total_rpnl": total_rpnl,
            "total_fees": total_fees, "total_trades": total_trades_all,
            "total_maker_fills": total_maker_fills_all,
            "pos_open_at_done": pos_open_at_done, "reasons": reasons,
        }, "episodes": episodes}, f, indent=2)
