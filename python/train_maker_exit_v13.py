"""
Train maker exit v13 — Curriculum: force shorts first, then bidirectional.

DIAGNOSTICO v12:
  - LR=1e-5, sin short veto -> aun 0 shorts en v12_100k
  - Root cause: PPO es on-policy, nunca samplea OPEN_SHORT porque su prior es ~0
  - Sin experiencias de short en el rollout buffer, no hay gradiente para shorts
  - Necesitamos FORZAR al modelo a tomar shorts para generar esas experiencias

CURRICULUM de 2 fases:
  Phase 1 (0-100k steps): SHORT-ONLY
    - Mask fuerza OPEN_LONG = 0 (imposible ir long)
    - Modelo TIENE QUE tomar shorts o quedarse en HOLD
    - Genera experiencias de short para que PPO aprenda
    - LR=5e-6 (agresivo para aprender rapido)
    - ent_coef=0.02

  Phase 2 (100k-400k steps): BIDIRECCIONAL
    - Re-habilita longs (mask normal)
    - LR=2e-6 (mas suave para estabilizar)
    - ent_coef=0.01
    - Modelo puede elegir L o S segun las features

RAZON POR LA QUE ESTO FUNCIONA:
  - En Phase 1, con BTC bajista (-1.38% train dataset), los shorts SON rentables
  - El modelo aprende que OPEN_SHORT genera recompensas positivas en este contexto
  - Esa asociacion queda codificada en los pesos
  - En Phase 2, el modelo tiene AMBAS opciones y usa los features para decidir

METRICAS objetivo:
  - Phase 1 eval: mayoría shorts, Net positivo (validamos que shorts funcionan)
  - Phase 2 eval: 50/50 L+S, Net positivo y estable
"""
import os, sys, gc, time, subprocess
import numpy as np
os.chdir(r"C:\Bot mk3")
sys.path.insert(0, 'python')
sys.path.insert(0, 'python/bot_ml')

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from bot_ml.grpc_env import GrpcTradingEnv

BASE_MODEL = r"C:\Bot mk3\python\runs_train\training_v9_selective_v20b\model_v20b_final.zip"
BASE_VENV  = r"C:\Bot mk3\python\runs_train\training_v9_selective_v20b\venv_v20b_final.pkl"
OUT_DIR    = r"C:\Bot mk3\python\runs_train\maker_exit_v13"
INITIAL_EQUITY = 1200.0
VAL_STEPS = 100000

CFG = dict(
    initial_equity=1200.0,
    max_leverage=10.0,
    max_pos_frac=0.10,
    profit_floor_bps=5.0,
    stop_loss_bps=5.0,
    reward_fee_cost_weight=0.25,
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0003,
    reward_trailing_mfe_penalty_weight=0.04,
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    # Long veto (Phase 2 only; Phase 1 mutes longs via mask)
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,
    long_veto_bb_pos_5m_threshold=0.40,
    long_veto_regime_dead_threshold=0.60,
    # Sin short veto: aprendizaje libre en Phase 1
    use_selective_entry_short_v1=False,
    fill_model=2,
    use_exit_curriculum_d1=True,
    exit_fallback_loss_bps=5.0,
    exit_fallback_mfe_giveback_bps=4.0,
    exit_fallback_thesis_decay_threshold=2.0,
    maker_first_exit_timeout_ms=20000,
    exit_maker_pricing_multiplier=1.0,
    reward_exit_maker_bonus_weight=0.10,
    reward_exit_taker_penalty_weight=0.06,
)

# Action indices
OPEN_LONG  = 1
ADD_LONG   = 2

def mask_fn_normal(env):
    """Standard mask: all valid actions."""
    return env.action_masks()

def mask_fn_short_only(env):
    """Phase 1: disable OPEN_LONG and ADD_LONG — force model to learn shorts."""
    mask = env.action_masks()
    mask[OPEN_LONG] = 0
    mask[ADD_LONG] = 0
    return mask

