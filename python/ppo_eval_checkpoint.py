import os
import sys
import numpy as np
import pandas as pd
import torch
import psutil
import gc
from collections import defaultdict, Counter
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from paper_account import PaperAccount

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG", 
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE"
]

def get_memory_usage():
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024 # MB

def run_ppo_audit(model_path, venv_path, dataset_id, steps_per_eval=5000, server="127.0.0.1:50051", is_lite=True, **env_kwargs):
    """Standalone audit for a specific checkpoint."""
    rss_start = get_memory_usage()
    print(f"[MEMORY] Audit Start RSS: {rss_start:.1f} MB")
    default_env_params = dict(
        server_addr=server, 
        dataset_id=dataset_id, 
        symbol="BTCUSDT", 
        fill_model=1, # Semi-Optimistic mapping to Realistic in RLConfig
        reward_maker_fill_bonus=0.0,
        reward_taker_fill_penalty=0.0,
        reward_toxic_fill_penalty=0.0,
        reward_idle_posting_penalty=0.0,
        reward_distance_to_mid_penalty=0.0,
        reward_reprice_penalty_bps=0.0,
        post_delta_threshold_bps=0.0,
    )
    default_env_params.update(env_kwargs)
    dummy_env = GrpcTradingEnv(**default_env_params)
    venv = DummyVecEnv([lambda: dummy_env])
    
    if venv_path and os.path.exists(venv_path):
        venv = VecNormalize.load(venv_path, venv)
        venv.training = False
        venv.norm_reward = False
    
    model = PPO.load(model_path, env=venv)
    model.policy.eval()
    
    from collections import Counter
    actions_counter = Counter()
    trades_count = 0
    inventory_sum = 0.0
    inventory_sq_sum = 0.0
    inventory_min = 0.0
    inventory_max = 0.0
    
    hold_times, spread_captures_bps = [], []
    total_gate_close_blocked = 0
    total_gate_offset_blocked = 0
    total_gate_imbalance_blocked = 0
    total_fees_paid = 0.0
    
    obs = venv.reset()
    initial_equity = 10000.0
    paper = PaperAccount(initial_balance=initial_equity, fixed_notional=1000.0)
    
    # AS Tracking
    as_stats = {h: {"fav": 0, "adv": 0, "fav_amt": 0.0, "adv_amt": 0.0} for h in [1000, 3000, 5000]}
    pending_mtm = [] # [{ts, side, mid, resolved_horizons, meta, results: {h: {fav, amt}}}]
    mtm_history = []
    
    # Phase 4 Aggregation
    final_action_counts = defaultdict(int)
    final_exit_distribution = defaultdict(int)
    final_causal = {
        dim: defaultdict(lambda: {h: {"fav": 0, "adv": 0, "adv_amt": 0.0} for h in [1000, 3000, 5000]})
        for dim in ["kind", "vol_bucket", "imb_bucket", "regime"]
    }
    total_realized_pnl = 0.0
    prev_actions = defaultdict(int)
    prev_exits = defaultdict(int)
    prev_rpnl = 0.0
    total_exit_blocked = 0
    sum_exit_blocked_pnl = 0.0
    total_exit_blocked_1_to_4 = 0
    total_opportunity_lost = 0
    prev_exit_blocked = 0
    prev_exit_blocked_1_to_4 = 0
    prev_opportunity_lost = 0
    
    for _ in range(steps_per_eval):
        with torch.no_grad():
            obs_t = torch.tensor(obs, dtype=torch.float32).to(model.device)
            action_t = model.policy.get_distribution(obs_t).get_actions(deterministic=True)
        
        action = int(action_t[0].item())
        actions_counter[action] += 1
        
        prev_obs = obs.copy()
        obs, reward, done, info = venv.step(np.array([action]))
        
        info0 = info[0]
        curr_mid = info0.get("mid_price", 0.0)
        curr_ts = info0.get("ts", 0)
        
        # Aggregate Phase 4 Telemetry
        step_actions = info0.get("action_counts", {})
        for k, v in step_actions.items():
            if v < prev_actions[k]: 
                final_action_counts[k] += v
            else:
                final_action_counts[k] += (v - prev_actions[k])
            prev_actions[k] = v
            
        step_exits = info0.get("exit_distribution", {})
        for k, v in step_exits.items():
            if v < prev_exits[k]:
                final_exit_distribution[k] += v
            else:
                final_exit_distribution[k] += (v - prev_exits[k])
            prev_exits[k] = v
            
        curr_rpnl_total = info0.get("realized_pnl_total", 0.0)
        total_realized_pnl += (curr_rpnl_total - prev_rpnl) if abs(curr_rpnl_total) >= abs(prev_rpnl) else curr_rpnl_total
        prev_rpnl = curr_rpnl_total

        # Blocked Exit Aggregation
        curr_eb_count = info0.get("exit_blocked_count", 0)
        curr_eb_avg_pnl = info0.get("exit_blocked_avg_pnl_bps", 0.0)
        if curr_eb_count < prev_exit_blocked: # Reset
            total_exit_blocked += curr_eb_count
            sum_exit_blocked_pnl += (curr_eb_count * curr_eb_avg_pnl)
        else:
            delta_count = curr_eb_count - prev_exit_blocked
            if delta_count > 0:
                total_exit_blocked += delta_count
                sum_exit_blocked_pnl += (delta_count * curr_eb_avg_pnl)
        prev_exit_blocked = curr_eb_count

        # Blocked Exit 1-4 Aggregation
        curr_eb_1_4 = info0.get("exit_blocked_1_to_4_count", 0)
        if curr_eb_1_4 < prev_exit_blocked_1_to_4:
            total_exit_blocked_1_to_4 += curr_eb_1_4
        else:
            total_exit_blocked_1_to_4 += (curr_eb_1_4 - prev_exit_blocked_1_to_4)
        prev_exit_blocked_1_to_4 = curr_eb_1_4

        # Opportunity Lost Aggregation
        curr_ol = info0.get("opportunity_lost_count", 0)
        if curr_ol < prev_opportunity_lost:
            total_opportunity_lost += curr_ol
        else:
            total_opportunity_lost += (curr_ol - prev_opportunity_lost)
        prev_opportunity_lost = curr_ol
        
        # Resolve AS
        remaining_mtm = []
        for mtm in pending_mtm:
            resolved = mtm["resolved_horizons"]
            for h in [1000, 3000, 5000]:
                if h not in resolved and curr_ts - mtm["ts"] >= h:
                    move_bps = (curr_mid - mtm["ref_mid"]) * mtm["side"] / mtm["ref_mid"] * 10000
                    if move_bps > 0:
                        as_stats[h]["fav"] += 1
                        as_stats[h]["fav_amt"] += move_bps
                        mtm[f"res_{h}"] = {"fav": True, "amt": move_bps}
                    elif move_bps < 0:
                        as_stats[h]["adv"] += 1
                        as_stats[h]["adv_amt"] += abs(move_bps)
                        mtm[f"res_{h}"] = {"fav": False, "amt": abs(move_bps)}
                    resolved.add(h)
                    
                    # Incremental Causal Breakdown (Skip if is_lite)
                    if not is_lite:
                        meta = mtm["meta"]
                        res = {"fav": move_bps > 0, "amt": abs(move_bps)}
                        for dim in ["kind", "vol_bucket", "imb_bucket", "regime"]:
                            key = meta[dim]
                            bucket = final_causal[dim][key][h]
                            if res["fav"]:
                                bucket["fav"] += 1
                            else:
                                bucket["adv"] += 1
                                bucket["adv_amt"] += res["amt"]
            if len(resolved) < 3: remaining_mtm.append(mtm)
        pending_mtm = remaining_mtm
        
        paper.step(curr_mid)
        # Incremental Inventory Stats
        pos_qty = paper.position_qty
        inventory_sum += pos_qty
        inventory_sq_sum += pos_qty * pos_qty
        inventory_min = min(inventory_min, pos_qty)
        inventory_max = max(inventory_max, pos_qty)

        total_gate_close_blocked += info0.get("gate_close_blocked", 0)
        total_gate_offset_blocked += info0.get("gate_offset_blocked", 0)
        total_gate_imbalance_blocked += info0.get("gate_imbalance_blocked", 0)
        total_fees_paid += info0.get("fees_paid", 0.0)
        
        for fill in info0.get("fills", []):
            side, price, qty = fill["side"], fill["price"], fill["qty"]
            is_opening = (abs(prev_obs[0, 48]) < 0.05)
            side_sign = 1.0 if side == "Buy" else -1.0
            capture_bps = (curr_mid - price) * side_sign / curr_mid * 10000 if curr_mid > 0 else 0
            spread_captures_bps.append(capture_bps)
            paper.apply_fill(side, price, qty, fill.get("liquidity") == "Maker")
            
            mtm_entry = {
                "ts": curr_ts, "side": -side_sign, "ref_mid": curr_mid,
                "resolved_horizons": set(),
                "meta": {
                    "vol_bucket": "high" if prev_obs[0, 9] > 0.5 else "low",
                    "imb_bucket": "high" if abs(prev_obs[0, 21]) > 0.5 else "low",
                    "kind": "opening" if is_opening else "closing",
                    "regime": ["trend", "range", "shock", "dead"][np.argmax(prev_obs[0, 70:74])]
                }
            }
            pending_mtm.append(mtm_entry)
            trades_count += 1

    venv.close()
    
    # Aggregations
    final_causal = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"fav": 0, "adv": 0, "adv_amt": 0.0})))
    for mtm in mtm_history:
        meta = mtm["meta"]
        for h in [1000, 3000, 5000]:
            if f"res_{h}" in mtm:
                res = mtm[f"res_{h}"]
                for dim in ["kind", "vol_bucket", "imb_bucket", "regime"]:
                    bucket = meta[dim]
                    stats = final_causal[dim][bucket][h]
                    if res.get("fav"): stats["fav"] += 1
                    else:
                        stats["adv"] += 1
                        stats["adv_amt"] += res.get("amt", 0.0)

    # --- Phase 4 Lifecycle Aggregation ---

    # --- End of Aggregation ---
    
    avg_win_hold_ms = info0.get("avg_win_hold_ms", 0.0)
    avg_loss_hold_ms = info0.get("avg_loss_hold_ms", 0.0)
    scorecard = {
        "net_pnl": float(total_realized_pnl / initial_equity * 100) if initial_equity > 0 else 0.0,
        "total_trades": int(sum(dict(final_exit_distribution).values())),
        "profit_factor": float(paper.get_report()["profit_factor"]),
        "avg_spread_capture_bps": float(np.mean(spread_captures_bps) if spread_captures_bps else 0),
        "as_reports": {
            f"as_{int(h)//1000}s": {
                "fav_pct": stats["fav"]/(stats["fav"]+stats["adv"])*100 if (stats["fav"]+stats["adv"])>0 else 0, 
                "avg_adv_bps": stats["adv_amt"]/stats["adv"] if stats["adv"]>0 else 0
            } for h, stats in as_stats.items()
        },
        "causal_breakdown": {} if is_lite else {
            dim: {
                b: {
                    f"{int(h)//1000}s": {
                        "fav%": s["fav"]/(s["fav"]+s["adv"])*100 if (s["fav"]+s["adv"])>0 else 0, 
                        "adv_bps": s["adv_amt"]/s["adv"] if s["adv"]>0 else 0
                    } for h, s in h_stats.items()
                } for b, h_stats in b_stats.items()
            } for dim, b_stats in final_causal.items()
        },
        "gate_telemetry": {
            "close": total_gate_close_blocked, 
            "offset": total_gate_offset_blocked, 
            "imbalance": total_gate_imbalance_blocked
        },
        "total_trades": int(trades_count), # Using a counter for trades
        "lifecycle": {
            "action_counts": dict(final_action_counts),
            "exit_distribution": dict(final_exit_distribution),
            "total_realized_pnl": float(total_realized_pnl),
            "avg_win_hold_ms": float(avg_win_hold_ms),
            "avg_loss_hold_ms": float(avg_loss_hold_ms),
            "avg_pnl_per_trade": float(total_realized_pnl / trades_count) if trades_count > 0 else 0.0,
            "exit_blocked": {
                "count": int(total_exit_blocked),
                "avg_pnl": float(sum_exit_blocked_pnl / total_exit_blocked) if total_exit_blocked > 0 else 0.0,
                "trapping_density": float(total_exit_blocked / steps_per_eval * 100) if steps_per_eval > 0 else 0.0,
                "count_1_to_4_bps": int(total_exit_blocked_1_to_4),
                "opportunity_lost_count": int(total_opportunity_lost)
            },
            "side_distribution": {
                "avg_qty": float(inventory_sum / steps_per_eval) if steps_per_eval > 0 else 0.0,
                "min_qty": float(inventory_min),
                "max_qty": float(inventory_max)
            },
            "action_usage_detailed": {
                ACTION_LABELS[i]: actions_counter.get(i, 0) / steps_per_eval * 100 for i in range(len(ACTION_LABELS))
            }
        }
    }
    
    # Calculate Semantic Sums for fail-fast check
    # Calculate Semantic Sums for fail-fast check
    scorecard["lifecycle"]["semantic_summary"] = {
        "OPEN": (actions_counter.get(1,0) + actions_counter.get(5,0)) / steps_per_eval * 100,
        "ADD": (actions_counter.get(2,0) + actions_counter.get(6,0)) / steps_per_eval * 100,
        "RED": (actions_counter.get(3,0) + actions_counter.get(7,0)) / steps_per_eval * 100,
        "CLOSE": (actions_counter.get(4,0) + actions_counter.get(8,0)) / steps_per_eval * 100,
        "HOLD": actions_counter.get(0,0) / steps_per_eval * 100
    }
    
    rss_end = get_memory_usage()
    print(f"[MEMORY] Audit End RSS: {rss_end:.1f} MB")
    return scorecard

if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--venv", type=str, required=True)
    parser.add_argument("--dataset", type=str, default="golden_l2_v1_val")
    parser.add_argument("--steps", type=int, default=5000)
    parser.add_argument("--use_selective_entry", type=str, default="False")
    parser.add_argument("--entry_veto_threshold_bps", type=float, default=1.0)
    parser.add_argument("--server", type=str, default="127.0.0.1:50051")
    parser.add_argument("--fill_model", type=int, default=2)
    parser.add_argument("--profit_floor_bps", type=float, default=2.0)
    args = parser.parse_args()
    
    use_selective = args.use_selective_entry.lower() == "true"
    
    res = run_ppo_audit(
        model_path=args.model,
        venv_path=args.venv,
        dataset_id=args.dataset,
        steps_per_eval=args.steps,
        server=args.server,
        use_selective_entry=use_selective,
        entry_veto_threshold_bps=args.entry_veto_threshold_bps,
        fill_model=args.fill_model,
        profit_floor_bps=args.profit_floor_bps
    )
    print(json.dumps(res, indent=2))
