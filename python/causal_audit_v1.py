"""
Causal Audit v1: Entry vs Exit
==============================
Tracks every trade lifecycle to separate 'Bad Entry' (no profit reached) 
from 'Bad Exit' (profit reached but wasted).
"""
import os
import sys
import torch
import numpy as np
from collections import defaultdict, Counter
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from sb3_contrib.common.wrappers import ActionMasker

# -- Config --
CONFIG = dict(
    dataset_id="golden_l2_v1_train",
    symbol="BTCUSDT",
    random_start_offset=True,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    fill_model=2,
)

def mask_fn(env: GrpcTradingEnv) -> np.ndarray:
    return env.action_masks()

def run_causal_audit(model, env, steps=10000):
    obs = env.reset()
    
    current_trade = None
    all_trades = []
    
    # 3) REDUCE Semantics
    reduce_qty_deltas = []
    reduce_leading_to_flat = 0
    reduce_total_attempts = 0

    for i in range(steps):
        masks = env.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        act_int = int(action[0])
        
        # Capture state BEFORE action
        # env.env_method("get_env_state") might be slow, we'll use info from previous step
        # but for the VERY FIRST step we need a reset info or similar.
        # GrpcTradingEnv doesn't expose state directly as a method easily without editing it.
        # We'll rely on info from the step.
        
        obs, reward, done, info = env.step(action)
        info0 = info[0]
        
        mid = info0['mid_price']
        pos_qty = info0['position_qty'] # Signed
        entry_price = info0['entry_price']
        
        # -- Track Trade Lifecycle --
        if abs(pos_qty) > 1e-9:
            if current_trade is None:
                # Started NEW trade
                current_trade = {
                    "start_step": i,
                    "side": "LONG" if pos_qty > 0 else "SHORT",
                    "entry_price": entry_price,
                    "entry_qty": abs(pos_qty),
                    "mfe_bps": 0.0,
                    "mae_bps": 0.0,
                    "reached": {0.5: False, 1.0: False, 2.0: False, 4.0: False},
                    "first_exit_effort": None,
                    "qty_history": [abs(pos_qty)],
                    "ts_start": info0['ts']
                }
            else:
                current_trade["qty_history"].append(abs(pos_qty))
            
            # Update MFE / MAE
            if current_trade["side"] == "LONG":
                pnl_bps = (mid - current_trade["entry_price"]) / current_trade["entry_price"] * 10000.0
            else:
                pnl_bps = (current_trade["entry_price"] - mid) / current_trade["entry_price"] * 10000.0
            
            current_trade["mfe_bps"] = max(current_trade["mfe_bps"], pnl_bps)
            current_trade["mae_bps"] = min(current_trade["mae_bps"], pnl_bps)
            
            for thresh in [0.5, 1.0, 2.0, 4.0]:
                if current_trade["mfe_bps"] >= thresh:
                    current_trade["reached"][thresh] = True

            # -- Track first exit attempt --
            if (act_int in {3, 4, 7, 8}) and current_trade["first_exit_effort"] is None:
                current_trade["first_exit_effort"] = {
                    "step": i,
                    "act": act_int,
                    "pnl_bps": pnl_bps,
                    "blocked": info0.get("gate_close_blocked", 0) > 0
                }
        else:
            if current_trade is not None:
                # Trade CLOSED
                current_trade["end_step"] = i
                all_trades.append(current_trade)
                current_trade = None

        # Add the last open trade if any
        if current_trade:
            all_trades.append(current_trade)

        # -- REDUCE Semantics Audit --
        if act_int in {3, 7}: # REDUCE
            reduce_total_attempts += 1
            # We check if pos_qty goes to zero in the NEXT step info?
            # Actually, if we just finished the trade, we already handled it.
            if abs(pos_qty) < 1e-9:
                reduce_leading_to_flat += 1
            
            # How much did it reduce?
            if len(info0.get("fills", [])) > 0:
                # If there were fills this step
                for fill in info0['fills']:
                    # We can't easily tell which fill belongs to REDUCE here if there are multiple
                    # but usually there's only one.
                    pass

    return all_trades, {
        "reduce_total": reduce_total_attempts,
        "reduce_flats": reduce_leading_to_flat
    }

