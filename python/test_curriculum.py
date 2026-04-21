"""
Quick test: verify exit curriculum produces maker exits with new binary.
Uses v20b_final + curriculum config, runs 5k steps on val dataset.
"""
import os, sys, time, subprocess, gc
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from bot_ml.grpc_env import GrpcTradingEnv

BASE_MODEL = "python/runs_train/training_v9_selective_v20b/model_v20b_final.zip"
BASE_VENV  = "python/runs_train/training_v9_selective_v20b/venv_v20b_final.pkl"

CFG = dict(
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
    exit_fallback_loss_bps=10.0,
    exit_fallback_mfe_giveback_bps=4.0,
    exit_fallback_thesis_decay_threshold=0.40,
    maker_first_exit_timeout_ms=8000,
    exit_maker_pricing_multiplier=1.0,
    reward_exit_maker_bonus_weight=0.0,
    reward_exit_taker_penalty_weight=0.0,
)

def mask_fn(env): return env.action_masks()

def start_server():
    os.system("taskkill //F //IM bot-server.exe 2>NUL")
    time.sleep(2)
    proc = subprocess.Popen(["target\\release\\bot-server.exe"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc

if __name__ == "__main__":
    server = start_server()
    try:
        def make_env():
            return ActionMasker(
                GrpcTradingEnv(server_addr="localhost:50051",
                               dataset_id="golden_l2_v1_val",
                               symbol="BTCUSDT", **CFG), mask_fn)

        venv = DummyVecEnv([make_env])
        venv = VecNormalize.load(BASE_VENV, venv)
        venv.training = False; venv.norm_reward = False

        model = MaskablePPO.load(BASE_MODEL, env=venv, device="cpu")

        trades = exit_maker = exit_taker = d1_fallbacks = intent_steps = 0
        in_pos = False; current_side = 0
        obs = venv.reset()

        STEPS = 10000
        for step in range(STEPS):
            masks = venv.env_method("action_masks")[0]
            action, _ = model.predict(obs, deterministic=True, action_masks=masks)
            obs, _, done_arr, info_list = venv.step(action)
            info = info_list[0]

            if info.get("exit_fallback_triggered", 0): d1_fallbacks += 1
            if info.get("exit_intent_active", 0): intent_steps += 1

            for fill in info.get("fills", []):
                side = fill.get("side", "")
                liq  = fill.get("liquidity", "")
                is_exit = (current_side > 0 and "Sell" in side) or \
                          (current_side < 0 and "Buy" in side)
                if not is_exit and not in_pos: trades += 1
                if is_exit:
                    if "Maker" in liq: exit_maker += 1
                    else:              exit_taker += 1

            pos = info.get("position_qty", 0.0)
            if abs(pos) > 1e-9:
                if not in_pos:
                    in_pos = True
                    current_side = 1 if pos > 0 else -1
            else:
                if in_pos:
                    in_pos = False; current_side = 0

            if done_arr[0]:
                obs = venv.reset(); in_pos = False; current_side = 0

        total_exits = exit_maker + exit_taker
        maker_pct = exit_maker / total_exits * 100 if total_exits > 0 else 0
        print(f"\n=== CURRICULUM TEST ({STEPS} steps) ===")
        print(f"  Trades:      {trades}")
        print(f"  Exits:       {total_exits}  ({exit_maker}M / {exit_taker}T)")
        print(f"  Maker%:      {maker_pct:.0f}%")
        print(f"  D1 fallbacks:{d1_fallbacks}")
        print(f"  Intent steps:{intent_steps}")
        if maker_pct > 0:
            print("  ✓ CURRICULUM WORKING - maker exits present!")
        elif d1_fallbacks > 0:
            print("  ~ Fallbacks firing but no maker fills yet (fill model?)")
        else:
            print("  ✗ Still 0% maker exits - check logic")

        venv.close()
    finally:
        server.terminate()
