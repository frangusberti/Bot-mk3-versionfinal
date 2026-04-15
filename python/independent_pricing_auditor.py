
import os
import sys
import torch
import gc
import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
import time
import subprocess

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from bot_ml.grpc_env import GrpcTradingEnv

# BASE CONFIG V2.0b
V20B_BASE_CONFIG = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.02,
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,
    long_veto_bb_pos_5m_threshold=0.40,
    long_veto_regime_dead_threshold=0.50,
    fill_model=2,
    use_exit_curriculum_d1=True,
    maker_first_exit_timeout_ms=8000
)

def mask_fn(env): return env.action_masks()

def restart_server():
    print("[SYSTEM] Force restarting bot-server...")
    os.system("taskkill /F /IM bot-server.exe 2>NUL")
    time.sleep(2)
    return subprocess.Popen([r"target\release\bot-server.exe"], stdout=subprocess.DEVNULL)

def run_detailed_audit(branch_name, model_dir, pricing_mult):
    model_path = os.path.join(model_dir, "model.zip")
    venv_path = os.path.join(model_dir, "venv.pkl")
    
    if not os.path.exists(model_path):
        print(f"[SKIP] {branch_name} not ready.")
        return None

    print(f"\n[AUDIT] Auditing {branch_name} (Mult={pricing_mult})...")
    
    def make_val_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_val",
            symbol="BTCUSDT",
            reward_exit_maker_bonus_weight=0.0,
            exit_maker_pricing_multiplier=pricing_mult,
            **V20B_BASE_CONFIG
        ), mask_fn)
    
    val_venv = DummyVecEnv([make_val_env])
    val_venv = VecNormalize.load(venv_path, val_venv)
    val_venv.training = False
    val_venv.norm_reward = False
    
    model = MaskablePPO.load(model_path, env=val_venv, device="cuda")

    stats = {
        "trades": 0, "realized_pnl": 0.0, "fees_total": 0.0,
        "exit_fees": 0.0, "exit_maker": 0, "exit_taker": 0,
        "wait_steps": 0, "fallbacks": 0, "dd_limits": 0,
        "CLOSE": 0, "REDUCE": 0, "latencies": [],
        "dangling": 0.0, "pnl_net": 0.0
    }
    
    obs = val_venv.reset()
    in_pos = False
    current_side = 0
    processed_steps = 0
    
    while processed_steps < 35000:
        masks = val_venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, done, info_list = val_venv.step(action)
        info = info_list[0]
        processed_steps += 1
        
        if info.get("exit_intent_active", 0): stats["wait_steps"] += 1
        if info.get("exit_fallback_triggered", 0): stats["fallbacks"] += 1
        
        act = int(action[0])
        if act in [4, 8]: stats["CLOSE"] += 1
        elif act in [3, 7]: stats["REDUCE"] += 1

        if info.get("trades_executed", 0) > 0:
            for fill in info.get("fills", []):
                side = fill.get('side', '')
                is_exit = (current_side > 0 and "Sell" in side) or (current_side < 0 and "Buy" in side)
                fee = abs(fill.get('fee', 0.0))
                stats["fees_total"] += fee
                if is_exit:
                    stats["exit_fees"] += fee
                    if "Maker" in fill.get('liquidity', ''): 
                        stats["exit_maker"] += 1
                        stats["latencies"].append(info.get("time_since_exit_intent_ms", 0))
                    else: stats["exit_taker"] += 1
                elif not in_pos: stats["trades"] += 1
        
        pos_qty = info.get("position_qty", 0.0)
        if abs(pos_qty) > 1e-9:
            if not in_pos:
                in_pos = True
                current_side = 1 if pos_qty > 0 else -1
        else:
            in_pos = False
            current_side = 0

        if done:
            if info.get("reason") == "DAILY_DD_LIMIT": stats["dd_limits"] += 1
            if processed_steps > 30000: break
            obs = val_venv.reset()
            in_pos = False

    stats["pnl_net"] = info.get("realized_pnl", 0.0) - stats["fees_total"]
    stats["realized_pnl"] = info.get("realized_pnl", 0.0)
    stats["dangling"] = pos_qty
    
    val_venv.close()
    del model
    gc.collect()
    return stats

if __name__ == "__main__":
    restart_server()
    branch_mults = {
        "Rama_P1_Base": 1.0,
        "Rama_P2_Aggressive": 0.5,
        "Rama_P3_Ultra": 0.1
    }
    
    results = {}
    for b, m in branch_mults.items():
        results[b] = run_detailed_audit(b, f"python/runs_train/abc_pricing_calib/{b}", m)
        
    print("\n" + "="*85)
    print("PHASE P: PRICING CALIBRATION FINAL RESULTS")
    print("="*85)
    header = f"{'Branch':<18} | {'Maker%':<6} | {'Net PnL':<10} | {'ExitFees':<8} | {'Latency':<8} | {'FB':<3} | {'DD':<2}"
    print(header)
    print("-" * len(header))
    
    for b, s in results.items():
        if not s: continue
        total_exit = s["exit_maker"] + s["exit_taker"]
        maker_pct = s["exit_maker"] / (total_exit if total_exit>0 else 1) * 100
        p90 = np.percentile(s["latencies"], 90) if s["latencies"] else 0
        
        print(f"{b:<18} | {maker_pct:>5.1f}% | {s['pnl_net']:>10.2f} | {s['exit_fees']:>8.2f} | {p90:>6.0f}ms | {s['fallbacks']:>3} | {s['dd_limits']:>2}")
    print("="*85)
    
    # Detailed actions and volume report
    print("\n[ACTION ANALYSIS]")
    for b, s in results.items():
        if not s: continue
        print(f"{b:<18} -> Multiplier: {branch_mults[b]:.1f} | Trades: {s['trades']} | CLOSE: {s['CLOSE']} | REDUCE: {s['REDUCE']} | Dangling: {s['dangling']:.6f}")
