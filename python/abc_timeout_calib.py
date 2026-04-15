
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
    print("[SYSTEM] Force restarting bot-server to clear RAM...")
    os.system("taskkill /F /IM bot-server.exe 2>NUL")
    time.sleep(2)
    return subprocess.Popen([r"target\release\bot-server.exe"], stdout=subprocess.DEVNULL)

def audit_model(branch_name, model, venv_path, timeout_ms):
    print(f"\n[AUDIT] Starting detailed audit for {branch_name} (Timeout={timeout_ms}ms)...")
    
    settings = V20B_BASE_CONFIG.copy()
    settings.update(dict(
        use_exit_curriculum_d1=True,
        maker_first_exit_timeout_ms=timeout_ms,
        exit_fallback_loss_bps=10.0,
        exit_fallback_mfe_giveback_bps=4.0,
        exit_fallback_thesis_decay_threshold=0.40
    ))
    
    def make_val_env():
        # APAGAR MAKER BONUS PARA ESTA FASE
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
    
    # Stats counters
    stats = {
        "trades": 0, "realized_pnl": 0.0, "fees_total": 0.0,
        "entry_fees": 0.0, "exit_fees": 0.0,
        "exit_maker": 0, "exit_taker": 0,
        "pnl_long": 0.0, "pnl_short": 0.0,
        "done_reasons": {},
        "mfe_ratio_sum": 0.0, "mfe_count": 0,
        "d1_fallbacks": 0, "d1_intent_steps": 0,
        "maker_latencies": [],
        "dangling_qty": 0.0
    }
    
    obs = val_venv.reset()
    in_pos = False
    current_side = 0
    max_upnl = 0.0
    start_rpnl = 0.0
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
        
        # Latency tracking at fill time
        if info.get("trades_executed", 0) > 0:
            for fill in info.get("fills", []):
                side = fill.get('side', '')
                is_exit = (current_side > 0 and "Sell" in side) or (current_side < 0 and "Buy" in side)
                f_fee = fill.get('fee', 0.0)
                l = fill.get('liquidity', '')
                stats["fees_total"] += abs(f_fee)
                
                if is_exit:
                    stats["exit_fees"] += abs(f_fee)
                    if "Maker" in l: 
                        stats["exit_maker"] += 1
                        stats["maker_latencies"].append(info.get("time_since_exit_intent_ms", 0))
                    else: 
                        stats["exit_taker"] += 1
                else:
                    stats["entry_fees"] += abs(f_fee)
                    if not in_pos: stats["trades"] += 1
        
        if abs(pos_qty) > 1e-9:
            if not in_pos:
                in_pos = True
                current_side = 1 if pos_qty > 0 else -1
                max_upnl = upnl_bps
                start_rpnl = info.get("realized_pnl", 0.0)
            max_upnl = max(max_upnl, upnl_bps)
        else:
            if in_pos:
                # Close trade stats
                trade_rpnl = info.get("realized_pnl", 0.0) - start_rpnl
                if current_side > 0: stats["pnl_long"] += trade_rpnl
                else: stats["pnl_short"] += trade_rpnl
                
                if max_upnl > 0.1:
                    actual_return = upnl_bps # at close point
                    stats["mfe_ratio_sum"] += (actual_return / max_upnl)
                    stats["mfe_count"] += 1
                
                in_pos = False
                current_side = 0

        if done:
            reason = info.get("reason", "UNKNOWN")
            stats["done_reasons"][reason] = stats["done_reasons"].get(reason, 0) + 1
            if processed_steps > 30000: break
            obs = val_venv.reset()
            in_pos = False
            current_side = 0
            
    stats["realized_pnl"] = info.get("realized_pnl", 0.0)
    stats["dangling_qty"] = pos_qty

    print(f"\n--- RESULTS FOR {branch_name} (Timeout={timeout_ms}ms) ---")
    total_exit = stats["exit_maker"] + stats["exit_taker"]
    print(f"Total Trades: {stats['trades']}")
    print(f"Exits Maker/Taker: {stats['exit_maker']} / {stats['exit_taker']} ({stats['exit_maker']/(total_exit if total_exit>0 else 1)*100:.1f}% Maker)")
    print(f"Avg Wait (steps in intent): {stats['d1_intent_steps']}")
    print(f"Exit Fees: {stats['exit_fees']:.2f}, Net PnL: {stats['realized_pnl'] - stats['fees_total']:.2f}")
    print(f"Realized PnL: {stats['realized_pnl']:.2f}")
    print(f"MFE Capture Ratio: {stats['mfe_ratio_sum']/(stats['mfe_count'] if stats['mfe_count']>0 else 1)*100:.1f}%")
    print(f"DAILY_DD_LIMIT count: {stats['done_reasons'].get('DAILY_DD_LIMIT', 0)}")
    print(f"Fallback Taker Count: {stats['d1_fallbacks']}")
    print(f"Dangling Position: {stats['dangling_qty']:.6f} BTC")
    
    if stats["maker_latencies"]:
        lats = sorted(stats["maker_latencies"])
        p50 = lats[len(lats)//2]
        p90 = lats[int(len(lats)*0.9)]
        print(f"Maker Fill Latency: p50={p50}ms, p90={p90}ms")
    else:
        print("Maker Fill Latency: N/A")
    
    print(f"Reasons: {stats['done_reasons']}")
    val_venv.close()
    gc.collect()

def run_experiment_branch(branch_name, timeout_ms, steps=25000):
    print(f"\n\n>>> STARTING BRANCH: {branch_name} (Timeout={timeout_ms}ms) <<<")
    server_proc = restart_server()
    torch.cuda.empty_cache()
    gc.collect()
    
    out_dir = f"python/runs_train/abc_timeout_calib/{branch_name}"
    os.makedirs(out_dir, exist_ok=True)
    
    base_model = "python/runs_train/training_v9_selective_v20b/model_v20b_final.zip"
    base_venv = "python/runs_train/training_v9_selective_v20b/venv_v20b_final.pkl"
    
    settings = V20B_BASE_CONFIG.copy()
    settings.update(dict(
        use_exit_curriculum_d1=True,
        maker_first_exit_timeout_ms=timeout_ms,
        exit_fallback_loss_bps=10.0,
        exit_fallback_mfe_giveback_bps=4.0,
        exit_fallback_thesis_decay_threshold=0.40
    ))

    def make_train_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            reward_exit_maker_bonus_weight=0.0, # APAGADO
            **settings
        ), mask_fn)
    
    train_venv = DummyVecEnv([make_train_env])
    train_venv = VecNormalize.load(base_venv, train_venv)
    model = MaskablePPO.load(base_model, env=train_venv, device="cuda", custom_objects={"learning_rate": 5e-6})
    model.learn(total_timesteps=steps, progress_bar=True)
    
    model.save(os.path.join(out_dir, "model.zip"))
    train_venv.save(os.path.join(out_dir, "venv.pkl"))
    
    audit_model(branch_name, model, os.path.join(out_dir, "venv.pkl"), timeout_ms)
    
    train_venv.close()
    server_proc.terminate()
    time.sleep(5)

if __name__ == "__main__":
    # Calibración A/B/C
    run_experiment_branch("Rama_A_3s", timeout_ms=3000, steps=25000)
    run_experiment_branch("Rama_B_5s", timeout_ms=5000, steps=25000)
    run_experiment_branch("Rama_C_8s", timeout_ms=8000, steps=25000)
