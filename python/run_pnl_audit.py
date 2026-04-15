"""
PnL Audit: causal analysis of negative PnL using 150k checkpoint.
Tracks roundtrips, spread capture, adverse selection, and exit types.
"""
import os, sys, json, numpy as np
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv
from ppo_masking_v3 import EVAL_CONFIG, mask_fn

INITIAL_EQUITY = 10000.0
out_dir = "python/runs_train/masking_v3_checkpoints"
model_path = os.path.join(out_dir, "model_150k.zip")
venv_path = os.path.join(out_dir, "venv_150k.pkl")

def make_env():
    env = GrpcTradingEnv(server_addr="localhost:50051",
                         dataset_id="stage2_eval", symbol="BTCUSDT", **EVAL_CONFIG)
    return ActionMasker(env, mask_fn)

ev = DummyVecEnv([make_env])
ev = VecNormalize.load(venv_path, ev)
ev.training = False; ev.norm_reward = False
model = MaskablePPO.load(model_path, env=ev, device="cpu")

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
]

print("[AUDIT] Running 10k steps with per-trade tracking...")
obs = ev.reset()

# Position tracking for roundtrip reconstruction
class PositionTracker:
    def __init__(self):
        self.is_open = False
        self.side = None        # "LONG" or "SHORT"
        self.entry_price = 0.0
        self.entry_mid = 0.0
        self.entry_ts = 0
        self.entry_qty = 0.0
        self.entry_fees = 0.0
        self.mids_after_entry = []  # for adverse selection
        self.roundtrips = []
        self.terminal_losses = []  # positions killed by DD

    def open_pos(self, side, price, qty, mid, ts, fee):
        self.is_open = True
        self.side = side
        self.entry_price = price
        self.entry_mid = mid
        self.entry_ts = ts
        self.entry_qty = qty
        self.entry_fees = fee
        self.mids_after_entry = []

    def tick_mid(self, mid):
        if self.is_open:
            self.mids_after_entry.append(mid)

    def close_pos(self, exit_price, exit_mid, exit_ts, exit_fee, exit_type):
        if not self.is_open:
            return
        hold_ms = exit_ts - self.entry_ts if exit_ts > 0 and self.entry_ts > 0 else 0
        if self.side == "LONG":
            gross_pnl_bps = (exit_price - self.entry_price) / self.entry_price * 10000
            spread_capture_bps = (self.entry_mid - self.entry_price) / self.entry_mid * 10000
        else:
            gross_pnl_bps = (self.entry_price - exit_price) / self.entry_price * 10000
            spread_capture_bps = (self.entry_price - self.entry_mid) / self.entry_mid * 10000

        total_fees = self.entry_fees + exit_fee
        fee_bps = total_fees / (self.entry_price * self.entry_qty) * 10000 if self.entry_qty > 0 else 0

        # Adverse selection: mid move against us after entry
        as_1s = as_3s = as_5s = 0.0
        if self.mids_after_entry:
            for delay_steps, label in [(1, "1s"), (3, "3s"), (5, "5s")]:
                if len(self.mids_after_entry) > delay_steps:
                    future_mid = self.mids_after_entry[delay_steps]
                    if self.side == "LONG":
                        move = (future_mid - self.entry_mid) / self.entry_mid * 10000
                    else:
                        move = (self.entry_mid - future_mid) / self.entry_mid * 10000
                    if label == "1s": as_1s = move
                    elif label == "3s": as_3s = move
                    elif label == "5s": as_5s = move

        rt = {
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": exit_price,
            "gross_pnl_bps": round(gross_pnl_bps, 2),
            "net_pnl_bps": round(gross_pnl_bps - fee_bps, 2),
            "spread_capture_bps": round(spread_capture_bps, 2),
            "fee_bps": round(fee_bps, 2),
            "hold_ms": hold_ms,
            "exit_type": exit_type,
            "as_1s": round(as_1s, 2),
            "as_3s": round(as_3s, 2),
            "as_5s": round(as_5s, 2),
            "qty": self.entry_qty,
            "total_fees_usd": round(total_fees, 4),
            "gross_pnl_usd": round((exit_price - self.entry_price) * self.entry_qty * (1 if self.side == "LONG" else -1), 4),
        }
        self.roundtrips.append(rt)
        self.is_open = False
        self.side = None

    def force_close_mtm(self, mid, ts, exit_type="DD_TERMINAL"):
        """Mark-to-market close for terminal episodes."""
        if self.is_open:
            self.close_pos(mid, mid, ts, 0.0, exit_type)

