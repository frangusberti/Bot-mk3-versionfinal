"""
Train maker exit v5.
Parte de v4_75k (mejor checkpoint de v4).
Key change: exit_fallback_thesis_decay_threshold=2.0 (era 0.40, demasiado agresivo)
Esto permite que la orden pasiva tenga mas tiempo de llenar antes de fallback.
Resultado esperado: 35-40% maker exits, Net PnL cerca de break-even o positivo.
"""
import os, sys, gc, time, subprocess, json
os.chdir(r"C:\Bot mk3")
sys.path.insert(0, 'python')
sys.path.insert(0, 'python/bot_ml')

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from bot_ml.grpc_env import GrpcTradingEnv

BASE_MODEL = r"C:\Bot mk3\python\runs_train\maker_exit_v4\model_75k.zip"
BASE_VENV  = r"C:\Bot mk3\python\runs_train\maker_exit_v4\venv_75k.pkl"
OUT_DIR    = r"C:\Bot mk3\python\runs_train\maker_exit_v5"
LR = 2e-6

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
    exit_fallback_thesis_decay_threshold=2.0,   # KEY: was 0.40, now 2.0
    maker_first_exit_timeout_ms=8000,
    exit_maker_pricing_multiplier=1.0,
    reward_exit_maker_bonus_weight=0.05,
    reward_exit_taker_penalty_weight=0.03,
)

def mask_fn(env): return env.action_masks()

def start_server():
    os.system("taskkill //F //IM bot-server.exe 2>NUL")
    time.sleep(2)
    proc = subprocess.Popen([r"C:\Bot mk3\target\release\bot-server.exe"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc

def make_env(dataset_id):
    return ActionMasker(
        GrpcTradingEnv("localhost:50051", dataset_id=dataset_id, symbol="BTCUSDT", **CFG), mask_fn)

def quick_audit(model, venv_path, label):
    print(f"\n[AUDIT] {label}")
    cfg_a = {**CFG, "reward_exit_maker_bonus_weight": 0.0, "reward_exit_taker_penalty_weight": 0.0}
    def make_val():
        return ActionMasker(GrpcTradingEnv("localhost:50051", dataset_id="golden_l2_v1_val", symbol="BTCUSDT", **cfg_a), mask_fn)
    venv = DummyVecEnv([make_val])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False; venv.norm_reward = False
    trades = em = et = fb = 0; in_pos = False; cs = 0
    obs = venv.reset()
    for _ in range(35000):
        masks = venv.env_method("action_masks")[0]
        act, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, _, done, info_list = venv.step(act)
        info = info_list[0]
        if info.get("exit_fallback_triggered", 0): fb += 1
        for fill in info.get("fills", []):
            s = fill.get("side",""); l = fill.get("liquidity","")
            is_exit = (cs>0 and "Sell" in s) or (cs<0 and "Buy" in s)
            if not is_exit and not in_pos: trades += 1
            if is_exit:
                if "Maker" in l: em += 1
                else: et += 1
        pos = info.get("position_qty", 0.0)
        if abs(pos)>1e-9:
            if not in_pos: in_pos=True; cs=1 if pos>0 else -1
        else:
            if in_pos: in_pos=False; cs=0
        if done[0]: obs=venv.reset(); in_pos=False; cs=0
    fees = info.get("fees_paid",0.0); rpnl = info.get("realized_pnl",0.0)
    tot = em+et; mk = em/tot*100 if tot>0 else 0
    print(f"  Trades={trades}({trades/35.0:.1f}/1k)  Maker={mk:.0f}%  ({em}M/{et}T)  Net={rpnl-fees:+.2f}  D1={fb}")
    venv.close(); gc.collect()
    return {"trades": trades, "maker_pct": round(mk,1), "net_pnl": round(rpnl-fees,2), "d1": fb}

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    server = start_server()
    try:
        train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
        train_venv = VecNormalize.load(BASE_VENV, train_venv)
        model = MaskablePPO.load(BASE_MODEL, env=train_venv, device="cuda",
                                 custom_objects={"learning_rate": LR})

        print(f"[TRAIN] v5: 100k steps from v4_75k, thesis_decay_threshold=2.0, LR={LR}")
        ckpts = [25000, 50000, 75000, 100000]
        trained = 0
        for ckpt in ckpts:
            model.learn(total_timesteps=ckpt - trained, progress_bar=True,
                        reset_num_timesteps=False)
            trained = ckpt
            path = f"{OUT_DIR}/model_{ckpt//1000}k.zip"
            venv_path = f"{OUT_DIR}/venv_{ckpt//1000}k.pkl"
            model.save(path); train_venv.save(venv_path)
            train_venv.close()
            print(f"[SAVED] {path}")

            server.terminate(); time.sleep(3); server = start_server()
            quick_audit(model, venv_path, f"v5_{ckpt//1000}k")

            if ckpt < ckpts[-1]:
                server.terminate(); time.sleep(3); server = start_server()
                train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
                train_venv = VecNormalize.load(venv_path, train_venv)
                model.set_env(train_venv)
    finally:
        server.terminate()
