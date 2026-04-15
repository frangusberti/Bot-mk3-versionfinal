"""
MaskablePPO Training v2: relaxed DD (5%) for training, 50k steps.
Eval with both 5% and strict 3% for comparison.
"""
import os, sys, json, torch, numpy as np, psutil
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv

INITIAL_EQUITY = 10000.0

# Base config (shared)
BASE_CONFIG = dict(
    close_position_loss_threshold=0.003,
    min_post_offset_bps=0.2,
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_quote_presence_bonus=0.0,
    reward_thesis_decay_weight=0.0001,
    override_action_dim=10,
    use_selective_entry=True,
    entry_veto_threshold_bps=0.2,
    micro_strict=False,
    fill_model=2,
    reward_consolidated_variant=True,
)

# Training: relaxed DD + random start
TRAIN_CONFIG = {**BASE_CONFIG, "max_daily_dd": 0.05, "random_start_offset": True}
# Eval: strict DD + random start
EVAL_CONFIG_STRICT = {**BASE_CONFIG, "max_daily_dd": 0.03, "random_start_offset": True}
EVAL_CONFIG_RELAXED = {**BASE_CONFIG, "max_daily_dd": 0.05, "random_start_offset": True}

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
]
CLOSE_ACTIONS = {4, 8}

def mask_fn(env):
    return env.action_masks()

def run_eval(model, eval_venv, steps=10000, label="eval"):
    obs = eval_venv.reset()
    episodes = []
    ep = {"steps": 0, "trades": 0, "maker_fills": 0, "close_with_pos": 0}
    last = {"equity": INITIAL_EQUITY, "rpnl": 0.0, "fees": 0.0, "pos_qty": 0.0,
            "reason": "", "pos_side": "FLAT"}
    win_hold_sum = 0.0; loss_hold_sum = 0.0; hold_samples = 0

    for _ in range(steps):
        masks = eval_venv.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        act_int = int(action[0])
        obs, reward, done, info = eval_venv.step(action)
        i0 = info[0]
        ep["steps"] += 1
        ep["trades"] += i0.get("trades_executed", 0)
        ep["maker_fills"] += i0.get("resting_fill_count", 0)

        last["equity"] = i0.get("equity", last["equity"])
        last["rpnl"] = i0.get("realized_pnl", last["rpnl"])
        last["fees"] = i0.get("fees_paid", last["fees"])
        last["pos_qty"] = i0.get("position_qty", last["pos_qty"])
        last["reason"] = i0.get("reason", "")
        last["pos_side"] = i0.get("position_side", "FLAT")

        w = i0.get("avg_win_hold_ms", 0.0)
        l = i0.get("avg_loss_hold_ms", 0.0)
        if w > 0 or l > 0:
            win_hold_sum += w; loss_hold_sum += l; hold_samples += 1

        if act_int in CLOSE_ACTIONS and abs(last["pos_qty"]) > 1e-9:
            ep["close_with_pos"] += 1

        if done[0]:
            episodes.append({
                "steps": ep["steps"], "trades": ep["trades"],
                "maker_fills": ep["maker_fills"],
                "close_with_pos": ep["close_with_pos"],
                "reason": last["reason"],
                "pos_open": abs(last["pos_qty"]) > 1e-9,
                "terminal_pnl": round(last["equity"] - INITIAL_EQUITY, 4),
                "rpnl": round(last["rpnl"], 4),
                "fees": round(last["fees"], 4),
            })
            ep = {"steps": 0, "trades": 0, "maker_fills": 0, "close_with_pos": 0}

    n = len(episodes)
    if n == 0:
        print(f"  [{label}] No episodes completed!"); return

    lengths = sorted([e["steps"] for e in episodes])
    reasons = {}
    pos_open = 0; total_cwp = 0
    for e in episodes:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
        if e["pos_open"]: pos_open += 1
        total_cwp += e["close_with_pos"]

    total_pnl = sum(e["terminal_pnl"] for e in episodes)
    total_rpnl = sum(e["rpnl"] for e in episodes)
    total_fees = sum(e["fees"] for e in episodes)
    total_trades = sum(e["trades"] for e in episodes)
    total_mf = sum(e["maker_fills"] for e in episodes)

    print(f"\n{'='*55}")
    print(f"  [{label}] {n} episodes | {sum(lengths)} steps")
    print(f"{'='*55}")
    print(f"  Avg/Median length:      {sum(lengths)/n:.0f} / {lengths[n//2]}")
    print(f"  Min/Max:                {lengths[0]} / {lengths[-1]}")
    print(f"  Done reasons:")
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {r}: {c} ({c/n*100:.0f}%)")
    print(f"  Pos open at done:       {pos_open}/{n} ({pos_open/n*100:.0f}%)")
    print(f"  Net PnL (MTM):          ${total_pnl:.2f}")
    print(f"  Realized PnL:           ${total_rpnl:.2f}")
    print(f"  Fees total:             ${total_fees:.2f}")
    print(f"  Total trades:           {total_trades}")
    print(f"  Maker fills:            {total_mf}")
    print(f"  close_with_pos:         {total_cwp}")
    print(f"  avg_win_hold_ms:        {win_hold_sum/max(hold_samples,1):.0f}")
    print(f"  avg_loss_hold_ms:       {loss_hold_sum/max(hold_samples,1):.0f}")
    print(f"{'='*55}")
    return {"label": label, "episodes": n, "total_pnl": total_pnl, "total_trades": total_trades}


