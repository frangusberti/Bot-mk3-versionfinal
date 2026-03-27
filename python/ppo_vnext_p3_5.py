"""
PPO vNext Phase 3.5 - Exit Architecture Refactor
================================================
New 10-action distribution (lifecycle-driven).
Enforces Profit Floor (10 bps) and strict state transitions.
"""
import os
import sys
import argparse
import json
import torch

# Ensure local imports are visible to both runtime and IDE
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback
from vnext_scorecard import generate_vnext_scorecard
from grpc_env import GrpcTradingEnv
import psutil
import gc


# -- vNext Config (Phase 3.5: Lifecycle Refactor) --
VNEXT_CONFIG = dict(
    # Hard Gates
    close_position_loss_threshold=0.003,  # 0.3% stop-loss
    min_post_offset_bps=0.2,              # Entry offset (Calibrated Phase 22)
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    profit_floor_bps=2.0,                # Reduced for Phase 27
    stop_loss_bps=30.0,

    # Simplified Reward
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    
    # Environment override
    override_action_dim=10,
)


# -- Memory Control --
MEMORY_THRESHOLD_MB = 6144 # 6GB
def get_rss_mb():
    return psutil.Process().memory_info().rss / 1e6

class MemoryTelemetryCallback(BaseCallback):
    def __init__(self, verbose=0):
        super(MemoryTelemetryCallback, self).__init__(verbose)
        self.peak_rss = 0

    def _on_step(self) -> bool:
        if self.n_calls % 1000 == 0:
            rss = get_rss_mb()
            self.peak_rss = max(self.peak_rss, rss)
            if self.verbose > 0:
                print(f"[MEMORY] Step {self.num_timesteps}: RSS={rss:.1f}MB, Peak={self.peak_rss:.1f}MB")
            if rss > MEMORY_THRESHOLD_MB:
                print(f"[WARNING] Memory Threshold Exceeded ({rss:.1f}MB > {MEMORY_THRESHOLD_MB}MB). Triggering GC...")
                gc.collect()
        return True

