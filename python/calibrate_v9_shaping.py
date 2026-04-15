
import os
import sys
import torch
import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from bot_ml.grpc_env import GrpcTradingEnv

def mask_fn(env):
    return env.action_masks()

BASE_CONFIG = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    use_winner_unlock=True,
    use_selective_entry=False,
    reward_thesis_decay_weight=0.0001,
    micro_strict=False,
)

def run_evaluation(model_path, venv_path, dataset_id, weight, steps=100000):
    """Evaluation helper for out-of-sample reporting."""
    config = BASE_CONFIG.copy()
    config["reward_trailing_mfe_penalty_weight"] = weight
    
    def make_env():
        return ActionMasker(GrpcTradingEnv(server_addr="localhost:50051", 
                                          dataset_id=dataset_id,
                                          symbol="BTCUSDT",
                                          fill_model=2,
                                          **config), mask_fn)
    
    venv = DummyVecEnv([make_env])
    # Load venv stats from training
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False
    venv.norm_reward = False
    
    model = MaskablePPO.load(model_path)
    obs = venv.reset()
    
    all_trades = []
    current_trade = None
    last_realized = 0.0
    pnl_by_side = {"LONG": 0.0, "SHORT": 0.0}
    
    for _ in range(steps):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, done, info_list = venv.step(action)
        info = info_list[0]
        
        pos_qty = info.get("position_qty", 0.0)
        equity = info.get("equity", 10000.0)
        upnl = info.get("unrealized_pnl", 0.0)
        upnl_bps = upnl / equity * 10000.0
        realized = info.get("realized_pnl", 0.0)
        side_str = info.get("position_side", "UNKNOWN")
        
        if abs(pos_qty) > 1e-8:
            if current_trade is None:
                current_trade = {"max_upnl_bps": upnl_bps, "entry_equity": equity, "side": side_str}
            else:
                current_trade["max_upnl_bps"] = max(current_trade["max_upnl_bps"], upnl_bps)
        else:
            if current_trade is not None:
                pnl_delta = realized - last_realized
                current_trade["realized_bps"] = pnl_delta / current_trade["entry_equity"] * 10000.0
                all_trades.append(current_trade)
                s = current_trade["side"].upper()
                if "BUY" in s or "LONG" in s: pnl_by_side["LONG"] += pnl_delta
                else: pnl_by_side["SHORT"] += pnl_delta
                current_trade = None
        
        last_realized = realized
        if done: break

    num_t = len(all_trades)
    captures = [t["realized_bps"] / t["max_upnl_bps"] for t in all_trades if t["max_upnl_bps"] > 0.1]
    avg_capture = np.mean(captures) if captures else 0.0
    rust_acts = info.get('action_counts', {})
    
    report = {
        "trades": num_t,
        "net_pnl": info.get('realized_pnl', 0.0) - info.get('fees_paid', 0.0),
        "realized_pnl": info.get('realized_pnl', 0.0),
        "fees": info.get('fees_paid', 0.0),
        "capture": avg_capture,
        "close_usage": rust_acts.get('CLOSE_LONG', 0) + rust_acts.get('CLOSE_SHORT', 0),
        "reduce_usage": rust_acts.get('REDUCE_LONG', 0) + rust_acts.get('REDUCE_SHORT', 0),
        "dangling": pos_qty,
        "reason": info.get('reason'),
        "pnl_long": pnl_by_side["LONG"],
        "pnl_short": pnl_by_side["SHORT"]
    }
    return report

def train_branch(label, weight, steps=25000):
    print(f"\n--- BRANCH {label}: Weight {weight} ---")
    out_dir = f"python/runs_train/calibration_v9/branch_{label}"
    os.makedirs(out_dir, exist_ok=True)
    
    base_model_path = "python/runs_train/itr_causal_chk/model_chk_100000.zip"
    base_venv_path = "python/runs_train/itr_causal_chk/venv_chk_100000.pkl"
    
    config = BASE_CONFIG.copy()
    config["reward_trailing_mfe_penalty_weight"] = weight
    
    def make_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            fill_model=2,
            random_start_offset=True,
            **config
        ), mask_fn)
    
    venv = DummyVecEnv([make_env for _ in range(4)])
    venv = VecNormalize.load(base_venv_path, venv)
    
    model = MaskablePPO.load(base_model_path, env=venv, device="cuda" if torch.cuda.is_available() else "cpu",
                            custom_objects={"learning_rate": 2e-6, "ent_coef": 0.01})
    
    model.learn(total_timesteps=steps)
    
    m_path = os.path.join(out_dir, f"model_{label}.zip")
    v_path = os.path.join(out_dir, f"venv_{label}.pkl")
    model.save(m_path)
    venv.save(v_path)
    
    # Evaluate Out-of-Sample
    val_report = run_evaluation(m_path, v_path, "golden_l2_v1_val", weight)
    return val_report

if __name__ == "__main__":
    weights = {"A": 0.02, "B": 0.05, "C": 0.10}
    results = {}
    for label, w in weights.items():
        results[label] = train_branch(label, w)
    
    print("\n" + "="*80)
    print(" FINAL CALIBRATION RESULTS (Out-of-Sample)")
    print("="*80)
    print(f"{'Branch':<8} | {'Weight':<8} | {'Net PnL':<10} | {'Capture':<10} | {'Fees':<10} | {'CLOSE':<6} | {'Reason'}")
    print("-" * 80)
    for label, res in results.items():
        print(f"{label:<8} | {weights[label]:<8.2f} | {res['net_pnl']:<10.2f} | {res['capture']:<10.2%} | {res['fees']:<10.2f} | {res['close_usage']:<6} | {res['reason']}")
    print("="*80)