def main():
    out_dir = "python/runs_train/masking_v2_dd05"
    os.makedirs(out_dir, exist_ok=True)
    rss0 = psutil.Process().memory_info().rss / 1024**2
    print(f"[MEM] Start: {rss0:.0f} MB")

    # --- Training ---
    print("\n[TRAIN] Setting up env (max_daily_dd=0.05, random_start=True)...")
    def make_train():
        env = GrpcTradingEnv(server_addr="localhost:50051",
                             dataset_id="golden_l2_v1_train", symbol="BTCUSDT", **TRAIN_CONFIG)
        return ActionMasker(env, mask_fn)

    train_venv = DummyVecEnv([make_train])
    train_venv = VecNormalize(train_venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[TRAIN] MaskablePPO (device={device}, 50k steps)...")
    model = MaskablePPO("MlpPolicy", train_venv, learning_rate=2e-4, ent_coef=0.03,
                        n_steps=2048, batch_size=64, n_epochs=10, verbose=0, device=device)
    model.learn(total_timesteps=50000, progress_bar=False)

    model.save(os.path.join(out_dir, "model.zip"))
    train_venv.save(os.path.join(out_dir, "venv.pkl"))
    print("[TRAIN] Done. Model saved.")

    # --- Eval: relaxed DD (same as training) ---
    print("\n[EVAL-RELAXED] 10k steps, max_daily_dd=0.05")
    def make_eval_r():
        env = GrpcTradingEnv(server_addr="localhost:50051",
                             dataset_id="stage2_eval", symbol="BTCUSDT", **EVAL_CONFIG_RELAXED)
        return ActionMasker(env, mask_fn)
    ev_r = DummyVecEnv([make_eval_r])
    ev_r = VecNormalize.load(os.path.join(out_dir, "venv.pkl"), ev_r)
    ev_r.training = False; ev_r.norm_reward = False
    m_r = MaskablePPO.load(os.path.join(out_dir, "model.zip"), env=ev_r, device=device)
    run_eval(m_r, ev_r, steps=10000, label="EVAL-DD5%")

    # --- Eval: strict DD ---
    print("\n[EVAL-STRICT] 10k steps, max_daily_dd=0.03")
    def make_eval_s():
        env = GrpcTradingEnv(server_addr="localhost:50051",
                             dataset_id="stage2_eval", symbol="BTCUSDT", **EVAL_CONFIG_STRICT)
        return ActionMasker(env, mask_fn)
    ev_s = DummyVecEnv([make_eval_s])
    ev_s = VecNormalize.load(os.path.join(out_dir, "venv.pkl"), ev_s)
    ev_s.training = False; ev_s.norm_reward = False
    m_s = MaskablePPO.load(os.path.join(out_dir, "model.zip"), env=ev_s, device=device)
    run_eval(m_s, ev_s, steps=10000, label="EVAL-DD3%")

    rss1 = psutil.Process().memory_info().rss / 1024**2
    print(f"\n[MEM] End: {rss1:.0f} MB (delta: +{rss1-rss0:.0f} MB)")


if __name__ == "__main__":
    main()
