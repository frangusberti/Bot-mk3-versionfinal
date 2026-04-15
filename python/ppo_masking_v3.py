"""
MaskablePPO Training v3: DD=5%, checkpoints at 75k/100k/150k.
Eval at each checkpoint with DD=5% + random_start.
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

TRAIN_CONFIG = {**BASE_CONFIG, "max_daily_dd": 0.05, "random_start_offset": True}
EVAL_CONFIG = {**BASE_CONFIG, "max_daily_dd": 0.05, "random_start_offset": True}

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
]
CLOSE_ACTIONS = {4, 8}

def mask_fn(env):
    return env.action_masks()

def run_eval(model, venv_path, steps=10000, label="eval"):
    def make_env():
        env = GrpcTradingEnv(server_addr="localhost:50051",
                             dataset_id="stage2_eval", symbol="BTCUSDT", **EVAL_CONFIG)
        return ActionMasker(env, mask_fn)

    ev = DummyVecEnv([make_env])
    ev = VecNormalize.load(venv_path, ev)
    ev.training = False; ev.norm_reward = False

    obs = ev.reset()
    episodes = []
    ep = {"steps": 0, "trades": 0, "maker_fills": 0, "close_with_pos": 0}
    last = {"equity": INITIAL_EQUITY, "rpnl": 0.0, "fees": 0.0, "pos_qty": 0.0, "reason": ""}
    actions_counter = Counter()
    win_hold_sum = 0.0; loss_hold_sum = 0.0; hold_samples = 0

    for _ in range(steps):
        masks = ev.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        act_int = int(action[0])
        actions_counter[act_int] += 1
        obs, reward, done, info = ev.step(action)
        i0 = info[0]
        ep["steps"] += 1
        ep["trades"] += i0.get("trades_executed", 0)
        ep["maker_fills"] += i0.get("resting_fill_count", 0)
        last["equity"] = i0.get("equity", last["equity"])
        last["rpnl"] = i0.get("realized_pnl", last["rpnl"])
        last["fees"] = i0.get("fees_paid", last["fees"])
        last["pos_qty"] = i0.get("position_qty", last["pos_qty"])
        last["reason"] = i0.get("reason", "")

        w = i0.get("avg_win_hold_ms", 0.0)
        l = i0.get("avg_loss_hold_ms", 0.0)
        if w > 0 or l > 0:
            win_hold_sum += w; loss_hold_sum += l; hold_samples += 1

        if act_int in CLOSE_ACTIONS and abs(last["pos_qty"]) > 1e-9:
            ep["close_with_pos"] += 1

        if done[0]:
            episodes.append({
                "steps": ep["steps"], "trades": ep["trades"],
                "maker_fills": ep["maker_fills"], "close_with_pos": ep["close_with_pos"],
                "reason": last["reason"], "pos_open": abs(last["pos_qty"]) > 1e-9,
                "terminal_pnl": round(last["equity"] - INITIAL_EQUITY, 4),
                "rpnl": round(last["rpnl"], 4), "fees": round(last["fees"], 4),
            })
            ep = {"steps": 0, "trades": 0, "maker_fills": 0, "close_with_pos": 0}

    ev.close()
    n = len(episodes)
    if n == 0:
        print(f"  [{label}] No episodes!"); return {}

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

    # Action distribution
    total_a = sum(actions_counter.values()) or 1
    hold_pct = actions_counter.get(0, 0) / total_a * 100
    open_pct = (actions_counter.get(1, 0) + actions_counter.get(5, 0)) / total_a * 100
    close_pct = (actions_counter.get(4, 0) + actions_counter.get(8, 0)) / total_a * 100
    reprice_pct = actions_counter.get(9, 0) / total_a * 100

    print(f"\n{'='*55}")
    print(f"  [{label}]")
    print(f"{'='*55}")
    print(f"  Episodes:               {n}")
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
    print(f"  HOLD:                   {hold_pct:.1f}%")
    print(f"  OPEN (L+S):             {open_pct:.1f}%")
    print(f"  CLOSE (L+S):            {close_pct:.1f}%")
    print(f"  REPRICE:                {reprice_pct:.1f}%")
    print(f"  avg_win_hold_ms:        {win_hold_sum/max(hold_samples,1):.0f}")
    print(f"  avg_loss_hold_ms:       {loss_hold_sum/max(hold_samples,1):.0f}")
    print(f"{'='*55}")
    return {"label": label, "episodes": n, "close_with_pos": total_cwp,
            "total_pnl": total_pnl, "total_trades": total_trades}


def main():
    out_dir = "python/runs_train/masking_v3_checkpoints"
    os.makedirs(out_dir, exist_ok=True)
    rss0 = psutil.Process().memory_info().rss / 1024**2
    print(f"[MEM] Start: {rss0:.0f} MB")

    # --- Setup training env ---
    print("\n[TRAIN] Setup (DD=5%, random_start=True)...")
    def make_train():
        env = GrpcTradingEnv(server_addr="localhost:50051",
                             dataset_id="golden_l2_v1_train", symbol="BTCUSDT", **TRAIN_CONFIG)
        return ActionMasker(env, mask_fn)

    train_venv = DummyVecEnv([make_train])
    train_venv = VecNormalize(train_venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = MaskablePPO("MlpPolicy", train_venv, learning_rate=2e-4, ent_coef=0.03,
                        n_steps=2048, batch_size=64, n_epochs=10, verbose=0, device=device)

    checkpoints = [75_000, 100_000, 150_000]
    trained_so_far = 0

    for ckpt in checkpoints:
        delta = ckpt - trained_so_far
        print(f"\n[TRAIN] Training {trained_so_far/1000:.0f}k -> {ckpt/1000:.0f}k ({delta/1000:.0f}k steps)...")
        model.learn(total_timesteps=delta, progress_bar=False, reset_num_timesteps=False)
        trained_so_far = ckpt

        # Save checkpoint
        ckpt_label = f"{ckpt//1000}k"
        model.save(os.path.join(out_dir, f"model_{ckpt_label}.zip"))
        train_venv.save(os.path.join(out_dir, f"venv_{ckpt_label}.pkl"))
        print(f"[TRAIN] Checkpoint {ckpt_label} saved.")

        # Eval
        rss = psutil.Process().memory_info().rss / 1024**2
        print(f"[MEM] {rss:.0f} MB")
        vp = os.path.join(out_dir, f"venv_{ckpt_label}.pkl")
        m_eval = MaskablePPO.load(os.path.join(out_dir, f"model_{ckpt_label}.zip"), device=device)
        result = run_eval(m_eval, vp, steps=10000, label=f"CKPT-{ckpt_label}")
        del m_eval  # free memory

    train_venv.close()
    rss1 = psutil.Process().memory_info().rss / 1024**2
    print(f"\n[MEM] End: {rss1:.0f} MB (delta: +{rss1-rss0:.0f} MB)")


if __name__ == "__main__":
    main()
