
import os
import sys
import torch
import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from bot_ml.grpc_env import GrpcTradingEnv

VNEXT_CONFIG = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.1,
    use_winner_unlock=True,
    use_selective_entry=False,
    reward_thesis_decay_weight=0.0001,
    micro_strict=False,
)

def mask_fn(env):
    return env.action_masks()

def run_validation(model_path, venv_path, dataset_id, steps=200000):
    print(f"\n{'='*60}")
    print(f" VALIDATION: {dataset_id}")
    print(f"{'='*60}")
    
    def make_env():
        return ActionMasker(GrpcTradingEnv(server_addr="localhost:50051", 
                                          dataset_id=dataset_id,
                                          symbol="BTCUSDT",
                                          fill_model=2,
                                          **VNEXT_CONFIG), mask_fn)
    
    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False
    venv.norm_reward = False
    
    model = MaskablePPO.load(model_path)
    obs = venv.reset()
    
    all_trades = []
    current_trade = None
    last_realized = 0.0
    
    pnl_by_side = {"LONG": 0.0, "SHORT": 0.0}
    
    for i in range(steps):
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
                
                # Update side PnL
                s = current_trade["side"].upper()
                if "BUY" in s or "LONG" in s: pnl_by_side["LONG"] += pnl_delta
                else: pnl_by_side["SHORT"] += pnl_delta
                
                current_trade = None
        
        last_realized = realized
        if done: break

    # Summary
    num_t = len(all_trades)
    avg_mfe = np.mean([t["max_upnl_bps"] for t in all_trades]) if num_t > 0 else 0.0
    captures = [t["realized_bps"] / t["max_upnl_bps"] for t in all_trades if t["max_upnl_bps"] > 0.1]
    avg_capture = np.mean(captures) if captures else 0.0
    
    rust_acts = info.get('action_counts', {})
    
    print(f"\n--- VALIDATION SCORECARD: {dataset_id} ---")
    print(f"Total Trades:      {num_t}")
    print(f"Realized PnL:      {info.get('realized_pnl', 0.0):.4f}")
    print(f"Net PnL (Fees):    {info.get('realized_pnl', 0.0) - info.get('fees_paid', 0.0):.4f}")
    print(f"Fees Total:        {info.get('fees_paid', 0.0):.4f}")
    print(f"Avg Realized/Tr:   {info.get('realized_pnl', 0.0)/num_t:.4f}" if num_t > 0 else "N/A")
    print(f"MFE Capture Ratio: {avg_capture:.2%}")
    
    # Per side
    print(f"PnL LONG:          {pnl_by_side['LONG']:.2f}")
    print(f"PnL SHORT:         {pnl_by_side['SHORT']:.2f}")
    
    # Reach
    if num_t > 0:
        r2 = len([t for t in all_trades if t["max_upnl_bps"] >= 2.0]) / num_t
        r4 = len([t for t in all_trades if t["max_upnl_bps"] >= 4.0]) / num_t
        r6 = len([t for t in all_trades if t["max_upnl_bps"] >= 6.0]) / num_t
        print(f"Reach +2/+4/+6:    {r2*100:.1f}% / {r4*100:.1f}% / {r6*100:.1f}%")

    # Rust Telemetry
    print(f"CLOSE Usage:       {rust_acts.get('CLOSE_LONG', 0) + rust_acts.get('CLOSE_SHORT', 0)}")
    print(f"REDUCE Usage:      {rust_acts.get('REDUCE_LONG', 0) + rust_acts.get('REDUCE_SHORT', 0)}")
    print(f"REDUCE_to_FLAT:    {rust_acts.get('REDUCE_TO_FLAT', 0)}")
    print(f"Blocked Partial:   {rust_acts.get('BLOCKED_PARTIAL_REDUCE', 0)}")
    
    print(f"Flat at Done:      {abs(pos_qty) < 1e-8}")
    print(f"Dangling Size:     {pos_qty:.6f}")
    print(f"Done Reason:       {info.get('reason')}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    model_path = "python/runs_train/monetization_v9/model_100k.zip"
    venv_path = "python/runs_train/monetization_v9/venv_100k.pkl"
    
    run_validation(model_path, venv_path, "golden_l2_v1_val")