tracker = PositionTracker()
ep_count = 0
total_steps = 0

for _ in range(10000):
    masks = ev.env_method("action_masks")
    action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
    act_int = int(action[0])
    obs, reward, done, info = ev.step(action)
    i0 = info[0]
    total_steps += 1

    mid = i0.get("mid_price", 0.0)
    ts = i0.get("ts", 0)
    pos_qty = i0.get("position_qty", 0.0)
    pos_side = i0.get("position_side", "FLAT")
    fills = i0.get("fills", [])

    # Track mid for adverse selection
    tracker.tick_mid(mid)

    # Detect position opens via fills
    for f in fills:
        f_side = f.get("side", "")
        f_price = f.get("price", 0.0)
        f_qty = f.get("qty", 0.0)
        f_fee = f.get("fee", 0.0)

        if act_int in (1, 2):  # OPEN_LONG, ADD_LONG
            if not tracker.is_open:
                tracker.open_pos("LONG", f_price, f_qty, mid, ts, f_fee)
        elif act_int in (5, 6):  # OPEN_SHORT, ADD_SHORT
            if not tracker.is_open:
                tracker.open_pos("SHORT", f_price, f_qty, mid, ts, f_fee)
        elif act_int in (3, 4):  # REDUCE_LONG, CLOSE_LONG
            if tracker.is_open:
                tracker.close_pos(f_price, mid, ts, f_fee, "CLOSE_NORMAL")
        elif act_int in (7, 8):  # REDUCE_SHORT, CLOSE_SHORT
            if tracker.is_open:
                tracker.close_pos(f_price, mid, ts, f_fee, "CLOSE_NORMAL")

    # Episode done: force MTM close
    if done[0]:
        reason = i0.get("reason", "UNKNOWN")
        if tracker.is_open:
            tracker.force_close_mtm(mid, ts, exit_type=reason)
        ep_count += 1

ev.close()

# === ANALYSIS ===
rts = tracker.roundtrips
n_rt = len(rts)
print(f"\n{'='*60}")
print(f"PnL AUDIT — 150k checkpoint, {ep_count} episodes, {n_rt} roundtrips")
print(f"{'='*60}")

if n_rt == 0:
    print("No roundtrips detected!")
    sys.exit(0)

# 1) PnL by side
long_rts = [r for r in rts if r["side"] == "LONG"]
short_rts = [r for r in rts if r["side"] == "SHORT"]
print(f"\n--- 1) PnL by Side ---")
for label, subset in [("LONG", long_rts), ("SHORT", short_rts)]:
    if not subset:
        print(f"  {label}: no trades")
        continue
    total_gross = sum(r["gross_pnl_usd"] for r in subset)
    total_fees = sum(r["total_fees_usd"] for r in subset)
    print(f"  {label}: {len(subset)} trades, gross=${total_gross:.2f}, fees=${total_fees:.2f}, net=${total_gross-total_fees:.2f}")

# 2) PnL by exit type
print(f"\n--- 2) PnL by Exit Type ---")
exit_types = defaultdict(list)
for r in rts:
    exit_types[r["exit_type"]].append(r)
for et, subset in sorted(exit_types.items()):
    total = sum(r["gross_pnl_usd"] for r in subset)
    fees = sum(r["total_fees_usd"] for r in subset)
    print(f"  {et}: {len(subset)} trades, gross=${total:.2f}, fees=${fees:.2f}, net=${total-fees:.2f}")

# 3) Execution metrics
print(f"\n--- 3) Execution Metrics ---")
spreads = [r["spread_capture_bps"] for r in rts]
fees_per_trade = [r["fee_bps"] for r in rts]
total_fees_usd = sum(r["total_fees_usd"] for r in rts)
as1 = [r["as_1s"] for r in rts if r["as_1s"] != 0]
as3 = [r["as_3s"] for r in rts if r["as_3s"] != 0]
as5 = [r["as_5s"] for r in rts if r["as_5s"] != 0]
print(f"  Avg spread capture:     {np.mean(spreads):.2f} bps")
print(f"  Avg fee per trade:      {np.mean(fees_per_trade):.2f} bps")
print(f"  Total fees USD:         ${total_fees_usd:.2f}")
print(f"  Fees per trade USD:     ${total_fees_usd/n_rt:.4f}")
if as1: print(f"  Adverse sel 1s:         {np.mean(as1):.2f} bps (n={len(as1)})")
if as3: print(f"  Adverse sel 3s:         {np.mean(as3):.2f} bps (n={len(as3)})")
if as5: print(f"  Adverse sel 5s:         {np.mean(as5):.2f} bps (n={len(as5)})")

