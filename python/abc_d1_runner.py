
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

D1_CURRICULUM_SETTINGS = dict(
    use_exit_curriculum_d1=True,
    maker_first_exit_timeout_ms=15000,
    exit_fallback_loss_bps=10.0,
    exit_fallback_mfe_giveback_bps=4.0,
    exit_fallback_thesis_decay_threshold=0.40
)

def mask_fn(env): return env.action_masks()

def restart_server():
    print("[SYSTEM] Force restarting bot-server to clear RAM...")
    os.system("taskkill /F /IM bot-server.exe 2>NUL")
    time.sleep(2)
    return subprocess.Popen([r"target\release\bot-server.exe"], stdout=subprocess.DEVNULL)

def audit_model(branch_name, model, venv_path, d1_enabled=False, maker_bonus=0.0):
    print(f"\n[AUDIT] Starting detailed audit for {branch_name}...")
    
    settings = V20B_BASE_CONFIG.copy()
    if d1_enabled:
        settings.update(D1_CURRICULUM_SETTINGS)
    
    def make_val_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_val",
            symbol="BTCUSDT",
            reward_exit_maker_bonus_weight=maker_bonus,
            **settings
        ), mask_fn)
    
    val_venv = DummyVecEnv([make_val_env])
    val_venv = VecNormalize.load(venv_path, val_venv)
    val_venv.training = False
    val_venv.norm_reward = False
    
    # Reset stats
    stats = {
        "trades": 0, "realized_pnl": 0.0, "fees_total": 0.0,
        "entry_fees": 0.0, "exit_fees": 0.0,
        "exit_maker": 0, "exit_taker": 0,
        "CLOSE_count": 0, "REDUCE_count": 0, "REDUCE_to_FLAT": 0,
        "pnl_long": 0.0, "pnl_short": 0.0,
        "done_reasons": {},
        "mfe_capture_sum": 0.0,
        "reach_2_bps": 0, "reach_4_bps": 0, "reach_6_bps": 0,
        "d1_fallbacks": 0, "d1_intent_count": 0, "d1_total_wait_ms": 0
    }
    
    obs = val_venv.reset()
    in_pos = False
    current_side = 0
    max_upnl = -999.0
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
        if info.get("exit_intent_active", 0): 
            stats["d1_intent_count"] += 1
            stats["d1_total_wait_ms"] += info.get("time_since_exit_intent_ms", 0)
        
        act_idx = int(action[0]) if isinstance(action, (np.ndarray, list)) else int(action)
        if act_idx == 4 or act_idx == 8: stats["CLOSE_count"] += 1
        if act_idx == 3 or act_idx == 7: stats["REDUCE_count"] += 1

        if info.get("trades_executed", 0) > 0:
            for fill in info.get("fills", []):
                price = fill.get('price', 0.0)
                qty = fill.get('qty', 0.0)
                l = fill.get('liquidity', '')
                f = fill.get('fee', 0.0)
                side = fill.get('side', '')
                stats["fees_total"] += abs(f)
                is_exit = (current_side > 0 and "Sell" in side) or (current_side < 0 and "Buy" in side)
                if is_exit:
                    stats["exit_fees"] += abs(f)
                    if "Maker" in l: stats["exit_maker"] += 1
                    else: stats["exit_taker"] += 1
                else:
                    stats["entry_fees"] += abs(f)
                    if not in_pos: stats["trades"] += 1
        
        if abs(pos_qty) > 1e-9:
            if not in_pos:
                in_pos = True
                current_side = 1 if pos_qty > 0 else -1
                max_upnl = 0.0
            max_upnl = max(max_upnl, upnl_bps)
        else:
            if in_pos:
                rpnl_delta = info.get("realized_pnl", 0.0) - stats["realized_pnl"]
                if current_side > 0: stats["pnl_long"] += rpnl_delta
                else: stats["pnl_short"] += rpnl_delta
                stats["realized_pnl"] = info.get("realized_pnl", 0.0)
                if max_upnl >= 2.0: stats["reach_2_bps"] += 1
                if max_upnl >= 4.0: stats["reach_4_bps"] += 1
                if max_upnl >= 6.0: stats["reach_6_bps"] += 1
                in_pos = False
                current_side = 0

        if done:
            reason = info.get("reason", "UNKNOWN")
            stats["done_reasons"][reason] = stats["done_reasons"].get(reason, 0) + 1
            if processed_steps > 30000: break
            obs = val_venv.reset()
            in_pos = False
            current_side = 0

    print(f"\n--- RESULTS FOR {branch_name} ---")
    print(f"Total Trades: {stats['trades']}")
    print(f"Realized PnL: {stats['realized_pnl']:.2f}, Net PnL: {stats['realized_pnl'] - stats['fees_total']:.2f}")
    print(f"Fees (Total/Exit): {stats['fees_total']:.2f} / {stats['exit_fees']:.2f}")
    print(f"Exits Maker/Taker: {stats['exit_maker']} / {stats['exit_taker']}")
    print(f"D1 Intent (steps): {stats['d1_intent_count']}, Fallbacks: {stats['d1_fallbacks']}")
    if stats['d1_intent_count'] > 0:
        print(f"Avg Wait-to-Exit: {stats['d1_total_wait_ms']/stats['d1_intent_count']:.1f}ms")
    print(f"Reasons: {stats['done_reasons']}")
    val_venv.close()
    gc.collect()

def run_experiment_branch(branch_name, d1_enabled=False, maker_bonus=0.0, steps=25000):
    print(f"\n\n>>> STARTING BRANCH: {branch_name} (MakerBonus={maker_bonus}) <<<")
    server_proc = restart_server()
    torch.cuda.empty_cache()
    gc.collect()
    out_dir = f"python/runs_train/abc_exit_shaping_v2/{branch_name}"
    os.makedirs(out_dir, exist_ok=True)
    base_model = "python/runs_train/training_v9_selective_v20b/model_v20b_final.zip"
    base_venv = "python/runs_train/training_v9_selective_v20b/venv_v20b_final.pkl"
    settings = V20B_BASE_CONFIG.copy()
    if d1_enabled: settings.update(D1_CURRICULUM_SETTINGS)

    def make_train_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            reward_exit_maker_bonus_weight=maker_bonus,
            **settings
        ), mask_fn)
    
    train_venv = DummyVecEnv([make_train_env])
    train_venv = VecNormalize.load(base_venv, train_venv)
    model = MaskablePPO.load(base_model, env=train_venv, device="cuda", custom_objects={"learning_rate": 5e-6})
    model.learn(total_timesteps=steps, progress_bar=True)
    model.save(os.path.join(out_dir, "model.zip"))
    train_venv.save(os.path.join(out_dir, "venv.pkl"))
    audit_model(branch_name, model, os.path.join(out_dir, "venv.pkl"), d1_enabled=d1_enabled, maker_bonus=maker_bonus)
    train_venv.close()
    server_proc.terminate()
    time.sleep(5)

if __name__ == "__main__":
    # Branch D1.1b: Curriculum + Redirection + Maker Bonus
    run_experiment_branch("Rama_D1_1b_Hybrid", d1_enabled=True, maker_bonus=0.5, steps=50000)
