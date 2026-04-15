"""
Long bias audit: diagnose why policy is 100% long-only.
Tracks raw actions, gates, vetos, features.
"""
import os, sys, json, numpy as np
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv

CKPT_DIR = "python/runs_train/masking_v3_checkpoints"
VENV_PATH = os.path.join(CKPT_DIR, "venv_150k.pkl")

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

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
]

def mask_fn(env):
    return env.action_masks()

def run_bias_audit(ckpt_label, steps=10000):
    model_path = os.path.join(CKPT_DIR, f"model_{ckpt_label}.zip")
    if not os.path.exists(model_path):
        print(f"  [{ckpt_label}] Model not found, skipping")
        return None

    def make_env():
        env = GrpcTradingEnv(server_addr="localhost:50051",
                             dataset_id="stage2_eval", symbol="BTCUSDT", **BASE_CONFIG)
        return ActionMasker(env, mask_fn)

    ev = DummyVecEnv([make_env])
    ev = VecNormalize.load(VENV_PATH, ev)
    ev.training = False; ev.norm_reward = False
    model = MaskablePPO.load(model_path, env=ev, device="cpu")

    obs = ev.reset()
    action_counts = Counter()
    
    # Track action probabilities when flat (both OPEN_LONG and OPEN_SHORT available)
    flat_probs_long = []
    flat_probs_short = []
    
    # Track masks when flat
    mask_allows_long_when_flat = 0
    mask_allows_short_when_flat = 0
    flat_steps = 0
    
    # Feature tracking
    microprice_vals = []
    imbalance_vals = []
    
    # Gate tracking
    entry_veto_long = 0
    entry_veto_short = 0
    imb_block_long = 0
    imb_block_short = 0

    for _ in range(steps):
        masks_raw = ev.env_method("action_masks")
        mask = np.array(masks_raw)
        
        # Get action probabilities from the policy
        action, _ = model.predict(obs, deterministic=True, action_masks=mask)
        act_int = int(action[0])
        action_counts[act_int] += 1
        
        obs, reward, done, info = ev.step(action)
        i0 = info[0]
        
        pos_qty = i0.get("position_qty", 0.0)
        
        # Track features when available
        micro = i0.get("microprice_minus_mid_bps", None)
        imb = i0.get("trade_imbalance_5s", None)
        if micro is not None:
            microprice_vals.append(micro)
        if imb is not None:
            imbalance_vals.append(imb)
        
        # When flat, check mask symmetry
        if abs(pos_qty) < 1e-9:
            flat_steps += 1
            m = mask[0]
            if m[1]:  # OPEN_LONG allowed
                mask_allows_long_when_flat += 1
            if m[5]:  # OPEN_SHORT allowed
                mask_allows_short_when_flat += 1
        
        # Track gate blocks from info
        ev_long = i0.get("entry_veto_long", 0)
        ev_short = i0.get("entry_veto_short", 0)
        ib_count = i0.get("gate_imbalance_blocked", 0)
        
    ev.close()
    
    total = sum(action_counts.values())
    open_long = action_counts.get(1, 0) + action_counts.get(2, 0)
    open_short = action_counts.get(5, 0) + action_counts.get(6, 0)
    
    print(f"\n  [{ckpt_label}] {total} steps")
    print(f"    OPEN_LONG:  {open_long} ({open_long/total*100:.1f}%)")
    print(f"    OPEN_SHORT: {open_short} ({open_short/total*100:.1f}%)")
    print(f"    HOLD:       {action_counts.get(0,0)} ({action_counts.get(0,0)/total*100:.1f}%)")
    print(f"    CLOSE_L:    {action_counts.get(4,0)} ({action_counts.get(4,0)/total*100:.1f}%)")
    print(f"    CLOSE_S:    {action_counts.get(8,0)} ({action_counts.get(8,0)/total*100:.1f}%)")
    print(f"    Flat steps: {flat_steps}")
    if flat_steps > 0:
        print(f"    Mask allows LONG when flat:  {mask_allows_long_when_flat}/{flat_steps} ({mask_allows_long_when_flat/flat_steps*100:.0f}%)")
        print(f"    Mask allows SHORT when flat: {mask_allows_short_when_flat}/{flat_steps} ({mask_allows_short_when_flat/flat_steps*100:.0f}%)")
    
    return {
        "ckpt": ckpt_label, "open_long": open_long, "open_short": open_short,
        "flat_steps": flat_steps,
        "mask_long_flat": mask_allows_long_when_flat,
        "mask_short_flat": mask_allows_short_when_flat,
    }


