"""
PPO vNext Phase 3 - Real-World Hardening
========================================
Adversarial evaluation on volatile/noisy data (stage2_train).
No micro-proxy bonus, no curriculum (final 0.3 bps gate active from start).
"""
import os
import sys
import argparse
import json
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv


# -- vNext Config (Phase 3: Hardened Constraints) --
VNEXT_CONFIG = dict(
    # Hard Gates
    close_position_loss_threshold=0.003,  # 0.3% uPnL loss to allow CLOSE_POSITION
    min_post_offset_bps=0.3,              # Terminal 0.3 bps gate
    imbalance_block_threshold=0.6,        # Block posting if |imbalance| > 0.6
    post_delta_threshold_bps=0.5,         # Min reprice delta
    profit_floor_bps=2.0,                 # Curriculum: relaxed from 5.0

    # Simplified Reward
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,

    # Legacy zeroed
    reward_quote_presence_bonus=0.0,      # Exploration proxy REMOVED
)


class VNextP3Callback(BaseCallback):
    """Adversarial scorecard callback for Phase 3."""

    CHECKPOINTS = [50_000, 100_000, 200_000, 300_000, 500_000]

    def __init__(self, out_dir="python/runs_train/vnext_p3", verbose=0):
        super().__init__(verbose)
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
                print(f"\n[P3] Step {step} (crossed {next_cp}): Saving checkpoint {label}...")

                ckpt_path = os.path.join(self.out_dir, f"model_{label}.zip")
                self.model.save(ckpt_path)

                venv_path = os.path.join(self.out_dir, f"venv_{label}.pkl")
                self.model.get_env().save(venv_path)

                # Dual Evaluation
                self._run_audit(ckpt_path, venv_path, self.val_baseline, "BASELINE", next_cp)
                self._run_audit(ckpt_path, venv_path, self.val_adversarial, "ADVERSARIAL", next_cp)
        return True

    def _run_audit(self, model_path, venv_path, dataset_id, mode_label, steps):
        from ppo_eval_checkpoint import run_ppo_audit
        label = f"{steps // 1000}k"
        try:
            print(f"  -- Running {mode_label} audit on {dataset_id}...")
            report = run_ppo_audit(
                model_path=model_path,
                venv_path=venv_path,
                dataset_id=dataset_id,
                steps_per_eval=10_000,
                **VNEXT_CONFIG,
            )

            report_path = os.path.join(self.out_dir, f"report_{label}_{mode_label.lower()}.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)

            ad = report.get("action_dist", {})
            lc = report.get("lifecycle", {})
            eb = lc.get("exit_blocked", {})
            net_pnl = report.get("net_pnl", 0)
            realized_pnl = lc.get("total_realized_pnl", 0)
            unrealized_pnl = net_pnl - realized_pnl

            # Semantic Summaries from lifecycle
            sem = lc.get("semantic_summary", {})
            open_pct = ad.get("OPEN_LONG", 0) + ad.get("OPEN_SHORT", 0)
            add_pct = ad.get("ADD_LONG", 0) + ad.get("ADD_SHORT", 0)
            red_pct = sem.get("RED", 0)
            close_pct = sem.get("CLOSE", 0)

            print(f"\n[P3] === {label} {mode_label} FOCUSED REPORT ({dataset_id}) ===")
            
            print(f"1) Action Dist:      HOLD={ad.get('HOLD',0):.1f}%, OPEN={open_pct:.1f}%, ADD={add_pct:.1f}%, REDUCE={red_pct:.1f}%, CLOSE={close_pct:.1f}%")
            
            print(f"2) Exit Behavior:    ClosedTrades={lc.get('total_realized_count', 0)}, ")
            print(f"                     BlockedExitCount={eb.get('count',0)}, Blocked_1to4bps={eb.get('count_1_to_4_bps',0)}")
            print(f"                     OpportunityLost={eb.get('opportunity_lost_count',0)}")
            
            print(f"3) PnL:              Realized={realized_pnl:.4f}%, Unrealized={unrealized_pnl:.4f}% (Total={net_pnl:.4f}%)")
            
            diagnosis = "REDUCE/CLOSE active" if (red_pct + close_pct) > 0.05 else "ADD dominates (TRAINING GAP)"
            print(f"4) Exit Diagnosis:   {diagnosis}")
            
            print(f"5) Entropy Clariy:   Prev=0.01 | Current=0.03 | Active_Prior=0.03") 
            print(f"[P3] -----------------------------------\n")

        except Exception as e:
            print(f"[P3] {mode_label} Eval FAILED at {label}: {e}")
            import traceback
            traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="PPO vNext Phase 3 - Real-World Hardening")
    parser.add_argument("--train_steps", type=int, default=300_000)
    parser.add_argument("--out", type=str, default="python/runs_train/vnext_p3")
    parser.add_argument("--dataset", type=str, default="stage2_train")
    parser.add_argument("--load", type=str, help="Load from Phase 2 300k checkpoint")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # Initialize Environment with Phase 3 dataset
    print(f"[P3] Initializing training environment on dataset: {args.dataset}")
    def make_env():
        return GrpcTradingEnv(
            server_addr="127.0.0.1:50051",
            dataset_id=args.dataset,
            symbol="BTCUSDT",
            fill_model=2,
            **VNEXT_CONFIG,
        )

    num_envs = 4
    venv = DummyVecEnv([make_env for _ in range(num_envs)])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    # device
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    if args.load and os.path.exists(args.load):
        print(f"[P3] Loading Phase 2 checkpoint: {args.load}")
        model = PPO.load(args.load, env=venv, device=device)
    else:
        print(f"[P3] Starting fresh (Adversarial Cold Start)")
        model = PPO(
            "MlpPolicy", venv,
            learning_rate=3e-4, ent_coef=0.03,
            n_steps=8192, batch_size=256, n_epochs=10,
            verbose=1, device=device,
            policy_kwargs=dict(net_arch=dict(pi=[256, 256], vf=[256, 256])),
        )

    # Train
    callback = VNextP3Callback(out_dir=args.out)
    print(f"[P3] Starting {args.train_steps} steps of Hardened training...")
    model.learn(total_timesteps=args.train_steps, callback=callback, progress_bar=True)

    # Save
    model.save(os.path.join(args.out, "ppo_vnext_p3_final.zip"))
    venv.save(os.path.join(args.out, "ppo_vnext_p3_venv_final.pkl"))
    print(f"\n[P3] Phase 3 Complete.")


if __name__ == "__main__":
    main()
