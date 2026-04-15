"""
Fill Model Comparison: same 150k checkpoint, fill_model = 0/1/2.
Measures spread capture at POST time, not fill time.
"""
import os, sys, json, numpy as np
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv

INITIAL_EQUITY = 10000.0
CKPT_DIR = "python/runs_train/masking_v3_checkpoints"
MODEL_PATH = os.path.join(CKPT_DIR, "model_150k.zip")
VENV_PATH = os.path.join(CKPT_DIR, "venv_150k.pkl")

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
    reward_consolidated_variant=True,
    max_daily_dd=0.05,
    random_start_offset=True,
)

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
]
OPEN_ACTIONS = {1, 2, 5, 6}
CLOSE_ACTIONS = {4, 8}
OPEN_LONG_ACTIONS = {1, 2}
OPEN_SHORT_ACTIONS = {5, 6}

def mask_fn(env):
    return env.action_masks()

def run_fillmodel_eval(fill_model_id, steps=10000):
    label = ["Conservative", "SemiOptimistic", "Optimistic"][fill_model_id]
    cfg = {**BASE_CONFIG, "fill_model": fill_model_id}

    def make_env():
        env = GrpcTradingEnv(server_addr="localhost:50051",
                             dataset_id="stage2_eval", symbol="BTCUSDT", **cfg)
        return ActionMasker(env, mask_fn)

    ev = DummyVecEnv([make_env])
    ev = VecNormalize.load(VENV_PATH, ev)
    ev.training = False; ev.norm_reward = False
    model = MaskablePPO.load(MODEL_PATH, env=ev, device="cpu")

    obs = ev.reset()

    # Track roundtrips with POST-time mid
    pending_open = None  # {side, post_mid, post_ts, act}
    roundtrips = []
    episodes = []
    ep = {"steps": 0, "trades": 0, "maker": 0, "taker": 0, "cwp": 0}
    last = {"equity": INITIAL_EQUITY, "rpnl": 0.0, "fees": 0.0,
            "pos_qty": 0.0, "reason": "", "mid": 0.0}
    mids_since_open = []

    for _ in range(steps):
        masks = ev.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        act_int = int(action[0])

        # Record mid BEFORE step (this is the "post time" mid)
        pre_mid = last["mid"]

        obs, reward, done, info = ev.step(action)
        i0 = info[0]
        ep["steps"] += 1
        ep["trades"] += i0.get("trades_executed", 0)
        ep["maker"] += i0.get("resting_fill_count", 0)
        ep["taker"] += i0.get("immediate_fill_count", 0)

        last["equity"] = i0.get("equity", last["equity"])
        last["rpnl"] = i0.get("realized_pnl", last["rpnl"])
        last["fees"] = i0.get("fees_paid", last["fees"])
        last["pos_qty"] = i0.get("position_qty", last["pos_qty"])
        last["reason"] = i0.get("reason", "")
        last["mid"] = i0.get("mid_price", last["mid"])

        # Track opens: record post_mid at the step we DECIDE to open
        if act_int in OPEN_ACTIONS and pending_open is None:
            side = "LONG" if act_int in OPEN_LONG_ACTIONS else "SHORT"
            pending_open = {"side": side, "post_mid": last["mid"], "post_ts": i0.get("ts", 0)}
            mids_since_open = []

        # Accumulate mids for adverse selection
        if pending_open is not None:
            mids_since_open.append(last["mid"])

        # Detect fills for the pending open
        fills = i0.get("fills", [])
        if pending_open is not None and fills:
            for f in fills:
                f_price = f.get("price", 0.0)
                f_qty = f.get("qty", 0.0)
                f_fee = f.get("fee", 0.0)
                f_liq = f.get("liquidity", "unknown")

                post_mid = pending_open["post_mid"]
                if post_mid > 0:
                    if pending_open["side"] == "LONG":
                        sc_bps = (post_mid - f_price) / post_mid * 10000
                    else:
                        sc_bps = (f_price - post_mid) / post_mid * 10000
                else:
                    sc_bps = 0.0

                # Adverse selection from POST mid
                as_1s = as_3s = as_5s = 0.0
                if len(mids_since_open) > 1:
                    for delay, attr in [(1, "1s"), (3, "3s"), (5, "5s")]:
                        if len(mids_since_open) > delay:
                            fm = mids_since_open[delay]
                            if pending_open["side"] == "LONG":
                                move = (fm - post_mid) / post_mid * 10000
                            else:
                                move = (post_mid - fm) / post_mid * 10000
                            if attr == "1s": as_1s = move
                            elif attr == "3s": as_3s = move
                            elif attr == "5s": as_5s = move

                roundtrips.append({
                    "side": pending_open["side"],
                    "post_mid": post_mid,
                    "fill_price": f_price,
                    "spread_capture_bps": round(sc_bps, 2),
                    "fee": f_fee,
                    "qty": f_qty,
                    "liquidity": f_liq,
                    "as_1s": round(as_1s, 2),
                    "as_3s": round(as_3s, 2),
                    "as_5s": round(as_5s, 2),
                })
                pending_open = None
                break

        if act_int in CLOSE_ACTIONS and abs(last["pos_qty"]) > 1e-9:
            ep["cwp"] += 1

        if done[0]:
            episodes.append({
                "steps": ep["steps"], "reason": last["reason"],
                "pos_open": abs(last["pos_qty"]) > 1e-9,
                "terminal_pnl": round(last["equity"] - INITIAL_EQUITY, 4),
                "rpnl": round(last["rpnl"], 4),
                "fees": round(last["fees"], 4),
                "trades": ep["trades"], "maker": ep["maker"],
                "taker": ep["taker"], "cwp": ep["cwp"],
            })
            ep = {"steps": 0, "trades": 0, "maker": 0, "taker": 0, "cwp": 0}
            pending_open = None
            mids_since_open = []

    ev.close()

    # === Report ===
    n_ep = len(episodes)
    n_rt = len(roundtrips)
    total_trades = sum(e["trades"] for e in episodes)
    total_maker = sum(e["maker"] for e in episodes)
    total_taker = sum(e["taker"] for e in episodes)
    total_pnl = sum(e["terminal_pnl"] for e in episodes)
    total_rpnl = sum(e["rpnl"] for e in episodes)
    total_fees = sum(e["fees"] for e in episodes)
    total_cwp = sum(e["cwp"] for e in episodes)
    pos_open = sum(1 for e in episodes if e["pos_open"])

    reasons = {}
    for e in episodes:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1

    sc_list = [r["spread_capture_bps"] for r in roundtrips]
    as1 = [r["as_1s"] for r in roundtrips if r["as_1s"] != 0]
    as3 = [r["as_3s"] for r in roundtrips if r["as_3s"] != 0]
    as5 = [r["as_5s"] for r in roundtrips if r["as_5s"] != 0]

    gross_pnl = total_pnl  # equity-based (already net of fees, but best we have)
    # Better: gross = rpnl (before fees from equity perspective)
    # Actually total_pnl = equity_final - initial = gross_pnl - fees implicitly
    # So gross ≈ total_pnl + total_fees
    gross_approx = total_pnl + total_fees

    print(f"\n{'='*55}")
    print(f"  fill_model={fill_model_id} ({label})")
    print(f"{'='*55}")
    print(f"  Episodes:               {n_ep}")
    print(f"  Total trades:           {total_trades}")
    print(f"  Maker fills:            {total_maker}")
    print(f"  Taker fills:            {total_taker}")
    print(f"  Gross PnL (approx):     ${gross_approx:.2f}")
    print(f"  Net PnL (MTM):          ${total_pnl:.2f}")
    print(f"  Fees total:             ${total_fees:.2f}")
    if sc_list:
        print(f"  Avg spread capture:     {np.mean(sc_list):.2f} bps (n={len(sc_list)}, at POST mid)")
    else:
        print(f"  Avg spread capture:     N/A (no fills)")
    if as1: print(f"  Adverse sel 1s:         {np.mean(as1):.2f} bps (n={len(as1)})")
    if as3: print(f"  Adverse sel 3s:         {np.mean(as3):.2f} bps (n={len(as3)})")
    if as5: print(f"  Adverse sel 5s:         {np.mean(as5):.2f} bps (n={len(as5)})")
    if not as1 and not as3 and not as5:
        print(f"  Adverse sel:            N/A")
    print(f"  Pos open at done:       {pos_open}/{n_ep} ({pos_open/max(n_ep,1)*100:.0f}%)")
    print(f"  close_with_pos:         {total_cwp}")
    print(f"  Done reasons:")
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {r}: {c}")
    print(f"{'='*55}")

    return {"fill_model": fill_model_id, "label": label, "episodes": n_ep,
            "total_trades": total_trades, "maker": total_maker, "taker": total_taker,
            "net_pnl": total_pnl, "gross_pnl": gross_approx, "fees": total_fees,
            "spread_capture_avg": round(np.mean(sc_list), 2) if sc_list else 0,
            "close_with_pos": total_cwp, "pos_open_pct": pos_open/max(n_ep,1)*100,
            "reasons": reasons}


def main():
    results = []
    for fm in [0, 1, 2]:
        print(f"\n[RUNNING] fill_model={fm}...")
        r = run_fillmodel_eval(fm, steps=10000)
        results.append(r)

    with open(os.path.join(CKPT_DIR, "fillmodel_comparison.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("\nSaved to fillmodel_comparison.json")


if __name__ == "__main__":
    main()