def check_features():
    """Run a passive eval just collecting raw feature values."""
    def make_env():
        env = GrpcTradingEnv(server_addr="localhost:50051",
                             dataset_id="stage2_eval", symbol="BTCUSDT", **BASE_CONFIG)
        return ActionMasker(env, mask_fn)
    
    ev = DummyVecEnv([make_env])
    ev = VecNormalize.load(VENV_PATH, ev)
    ev.training = False; ev.norm_reward = False
    model = MaskablePPO.load(os.path.join(CKPT_DIR, "model_150k.zip"), env=ev, device="cpu")
    
    obs = ev.reset()
    
    micros = []
    imbs = []
    mids = []
    rewards_when_long = []
    rewards_when_short = []
    
    # Also capture raw action logits when flat
    flat_logit_samples = []
    
    for step_i in range(10000):
        masks_raw = ev.env_method("action_masks")
        mask = np.array(masks_raw)
        
        # Get action distribution (not just deterministic)
        obs_tensor = model.policy.obs_to_tensor(obs)[0]
        with __import__('torch').no_grad():
            dist = model.policy.get_distribution(obs_tensor, mask[0] if len(mask.shape) == 2 else mask)
            probs = dist.distribution.probs.cpu().numpy()[0]
        
        action, _ = model.predict(obs, deterministic=True, action_masks=mask)
        act_int = int(action[0])
        
        obs, reward, done, info = ev.step(action)
        i0 = info[0]
        
        pos_qty = i0.get("position_qty", 0.0)
        mid = i0.get("mid_price", 0.0)
        if mid > 0:
            mids.append(mid)
        
        # When flat, record action probabilities
        if abs(pos_qty) < 1e-9 and len(flat_logit_samples) < 200:
            flat_logit_samples.append({
                "step": step_i,
                "probs": [round(p, 4) for p in probs],
                "chosen": act_int,
                "mask": [bool(m) for m in (mask[0] if len(mask.shape) == 2 else mask)],
            })
    
    ev.close()
    
    # Analyze flat action probabilities
    print("\n--- FLAT ACTION PROBABILITY DISTRIBUTION ---")
    if flat_logit_samples:
        avg_probs = np.mean([s["probs"] for s in flat_logit_samples], axis=0)
        print("  Avg action probs when flat (masked):")
        for i, (label, prob) in enumerate(zip(ACTION_LABELS, avg_probs)):
            if prob > 0.001:
                print(f"    {label:>12}: {prob:.4f} ({prob*100:.1f}%)")
        
        # Specifically compare OPEN_LONG vs OPEN_SHORT
        ol_probs = [s["probs"][1] for s in flat_logit_samples]
        os_probs = [s["probs"][5] for s in flat_logit_samples]
        print(f"\n  OPEN_LONG  prob: avg={np.mean(ol_probs):.4f} min={np.min(ol_probs):.4f} max={np.max(ol_probs):.4f}")
        print(f"  OPEN_SHORT prob: avg={np.mean(os_probs):.4f} min={np.min(os_probs):.4f} max={np.max(os_probs):.4f}")
        print(f"  Ratio L/S: {np.mean(ol_probs)/max(np.mean(os_probs), 1e-6):.1f}x")
        
        # Check mask asymmetry
        long_allowed = sum(1 for s in flat_logit_samples if s["mask"][1])
        short_allowed = sum(1 for s in flat_logit_samples if s["mask"][5])
        print(f"\n  Mask allows OPEN_LONG:  {long_allowed}/{len(flat_logit_samples)}")
        print(f"  Mask allows OPEN_SHORT: {short_allowed}/{len(flat_logit_samples)}")
    
    # Dataset direction
    if mids:
        first_mid = mids[0]
        last_mid = mids[-1]
        drift_bps = (last_mid - first_mid) / first_mid * 10000
        print(f"\n--- EVAL DATASET DIRECTION ---")
        print(f"  First mid: {first_mid:.2f}")
        print(f"  Last mid:  {last_mid:.2f}")
        print(f"  Drift:     {drift_bps:.1f} bps")
        
        # Check how many steps price went up vs down
        ups = sum(1 for i in range(1, len(mids)) if mids[i] > mids[i-1])
        downs = sum(1 for i in range(1, len(mids)) if mids[i] < mids[i-1])
        print(f"  Up steps:  {ups} ({ups/max(len(mids)-1,1)*100:.0f}%)")
        print(f"  Down steps:{downs} ({downs/max(len(mids)-1,1)*100:.0f}%)")


def main():
    print("=" * 60)
    print("LONG BIAS AUDIT")
    print("=" * 60)
    
    # 1) Action distribution by checkpoint
    print("\n--- ACTION DISTRIBUTION BY CHECKPOINT ---")
    for ckpt in ["75k", "100k", "150k"]:
        run_bias_audit(ckpt, steps=5000)
    
    # 2) Feature analysis + flat probabilities
    print("\n--- FEATURE & PROBABILITY ANALYSIS (150k) ---")
    check_features()


if __name__ == "__main__":
    main()
