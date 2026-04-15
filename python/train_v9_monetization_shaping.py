
import os
import sys
import argparse
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

# Selective Entry V2.0b (Balanced) Config
VNEXT_CONFIG = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.02, # Branch A (Winner)
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    micro_strict=False,
    
    # Selective Entry V2.0b (Balanced Thresholds)
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,
    long_veto_bb_pos_5m_threshold=0.40,
    long_veto_regime_dead_threshold=0.50,
)

def mask_fn(env):
    return env.action_masks()

def run_audit(model_path, venv_path, steps=30000, label=""):
    """Deep audit logic for checkpoint reports."""
    def make_env():
        return ActionMasker(GrpcTradingEnv(server_addr="localhost:50051", 
                                          dataset_id="golden_l2_v1_val",
                                          symbol="BTCUSDT",
                                          fill_model=2,
                                          **VNEXT_CONFIG), mask_fn)
    
    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False
    venv.norm_reward = False
    
    model = MaskablePPO.load(model_path)
    obs = venv.reset()
    
    total_trades = 0
    longs = 0
    shorts = 0
    never_reach_05 = 0
    current_trade_max_upnl = -999.0
    in_pos = False
    
    flow_vetos = 0
    bb_vetos = 0
    dead_vetos = 0
    maker_exits = 0
    taker_exits = 0
    pnl_long = 0.0
    pnl_short = 0.0
    
    last_realized = 0.0
    
    for i in range(steps):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, done, info_list = venv.step(action)
        info = info_list[0]
        
        pos_qty = info.get("position_qty", 0.0)
        upnl_bps = info.get("unrealized_pnl", 0.0) / info.get("equity", 10000.0) * 10000.0
        realized = info.get("realized_pnl", 0.0)
        
        flow_vetos += info.get("veto_long_flow_count", 0)
        bb_vetos += info.get("veto_long_bb_count", 0)
        dead_vetos += info.get("veto_long_dead_regime_count", 0)

        trades_this_step = info.get("trades_executed", 0)
        if trades_this_step > 0:
            for fill in info.get("fills", []):
                q = fill.get('qty', 0.0) if isinstance(fill, dict) else getattr(fill, 'qty', 0.0)
                s = fill.get('side', '') if isinstance(fill, dict) else getattr(fill, 'side', '')
                liqi = fill.get('liquidity', '') if isinstance(fill, dict) else getattr(fill, 'liquidity', '')
                f = fill.get('fee', 0.0) if isinstance(fill, dict) else getattr(fill, 'fee', 0.0)
                
                if q > 0 and not in_pos:
                    total_trades += 1
                    if s in ["Buy", "Side_Buy"]: longs += 1
                    else: shorts += 1
                elif q > 0 and in_pos and abs(pos_qty) < 1e-8:
                    if "Maker" in liqi: maker_exits += 1
                    else: taker_exits += 1
        
        if abs(pos_qty) > 1e-8:
            in_pos = True
            current_trade_max_upnl = max(current_trade_max_upnl, upnl_bps)
            pnl_delta = realized - last_realized
            # Tracking pnl per side (approx based on active side)
            if pos_qty > 0: pnl_long += pnl_delta
            else: pnl_short += pnl_delta
        else:
            if in_pos:
                if current_trade_max_upnl < 0.5: never_reach_05 += 1
                current_trade_max_upnl = -999.0
            in_pos = False
        
        last_realized = realized
        if done: break

    realized_total = info.get('realized_pnl', 0.0)
    fees_total = info.get('fees_paid', 0.0)
    rust_acts = info.get('action_counts', {})
    
    print(f"\n--- MILESTONE AUDIT: {label} ---")
    print(f" 1) Total Trades              : {total_trades}")
    print(f" 2) LONG vs SHORT Trades      : {longs} L / {shorts} S")
    print(f" 3) Realized PnL              : {realized_total:.2f}")
    print(f" 4) Net PnL After Fees        : {realized_total - fees_total:.2f}")
    print(f" 5) Fees Total                : {fees_total:.2f}")
    print(f" 6) PnL LONG / SHORT          : {pnl_long:.2f} / {pnl_short:.2f}")
    print(f" 7) % Never Reach +0.5 bps    : {never_reach_05/total_trades*100:.1f}%" if total_trades > 0 else "N/A")
    print(f" 8) MFE Capture Ratio         : TBD (N/A in mini-report)")
    print(f" 9) Exit Maker / Taker        : {maker_exits} / {taker_exits}")
    print(f" 10) CLOSE Usage              : {rust_acts.get('CLOSE_LONG', 0) + rust_acts.get('CLOSE_SHORT', 0)}")
    print(f" 11) REDUCE Usage             : {rust_acts.get('REDUCE_LONG', 0) + rust_acts.get('REDUCE_SHORT', 0)}")
    print(f" 12) Dangling Position        : {pos_qty:.8f}")
    print(f" 13-15) Vetos (F/B/D)         : {flow_vetos} / {bb_vetos} / {dead_vetos}")
    print(f" 16) % LONG Vetados           : {flow_vetos+bb_vetos+dead_vetos} total")
    print(f" 17) Done Reason              : {info.get('reason', 'UNKNOWN')}")
    print(f"----------------------------------------\n")

class TrainingMilestoneCallback(BaseCallback):
    def __init__(self, milestones, out_dir):
        super().__init__()
        self.milestones = sorted(milestones)
        self.out_dir = out_dir
        self.next_idx = 0

    def _on_step(self):
        if self.next_idx < len(self.milestones):
            if self.num_timesteps >= self.milestones[self.next_idx]:
                m = self.milestones[self.next_idx]
                self.next_idx += 1
                label = f"{m // 1000}k"
                print(f"\n[TRAIN] Reached {label} milestone. Saving and auditing...")
                
                model_path = os.path.join(self.out_dir, f"model_{label}.zip")
                self.model.save(model_path)
                
                venv_path = os.path.join(self.out_dir, f"venv_{label}.pkl")
                self.model.get_env().save(venv_path)
                
                run_audit(model_path, venv_path, label=label)
        return True

def main():
    out_dir = "python/runs_train/training_v9_selective_v20b"
    os.makedirs(out_dir, exist_ok=True)
    
    # RESTART FROM BASE MODEL_A (0.02)
    base_model_path = "python/runs_train/calibration_v9/branch_A/model_A.zip"
    base_venv_path = "python/runs_train/calibration_v9/branch_A/venv_A.pkl"
    
    def make_train_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            fill_model=2,
            random_start_offset=True,
            **VNEXT_CONFIG
        ), mask_fn)
    
    venv = DummyVecEnv([make_train_env for _ in range(4)])
    venv = VecNormalize.load(base_venv_path, venv)
    
    model = MaskablePPO.load(base_model_path, env=venv, device="cuda" if torch.cuda.is_available() else "cpu", 
                             custom_objects={"learning_rate": 2e-6})
    
    callback = TrainingMilestoneCallback(milestones=[25000, 50000, 100000], out_dir=out_dir)
    
    print(f"[TRAIN] Starting V2.0b (Balanced) +100k training run...")
    model.learn(total_timesteps=100000, callback=callback, progress_bar=True)
    
    model.save(os.path.join(out_dir, "model_v20b_final.zip"))
    venv.save(os.path.join(out_dir, "venv_v20b_final.pkl"))

if __name__ == "__main__":
    main()