class VNextP3_5Callback(BaseCallback):
    """Scorecard callback for Phase 3.5."""

    CHECKPOINTS = [50_000, 100_000, 200_000, 300_000, 500_000]

    def __init__(self, out_dir="python/runs_train/vnext_p3_5", verbose=0):
        super(VNextP3_5Callback, self).__init__(verbose)
        self.out_dir = out_dir
        self.val_baseline = "golden_l2_v1_val"
        self.val_adversarial = "stage2_eval"
        self._last_checkpoint_idx = 0
        os.makedirs(out_dir, exist_ok=True)

    def _on_step(self) -> bool:
        step = self.num_timesteps
        if self._last_checkpoint_idx < len(self.CHECKPOINTS):
            next_cp = self.CHECKPOINTS[self._last_checkpoint_idx]
            if step >= next_cp:
                self._last_checkpoint_idx += 1
                label = f"{next_cp // 1000}k"
                print(f"\n[P3.5] Step {step} (crossed {next_cp}): Saving checkpoint {label}...")

                ckpt_path = os.path.join(self.out_dir, f"model_{label}.zip")
                self.model.save(ckpt_path)

                venv_path = os.path.join(self.out_dir, f"venv_{label}.pkl")
                self.model.get_env().save(venv_path)

                # Dual Evaluation
                self._run_audit(ckpt_path, venv_path, self.val_baseline, "BASELINE", next_cp)
                self._run_audit(ckpt_path, venv_path, self.val_adversarial, "ADVERSARIAL", next_cp)
        return True

    def _run_audit(self, model_path, venv_path, dataset_id, mode_label, steps):
        from ppo_eval_checkpoint import run_ppo_audit, get_memory_usage
        label = f"{steps // 1000}k"
        
        rss_start = get_memory_usage()
        is_lite = rss_start > MEMORY_THRESHOLD_MB
        if is_lite:
            print(f"[MEMORY_GUARD] Audit triggering LITE MODE (RSS={rss_start:.1f}MB)")

        try:
            print(f"  -- Running {mode_label} audit on {dataset_id}...")
            report = run_ppo_audit(
                model_path=model_path,
                venv_path=venv_path,
                dataset_id=dataset_id,
                steps_per_eval=5000,
                server="localhost:50054",
                is_lite=is_lite,
                **VNEXT_CONFIG,
            )
            
            report["peak_rss_mb"] = get_memory_usage()

            report_path = os.path.join(self.out_dir, f"report_{label}_{mode_label.lower()}.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)

            net_pnl = report.get("net_pnl", 0)
            
            # vNext Scorecard
            scorecard = generate_vnext_scorecard(report, steps)
            
            print(f"\n[P3.5] === {label} {mode_label} SCORECARD ({dataset_id}) ===")
            print(f"  Status:            {scorecard['status']}")
            print(f"  Recommendation:    {scorecard['recommendation']}")
            print(f"  Net PnL:           {net_pnl:.4f}%")
            print(f"  Profit Factor:     {report.get('profit_factor', 0):.2f}")
            print(f"  Total Trades:      {report.get('total_trades', 0)}")
            
            if scorecard["reasons"]:
                print(f"  -- Details --")
                for reason in scorecard["reasons"]:
                    print(f"  - {reason}")
                    
            print(f"[P3.5] -----------------------------------\n")

        except Exception as e:
            print(f"[P3.5] {mode_label} Eval FAILED at {label}: {e}")
            import traceback
            traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="PPO vNext Phase 3.5 - Exit Architecture Refactor")
    parser.add_argument("--train_steps", type=int, default=500_000)
    parser.add_argument("--out", type=str, default="python/runs_train/vnext_p3_5")
    parser.add_argument("--dataset", type=str, default="stage2_train")
    parser.add_argument("--load", type=str, help="Path to existing checkpoint")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    print(f"[P3.5] Initializing training environment on dataset: {args.dataset}")
    def make_env():
        return GrpcTradingEnv(
            server_addr="localhost:50054",
            dataset_id=args.dataset,
            symbol="BTCUSDT",
            fill_model=2,
            **VNEXT_CONFIG,
        )

    num_envs = 2
    venv = DummyVecEnv([make_env for _ in range(num_envs)])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    if args.load and os.path.exists(args.load):
        print(f"[P3.5] Loading checkpoint: {args.load}")
        # Try to load associated VecNormalize stats
        venv_load_path = args.load.replace(".zip", "_venv.pkl")
        if os.path.exists(venv_load_path):
            print(f"[P3.5] Loading normalization stats from: {venv_load_path}")
            venv = VecNormalize.load(venv_load_path, venv)
        
        custom_objects = {
            "learning_rate": 1e-5,
            "target_kl": 0.015,
            "ent_coef": 0.01
        }
        model = PPO.load(args.load, env=venv, device=device, custom_objects=custom_objects)
        print(f"[P3.5] Starting fresh (New 10-action workspace)")
        model = PPO(
            "MlpPolicy", venv,
            learning_rate=2e-5, ent_coef=0.03,  # Calibrated for Phase 27
            target_kl=0.02,
            n_steps=8192, batch_size=256, n_epochs=10,
            verbose=1, device=device,
            policy_kwargs=dict(net_arch=dict(pi=[256, 256], vf=[256, 256])),
        )

    callback = VNextP3_5Callback(out_dir=args.out)
    mem_callback = MemoryTelemetryCallback(verbose=1)
    
    print(f"[P3.5] Starting {args.train_steps} steps of Phase 3.5 training...")
    model.learn(total_timesteps=args.train_steps, callback=[callback, mem_callback], progress_bar=True)

    model.save(os.path.join(args.out, "ppo_vnext_p3_5_final.zip"))
    venv.save(os.path.join(args.out, "ppo_vnext_p3_5_venv_final.pkl"))
    print(f"\n[P3.5] Phase 3.5 Complete.")


if __name__ == "__main__":
    main()