def start_server():
    os.system("taskkill //F //IM bot-server.exe 2>NUL")
    time.sleep(2)
    proc = subprocess.Popen([r"C:\Bot mk3\target\release\bot-server.exe"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc

def make_env(dataset_id, phase=2):
    """Phase 1=short-only mask, Phase 2=normal mask."""
    mfn = mask_fn_short_only if phase == 1 else mask_fn_normal
    return ActionMasker(
        GrpcTradingEnv("localhost:50051", dataset_id=dataset_id, symbol="BTCUSDT", **CFG), mfn)

def quick_audit(model, venv_path, label, phase=2):
    """Audit with normal (bidirectional) mask always — measures true capability."""
    print(f"\n[AUDIT] {label}")
    cfg_a = {**CFG, "reward_exit_maker_bonus_weight": 0.0, "reward_exit_taker_penalty_weight": 0.0}
    def make_val():
        return ActionMasker(GrpcTradingEnv("localhost:50051",
            dataset_id="golden_l2_v1_val", symbol="BTCUSDT", **cfg_a), mask_fn_normal)
    venv = DummyVecEnv([make_val])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False; venv.norm_reward = False

    trades = em = et = fb = 0
    long_trades = short_trades = 0
    in_pos = False; cs = 0
    total_net = 0.0
    last_done = False
    info = {}

    obs = venv.reset()
    for _ in range(VAL_STEPS):
        masks = venv.env_method("action_masks")[0]
        act, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, _, done, info_list = venv.step(act)
        info = info_list[0]
        last_done = done[0]

        if info.get("exit_fallback_triggered", 0): fb += 1
        for fill in info.get("fills", []):
            s = fill.get("side",""); l = fill.get("liquidity","")
            is_exit = (cs>0 and "Sell" in s) or (cs<0 and "Buy" in s)
            if not is_exit and not in_pos:
                trades += 1
                if "Buy" in s: long_trades += 1
                else: short_trades += 1
            if is_exit:
                if "Maker" in l: em += 1
                else: et += 1
        pos = info.get("position_qty", 0.0)
        if abs(pos) > 1e-9:
            if not in_pos: in_pos = True; cs = 1 if pos > 0 else -1
        else:
            if in_pos: in_pos = False; cs = 0

        if done[0]:
            ep_equity = info.get("equity", INITIAL_EQUITY)
            total_net += ep_equity - INITIAL_EQUITY
            obs = venv.reset(); in_pos = False; cs = 0

    if not last_done:
        ep_equity = info.get("equity", INITIAL_EQUITY)
        ep_unrealized = info.get("unrealized_pnl", 0.0)
        total_net += ep_equity - ep_unrealized - INITIAL_EQUITY

    venv.close(); gc.collect()

    tot = em + et
    mk = em / tot * 100 if tot > 0 else 0
    net_per_trade = total_net / trades if trades > 0 else 0.0
    steps_k = VAL_STEPS / 1000.0
    print(f"  Trades={trades}({trades/steps_k:.1f}/1k)  L={long_trades} S={short_trades}  "
          f"Exits={tot}  Maker={mk:.0f}%  ({em}M/{et}T)  "
          f"Net={total_net:+.2f}  Net/trade={net_per_trade:+.4f}  D1={fb}")
    return {"trades": trades, "long": long_trades, "short": short_trades,
            "exits": tot, "maker_pct": round(mk,1),
            "net_pnl": round(total_net,4), "net_per_trade": round(net_per_trade,6), "d1": fb}

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"[TRAIN] v13: Curriculum Short->Bidirectional | $1200/10x/10% | floor=5bps")
    print(f"  Phase 1 (100k): SHORT-ONLY forced (mask disables OPEN_LONG)")
    print(f"  Phase 2 (300k): Bidirectional (normal mask)")

    server = start_server()
    try:
        # ===== PHASE 1: SHORT-ONLY (100k steps) =====
        print(f"\n[PHASE 1] SHORT-ONLY training | LR=5e-6 | ent=0.02")
        LR1 = 5e-6
        train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train", phase=1)])
        train_venv = VecNormalize.load(BASE_VENV, train_venv)
        model = MaskablePPO.load(BASE_MODEL, env=train_venv, device="cuda",
                                 custom_objects={"learning_rate": LR1,
                                                 "ent_coef": 0.02})

        phase1_ckpts = [50000, 100000]
        trained = 0
        for ckpt in phase1_ckpts:
            model.learn(total_timesteps=ckpt - trained, progress_bar=True,
                        reset_num_timesteps=False)
            trained = ckpt
            path = f"{OUT_DIR}/model_p1_{ckpt//1000}k.zip"
            venv_path = f"{OUT_DIR}/venv_p1_{ckpt//1000}k.pkl"
            model.save(path); train_venv.save(venv_path)
            train_venv.close()
            print(f"[SAVED] Phase1 {path}")

            server.terminate(); time.sleep(3); server = start_server()
            quick_audit(model, venv_path, f"v13_p1_{ckpt//1000}k", phase=1)

            if ckpt < phase1_ckpts[-1]:
                server.terminate(); time.sleep(3); server = start_server()
                train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train", phase=1)])
                train_venv = VecNormalize.load(venv_path, train_venv)
                model.set_env(train_venv)

        # Save Phase 1 final model
        p1_model = f"{OUT_DIR}/model_p1_100k.zip"
        p1_venv  = f"{OUT_DIR}/venv_p1_100k.pkl"

        # ===== PHASE 2: BIDIRECTIONAL (300k steps) =====
        print(f"\n[PHASE 2] BIDIRECTIONAL training | LR=2e-6 | ent=0.01")
        LR2 = 2e-6
        server.terminate(); time.sleep(3); server = start_server()
        train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train", phase=2)])
        train_venv = VecNormalize.load(p1_venv, train_venv)
        model = MaskablePPO.load(p1_model, env=train_venv, device="cuda",
                                 custom_objects={"learning_rate": LR2,
                                                 "ent_coef": 0.01})

        phase2_ckpts = [50000, 100000, 150000, 200000, 300000]
        trained = 0
        for ckpt in phase2_ckpts:
            model.learn(total_timesteps=ckpt - trained, progress_bar=True,
                        reset_num_timesteps=False)
            trained = ckpt
            path = f"{OUT_DIR}/model_{ckpt//1000}k.zip"
            venv_path = f"{OUT_DIR}/venv_{ckpt//1000}k.pkl"
            model.save(path); train_venv.save(venv_path)
            train_venv.close()
            print(f"[SAVED] Phase2 {path}")

            server.terminate(); time.sleep(3); server = start_server()
            quick_audit(model, venv_path, f"v13_{ckpt//1000}k", phase=2)

            if ckpt < phase2_ckpts[-1]:
                server.terminate(); time.sleep(3); server = start_server()
                train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train", phase=2)])
                train_venv = VecNormalize.load(venv_path, train_venv)
                model.set_env(train_venv)
    finally:
        server.terminate()
