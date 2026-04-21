"""
Train maker exit v2.
Objetivo: igual que v1, pero ahora con el curriculum ARREGLADO en el servidor.
- exit_intent_ts se setea correctamente al elegir ReduceLong/CloseLong
- auto-fallback dispara market exit si timeout/loss/giveback
- submit_passive_order bypasea entry gates para exits
Base: v20b_final (mismo que v1, porque v1 fue entrenado con curriculum roto)
"""
import os, sys, gc, time, subprocess, json, datetime
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from bot_ml.grpc_env import GrpcTradingEnv

BASE_MODEL = "python/runs_train/training_v9_selective_v20b/model_v20b_final.zip"
BASE_VENV  = "python/runs_train/training_v9_selective_v20b/venv_v20b_final.pkl"
OUT_DIR    = "python/runs_train/maker_exit_v2"
TRAIN_STEPS = 50000
LR = 5e-6

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
    reward_exit_maker_bonus_weight=0.05,
    reward_exit_taker_penalty_weight=0.03,
)

def mask_fn(env): return env.action_masks()

def start_server():
    os.system("taskkill //F //IM bot-server.exe 2>NUL")
    time.sleep(2)
    proc = subprocess.Popen(["target\\release\\bot-server.exe"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc

def make_env(dataset_id):
    return ActionMasker(
        GrpcTradingEnv(server_addr="localhost:50051", dataset_id=dataset_id,
                       symbol="BTCUSDT", **CFG),
        mask_fn)

def quick_audit(model, venv_path, label):
    print(f"\n[AUDIT] {label}")
    cfg_audit = {**CFG, "reward_exit_maker_bonus_weight": 0.0,
                         "reward_exit_taker_penalty_weight": 0.0}
    def make_val():
        return ActionMasker(
            GrpcTradingEnv(server_addr="localhost:50051",
                           dataset_id="golden_l2_v1_val",
                           symbol="BTCUSDT", **cfg_audit), mask_fn)

    venv = DummyVecEnv([make_val])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False; venv.norm_reward = False

    trades = exit_maker = exit_taker = d1_fallbacks = 0
    in_pos = False; current_side = 0
    obs = venv.reset()

    for _ in range(35000):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, _, done_arr, info_list = venv.step(action)
        info = info_list[0]

        if info.get("exit_fallback_triggered", 0): d1_fallbacks += 1

        for fill in info.get("fills", []):
            side = fill.get("side", "")
            is_exit = (current_side > 0 and "Sell" in side) or \
                      (current_side < 0 and "Buy" in side)
            if not is_exit and not in_pos: trades += 1
            if is_exit:
                if "Maker" in fill.get("liquidity", ""): exit_maker += 1
                else: exit_taker += 1

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

    fees = info.get("fees_paid", 0.0)
    rpnl = info.get("realized_pnl", 0.0)
    total_exits = exit_maker + exit_taker
    maker_pct = exit_maker / total_exits * 100 if total_exits > 0 else 0
    print(f"  Trades={trades}  Maker%={maker_pct:.0f}%  ({exit_maker}M/{exit_taker}T)"
          f"  rPnL={rpnl:+.2f}  fees={fees:.2f}  NetPnL={rpnl-fees:+.2f}"
          f"  D1fall={d1_fallbacks}")
    venv.close(); gc.collect()
    return {"trades": trades, "maker_pct": round(maker_pct,1),
            "exit_maker": exit_maker, "exit_taker": exit_taker,
            "net_pnl": round(rpnl - fees, 2), "d1_fallbacks": d1_fallbacks}

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    server = start_server()

    try:
        train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
        train_venv = VecNormalize.load(BASE_VENV, train_venv)
        model = MaskablePPO.load(BASE_MODEL, env=train_venv, device="cuda",
                                 custom_objects={"learning_rate": LR})

        print(f"[TRAIN] {TRAIN_STEPS} steps — curriculum FIXED, bonus=0.05, penalty=0.03")
        ckpts = [10000, 25000, 50000]
        trained = 0
        for ckpt in ckpts:
            model.learn(total_timesteps=ckpt - trained, progress_bar=True,
                        reset_num_timesteps=False)
            trained = ckpt
            path = os.path.join(OUT_DIR, f"model_{ckpt//1000}k.zip")
            venv_path = os.path.join(OUT_DIR, f"venv_{ckpt//1000}k.pkl")
            model.save(path); train_venv.save(venv_path)
            train_venv.close()
            print(f"[SAVED] {path}")

            server.terminate(); time.sleep(3); server = start_server()
            quick_audit(model, venv_path, f"checkpoint {ckpt//1000}k")

            if ckpt < ckpts[-1]:
                server.terminate(); time.sleep(3); server = start_server()
                train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
                train_venv = VecNormalize.load(venv_path, train_venv)
                model.set_env(train_venv)

    finally:
        server.terminate()
