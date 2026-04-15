import os, sys, json, numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv

CKPT_DIR = "python/runs_train/masking_v3_checkpoints"

BASE_CONFIG = dict(
    close_position_loss_threshold=0.003,
    min_post_offset_bps=0.2,
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_quote_presence_bonus=0.0,
    reward_thesis_decay_weight=0.0001,
    override_action_dim=10,
    use_selective_entry=True,
    entry_veto_threshold_bps=0.2,
    micro_strict=False,
    fill_model=2,
    reward_consolidated_variant=True,
    max_daily_dd=0.05,
    random_start_offset=True,
)

def mask_fn(env):
    return env.action_masks()

def check_gates():
    env = GrpcTradingEnv(server_addr="localhost:50051", dataset_id="stage2_eval", symbol="BTCUSDT", **BASE_CONFIG)
    ev = ActionMasker(env, mask_fn)
    ev = DummyVecEnv([lambda: ev])
    ev = VecNormalize.load(os.path.join(CKPT_DIR, "venv_150k.pkl"), ev)
    ev.training = False; ev.norm_reward = False
    model = MaskablePPO.load(os.path.join(CKPT_DIR, "model_150k.zip"), env=ev, device="cpu")

    obs = ev.reset()
    
    counts = {
        "OPEN_LONG": 0, "OPEN_SHORT": 0, 
        "VETO_LONG": 0, "VETO_SHORT": 0,
        "IMB_BLOCK_LONG": 0, "IMB_BLOCK_SHORT": 0,
        "MAKER_FILL_LONG": 0, "MAKER_FILL_SHORT": 0
    }

    pending_side = None

    for _ in range(5000):
        mask = ev.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(mask))
        act_int = int(action[0])
        
        if act_int in (1, 2):
            counts["OPEN_LONG"] += 1
            pending_side = "LONG"
        elif act_int in (5, 6):
            counts["OPEN_SHORT"] += 1
            pending_side = "SHORT"
            
        # Call step
        obs, reward, done, info = ev.step(action)
        i0 = info[0]
        
        # Check actual trades and fills
        fills = i0.get("fills", [])
        for f in fills:
            if pending_side == "LONG":
                counts["MAKER_FILL_LONG"] += 1
            elif pending_side == "SHORT":
                counts["MAKER_FILL_SHORT"] += 1
        
        # Unpack why pending side didn't become a fill
        # Unfortunately info doesn't track veto per step individually per action. 
        # But we can approximate by seeing if we asked for OPEN and nothing emerged.

    print("--- GATE CHECK EVAL ---")
    for k, v in counts.items():
        print(f"  {k}: {v}")

check_gates()