# 4) Trade quality
print(f"\n--- 4) Trade Quality ---")
net_pnls = [r["net_pnl_bps"] for r in rts]
wins = [r for r in rts if r["net_pnl_bps"] > 0]
losses = [r for r in rts if r["net_pnl_bps"] <= 0]
win_rate = len(wins) / n_rt * 100 if n_rt > 0 else 0
print(f"  Win rate:               {win_rate:.1f}% ({len(wins)}/{n_rt})")
if wins:
    print(f"  Avg win:                {np.mean([w['net_pnl_bps'] for w in wins]):.2f} bps")
    print(f"  Avg win hold:           {np.mean([w['hold_ms'] for w in wins]):.0f} ms")
if losses:
    print(f"  Avg loss:               {np.mean([l['net_pnl_bps'] for l in losses]):.2f} bps")
    print(f"  Avg loss hold:          {np.mean([l['hold_ms'] for l in losses]):.0f} ms")

# Loss distribution
if losses:
    loss_bps = sorted([l["net_pnl_bps"] for l in losses])
    print(f"\n  Loss distribution (bps):")
    print(f"    Best loss:  {loss_bps[-1]:.1f}")
    print(f"    Median:     {loss_bps[len(loss_bps)//2]:.1f}")
    print(f"    Worst loss: {loss_bps[0]:.1f}")
    print(f"    P25:        {loss_bps[len(loss_bps)//4]:.1f}")
    print(f"    P75:        {loss_bps[3*len(loss_bps)//4]:.1f}")

# Normal closes vs terminal
normal = [r for r in rts if r["exit_type"] == "CLOSE_NORMAL"]
terminal = [r for r in rts if r["exit_type"] != "CLOSE_NORMAL"]
print(f"\n  Normal closes:     {len(normal)} trades")
if normal:
    print(f"    Win rate:        {len([r for r in normal if r['net_pnl_bps']>0])/len(normal)*100:.0f}%")
    print(f"    Avg PnL:         {np.mean([r['net_pnl_bps'] for r in normal]):.2f} bps")
print(f"  Terminal closes:   {len(terminal)} trades")
if terminal:
    print(f"    Win rate:        {len([r for r in terminal if r['net_pnl_bps']>0])/len(terminal)*100:.0f}%")
    print(f"    Avg PnL:         {np.mean([r['net_pnl_bps'] for r in terminal]):.2f} bps")

# 5) Veredicto
print(f"\n{'='*60}")
print(f"--- 5) CAUSAL VERDICT ---")
print(f"{'='*60}")

total_gross = sum(r["gross_pnl_usd"] for r in rts)
total_net = total_gross - total_fees_usd
terminal_loss = sum(r["gross_pnl_usd"] for r in terminal)
normal_pnl = sum(r["gross_pnl_usd"] for r in normal)

print(f"  Total gross PnL:   ${total_gross:.2f}")
print(f"  Total fees:        ${total_fees_usd:.2f}")
print(f"  Total net PnL:     ${total_net:.2f}")
print(f"  From normal exits: ${normal_pnl:.2f}")
print(f"  From DD terminal:  ${terminal_loss:.2f}")
pct_terminal = abs(terminal_loss) / max(abs(total_gross), 0.01) * 100
print(f"  Terminal % of loss: {pct_terminal:.0f}%")

# Save
with open(os.path.join(out_dir, "pnl_audit.json"), "w") as f:
    json.dump({"roundtrips": rts, "summary": {
        "total_roundtrips": n_rt, "win_rate": win_rate,
        "total_gross": total_gross, "total_fees": total_fees_usd,
        "total_net": total_net, "terminal_loss": terminal_loss,
    }}, f, indent=2)
print(f"\nSaved to pnl_audit.json")