def main():
    model_path = "python/runs_train/itr_serious_50k/model_itr_serious.zip"
    venv_path = "python/runs_train/itr_serious_50k/venv_itr_serious.pkl"
    
    model = MaskablePPO.load(model_path)
    
    def make_env():
        env = GrpcTradingEnv(server_addr="localhost:50051", **CONFIG)
        return ActionMasker(env, mask_fn)

    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False
    
    print("[CAUSAL AUDIT] Running 15000 steps...")
    trades, red_stats = run_causal_audit(model, venv, steps=15000)
    
    if not trades:
        print("No trades found in audit window.")
        return

    num_trades = len(trades)
    reach_05 = sum(1 for t in trades if t['reached'][0.5])
    reach_10 = sum(1 for t in trades if t['reached'][1.0])
    reach_20 = sum(1 for t in trades if t['reached'][2.0])
    reach_40 = sum(1 for t in trades if t['reached'][4.0])
    
    avg_mfe = np.mean([t['mfe_bps'] for t in trades])
    avg_mae = np.mean([t['mae_bps'] for t in trades])
    
    first_exit_pnl = [t['first_exit_effort']['pnl_bps'] for t in trades if t['first_exit_effort']]
    first_exit_in_red = sum(1 for p in first_exit_pnl if p < 0)
    
    print("\n" + "="*60)
    print("           CAUSAL AUDIT REPORT: ENTRY VS EXIT")
    print("="*60)
    print(f"1) Trade Health / Excursion (N={num_trades})")
    print(f"   Avg MFE:            {avg_mfe:.2f} bps")
    print(f"   Avg MAE:            {avg_mae:.2f} bps")
    print(f"   Threshold Reach Rate:")
    print(f"     > +0.5 bps:       {reach_05/num_trades*100:.1f}%")
    print(f"     > +1.0 bps:       {reach_10/num_trades*100:.1f}%")
    print(f"     > +2.0 bps:       {reach_20/num_trades*100:.1f}%")
    print(f"     > +4.0 bps:       {reach_40/num_trades*100:.1f}%")
    
    print("-" * 20)
    print("2) First Exit Intent Strategy")
    if first_exit_pnl:
        print(f"   Avg PnL at Attempt: {np.mean(first_exit_pnl):.2f} bps")
        print(f"   Attempts in RED:    {first_exit_in_red} / {len(first_exit_pnl)} ({first_exit_in_red/len(first_exit_pnl)*100:.1f}%)")
    else:
        print("   No exit attempts recorded.")

    print("-" * 20)
    print("3) REDUCE Semantics Audit")
    print(f"   Total REDUCE actions: {red_stats['reduce_total']}")
    print(f"   REDUCE leading to flat: {red_stats['reduce_flats']} ({red_stats['reduce_flats']/(red_stats['reduce_total'] or 1)*100:.1f}%)")
    print(f"   Observation: Under current logic REDUCE(qty*0.5) cannot reach 0.0 mathematically.")

    print("-" * 20)
    print("4) CAUSAL VERDICT")
    is_bad_entry = (reach_05 / num_trades) < 0.4
    if is_bad_entry:
        print("   [CAUSAL] Primary: ENTRADA MALA. >60% de trades nunca llegan a +0.5 bps.")
    else:
        print("   [CAUSAL] Primary: SALIDA MALA (Lifecycle). >40% llegan a profit pero se desperdician.")
        
    if first_exit_in_red / num_trades > 0.5:
        print("   [DEBUG] El agente intenta salir prematuramente estando en negativo.")
    
    print("="*60)

if __name__ == "__main__":
    main()
