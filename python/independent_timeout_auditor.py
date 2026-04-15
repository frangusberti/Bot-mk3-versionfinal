
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
    fill_model=2 # OPTIMISTIC
)

def mask_fn(env): return env.action_masks()

def restart_server():
    print("[SYSTEM] Force restarting bot-server...")
    os.system("taskkill /F /IM bot-server.exe 2>NUL")
    time.sleep(2)
    return subprocess.Popen([r"target\release\bot-server.exe"], stdout=subprocess.DEVNULL)

def audit_branch(branch_name, model_dir, timeout_ms):
    model_path = os.path.join(model_dir, "model.zip")
    venv_path = os.path.join(model_dir, "venv.pkl")
    
    if not os.path.exists(model_path):
        print(f"[SKIP] {branch_name} not ready yet.")
        return None

    print(f"\n[AUDIT] Starting independent audit for {branch_name}...")
    
    settings = V20B_BASE_CONFIG.copy()
    settings.update(dict(
        use_exit_curriculum_d1=True,
        maker_first_exit_timeout_ms=timeout_ms,
        exit_fallback_loss_bps=10.0,
        exit_fallback_mfe_giveback_bps=4.0,
        exit_fallback_thesis_decay_threshold=0.40
    ))
    
    def make_val_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_val",
            symbol="BTCUSDT",
            reward_exit_maker_bonus_weight=0.0,
            **settings
        ), mask_fn)
    
    val_venv = DummyVecEnv([make_val_env])
    val_venv = VecNormalize.load(venv_path, val_venv)
    val_venv.training = False
    val_venv.norm_reward = False
    
    model = MaskablePPO.load(model_path, env=val_venv, device="cuda")

    stats = {
        "trades": 0, "realized_pnl": 0.0, "fees_total": 0.0,
        "exit_maker": 0, "exit_taker": 0,
        "mfe_ratio_sum": 0.0, "mfe_count": 0,
        "d1_fallbacks": 0, "d1_intent_steps": 0,
        "maker_latencies": [],
        "dangling_qty": 0.0
    }
    
    obs = val_venv.reset()
    in_pos = False
    current_side = 0
    max_upnl = 0.0
    processed_steps = 0
    
    while processed_steps < 35000:
        masks = val_venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, done, info_list = val_venv.step(action)
        info = info_list[0]
        processed_steps += 1
        
        pos_qty = info.get("position_qty", 0.0)
        upnl_bps = info.get("unrealized_pnl", 0.0) / info.get("equity", 10000.0) * 10000.0

        if info.get("exit_fallback_triggered", 0): stats["d1_fallbacks"] += 1
        if info.get("exit_intent_active", 0): stats["d1_intent_steps"] += 1
        
        if info.get("trades_executed", 0) > 0:
            for fill in info.get("fills", []):
                side = fill.get('side', '')
                is_exit = (current_side > 0 and "Sell" in side) or (current_side < 0 and "Buy" in side)
                stats["fees_total"] += abs(fill.get('fee', 0.0))
                if is_exit:
                    if "Maker" in fill.get('liquidity', ''): 
                        stats["exit_maker"] += 1
                        stats["maker_latencies"].append(info.get("time_since_exit_intent_ms", 0))
                    else: stats["exit_taker"] += 1
                else:
                    if not in_pos: stats["trades"] += 1
        
        if abs(pos_qty) > 1e-9:
            if not in_pos:
                in_pos = True
                current_side = 1 if pos_qty > 0 else -1
                max_upnl = upnl_bps
            max_upnl = max(max_upnl, upnl_bps)
        else:
            if in_pos:
                if max_upnl > 0.1:
                    stats["mfe_ratio_sum"] += (upnl_bps / max_upnl)
                    stats["mfe_count"] += 1
                in_pos = False
                current_side = 0

        if done:
            if processed_steps > 30000: break
            obs = val_venv.reset()
            in_pos = False
            current_side = 0
            
    stats["realized_pnl"] = info.get("realized_pnl", 0.0)
    stats["dangling_qty"] = pos_qty
    stats["reasons"] = info.get("reason", "N/A")

    val_venv.close()
    del model
    gc.collect()
    return stats

if __name__ == "__main__":
    restart_server()
    results = {}
    
    timeout_map = {"Rama_A_3s": 3000, "Rama_B_5s": 5000, "Rama_C_8s": 8000}
    
    for branch, timeout in timeout_map.items():
        res = audit_branch(branch, f"python/runs_train/abc_timeout_calib/{branch}", timeout)
        if res: results[branch] = res
        
    print("\n" + "="*50)
    print("FINAL TIMEOUT CALIBRATION SUMMARY")
    print("="*50)
    print(f"{'Branch':<15} | {'Maker%':<6} | {'Wait Steps':<10} | {'MFE%':<6} | {'Net PnL':<10} | {'p90Lat':<8}")
    print("-"*75)
    
    for branch, s in results.items():
        total_exit = s["exit_maker"] + s["exit_taker"]
        maker_pct = s["exit_maker"] / (total_exit if total_exit>0 else 1) * 100
        mfe_pct = s["mfe_ratio_sum"] / (s["mfe_count"] if s["mfe_count"]>0 else 1) * 100
        
        p90 = 0
        if s["maker_latencies"]:
            lats = sorted(s["maker_latencies"])
            p90 = lats[int(len(lats)*0.9)]
            
        print(f"{branch:<15} | {maker_pct:>5.1f}% | {s['d1_intent_steps']:>10} | {mfe_pct:>5.1f}% | {s['realized_pnl']-s['fees_total']:>10.2f} | {p90:>6}ms")
    print("="*50)
