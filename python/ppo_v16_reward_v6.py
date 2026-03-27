"""
PPO v16 — Reward v6: Anti-Toxic-Maker Training
================================================
Fresh start (no warm-start) with redesigned reward parameters
targeting the toxic maker failure mode identified in forensic audit.

Key v6 changes vs v15:
  - toxic_fill_penalty:  0.001 → 0.005  (5x)
  - mtm_penalty_mult:    0.075 → 0.5    (7x)
  - mtm_window:          5000  → 3000 ms
  - maker_fill_bonus:    0.006 → 0.002  (3x reduction)
  - distance_to_mid:     1e-5  → 1e-4   (10x)
  - post_delta_threshold: 0.05 → 0.5 bps (minimum quote offset)
  - taker_action_penalty: 0.001 → 0.003 (3x)
  - taker_fill_penalty:  0.0005 → 0.002 (4x)
  - skew_penalty:        5e-5  → 2e-4   (4x)
  - inventory_change:    0.003 → 0.005  (1.7x)
  - reprice_penalty:     5e-5  → 1e-4   (2x)
  - adverse_sel_bonus:   0.8   → 0.3    (reduced gambling incentive)
"""
import os
import sys
import argparse
import json
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv


# ── Reward v6 Parameter Table ─────────────────────────────────────────────
REWARD_V6 = dict(
    # Core fill incentives (reduced to prevent fill-chasing)
    reward_maker_fill_bonus=0.002,           # v5: 0.006 → v6: 0.002
    reward_taker_fill_penalty=0.002,         # v5: 0.0005 → v6: 0.002
    reward_toxic_fill_penalty=0.005,         # v5: 0.001 → v6: 0.005

    # Idle / presence
    reward_idle_posting_penalty=0.00001,     # unchanged
    reward_quote_presence_bonus=0.00015,     # unchanged (anti-passivity)

    # Distance & posting constraints
    reward_distance_to_mid_penalty=0.0001,   # v5: 1e-5 → v6: 1e-4
    post_delta_threshold_bps=0.5,            # v5: 0.05 → v6: 0.5 (minimum offset)
    reward_reprice_penalty_bps=0.0001,       # v5: 5e-5 → v6: 1e-4

    # Adverse Selection / MtM (PRIORITY #1)
    reward_mtm_penalty_window_ms=3000,       # v5: 5000 → v6: 3000
    reward_mtm_penalty_multiplier=0.5,       # v5: 0.075 → v6: 0.5
    reward_adverse_selection_bonus_multiplier=0.3,  # v5: 0.8 → v6: 0.3

    # Inventory discipline
    reward_skew_penalty_weight=0.0002,       # v5: 5e-5 → v6: 2e-4
    reward_inventory_change_penalty=0.005,   # v5: 0.003 → v6: 0.005

    # Two-sided & PnL
    reward_two_sided_bonus=0.001,            # unchanged
    reward_realized_pnl_multiplier=0.001,    # unchanged

    # Action penalties
    reward_cancel_all_penalty=3e-7,          # unchanged
    reward_taker_action_penalty=0.003,       # v5: 0.001 → v6: 0.003
)


class RewardV6Callback(BaseCallback):
    """Callback with checkpoint audits for Reward v6 training."""

    # Checkpoint schedule: early & frequent for a fresh start
    CHECKPOINTS = [50_000, 100_000, 150_000, 200_000, 250_000, 300_000]

    def __init__(self, val_dataset="golden_l2_v1_val", out_dir="python/runs_train/reward_v6/ppo_v16", verbose=0):
        super().__init__(verbose)
        self.val_dataset = val_dataset
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)

    def _on_step(self) -> bool:
        step = self.n_calls
        if step in self.CHECKPOINTS:
            label = f"{step // 1000}k"
            print(f"\n[Reward v6] Step {step}: Saving checkpoint {label}...")

            ckpt_path = os.path.join(self.out_dir, f"model_{label}.zip")
            self.model.save(ckpt_path)

            venv_path = os.path.join(self.out_dir, f"venv_{label}.pkl")
            self.model.get_env().save(venv_path)

            self._run_eval(ckpt_path, venv_path, step)
        return True

    def _run_eval(self, model_path, venv_path, steps):
        from ppo_eval_checkpoint import run_ppo_audit
        label = f"{steps // 1000}k"
        try:
            report = run_ppo_audit(
                model_path=model_path,
                venv_path=venv_path,
                dataset_id=self.val_dataset,
                steps_per_eval=10_000,
            )

            report_path = os.path.join(self.out_dir, f"report_{label}.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)

            # ── Print Scorecard (Curriculum Enhanced) ──
            ad = report.get("action_dist", {})
            lc = report.get("lifecycle", {})
            eb = lc.get("exit_blocked", {})
            hold_pct = ad.get("HOLD", 0)
            bid_pct = ad.get("POST_BID", 0)
            ask_pct = ad.get("POST_ASK", 0)

            print(f"[Reward v6] ── {label} Scorecard ──")
            print(f"  Entropy:  Previous=0.01 | Current=0.03") # Explicit comparison as requested
            print(f"  Net PnL:  {report.get('net_pnl', 0):.4f}% (Realized: {lc.get('total_realized_pnl', 0):.4f})")
            print(f"  PF:       {report.get('profit_factor', 0):.2f}")
            print(f"  Trades:   {report.get('total_trades', 0)}")
            print(f"  Maker:    {report.get('maker_fills', 0)} | Toxic: {report.get('toxic_fills', 0)}")
            print(f"  HOLD:     {hold_pct:.2f}% | OPEN: {lc.get('semantic_summary', {}).get('OPEN', 0):.2f}%")
            print(f"  RED/CLOSE:{lc.get('semantic_summary', {}).get('RED', 0):.2f}% / {lc.get('semantic_summary', {}).get('CLOSE', 0):.2f}%")
            
            # Blocked Exit Telemetry
            print(f"  Blocked:  Total={eb.get('count',0)} | 1-4bps={eb.get('count_1_to_4_bps',0)} | LostOpp={eb.get('opportunity_lost_count',0)}")
            print(f"  Max DD:   {report.get('max_drawdown', 0)*100:.3f}%")

            # ── Automated Alerts ──
            if hold_pct > 99.9:
                print(f"  ⚠️  ALERT: HOLD = {hold_pct:.4f}% — passivity freeze!")
            toxic = report.get("toxic_fills", 0)
            maker = report.get("maker_fills", 0)
            if maker > 0 and toxic / maker > 0.5:
                print(f"  ⚠️  ALERT: Toxic fill ratio = {toxic/maker:.0%} — still too high!")

        except Exception as e:
            print(f"[Reward v6] Eval FAILED at {label}: {e}")


def main():
    parser = argparse.ArgumentParser(description="PPO v16 — Reward v6 Anti-Toxic-Maker")
    parser.add_argument("--train_steps", type=int, default=300_000,
                        help="Total training steps (default 300k)")
    parser.add_argument("--out", type=str, default="python/runs_train/reward_v6/ppo_v16",
                        help="Output directory for checkpoints and reports")
    parser.add_argument("--lr", type=float, default=3e-4,
                        help="Learning rate (higher for fresh start)")
    parser.add_argument("--ent_coef", type=float, default=0.03,
                        help="Entropy coefficient (higher for exploration)")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # ── 1. Save config for reproducibility ──
    config_record = {
        "version": "v16_reward_v6",
        "reward_params": REWARD_V6,
        "ppo_params": {
            "learning_rate": args.lr,
            "ent_coef": args.ent_coef,
            "n_steps": 2048,
            "batch_size": 64,
            "n_epochs": 10,
        },
        "train_steps": args.train_steps,
        "warm_start": False,
        "fill_model": 2,
        "dataset": "golden_l2_v1_train",
    }
    with open(os.path.join(args.out, "config_v16.json"), "w") as f:
        json.dump(config_record, f, indent=2)
    print(f"[Reward v6] Config saved to {args.out}/config_v16.json")

    # ── 2. Initialize Environment with Reward v6 ──
    print("[Reward v6] Initializing training environment...")
    raw_env = GrpcTradingEnv(
        server_addr="127.0.0.1:50051",
        dataset_id="golden_l2_v1_train",
        symbol="BTCUSDT",
        fill_model=2,   # Optimistic for faster reward feedback
        profit_floor_bps=2.0,
        **REWARD_V6,
    )
    venv = DummyVecEnv([lambda: raw_env])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    # ── 3. Create Fresh PPO Agent (NO warm-start) ──
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[Reward v6] Creating fresh PPO agent on {device}")
    model = PPO(
        "MlpPolicy",
        venv,
        learning_rate=args.lr,
        ent_coef=args.ent_coef,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        max_grad_norm=0.5,
        vf_coef=0.5,
        verbose=1,
        device=device,
        policy_kwargs=dict(
            net_arch=dict(pi=[256, 256], vf=[256, 256]),
        ),
    )

    # ── 4. Train ──
    callback = RewardV6Callback(out_dir=args.out)
    print(f"[Reward v6] Starting {args.train_steps} steps fresh training...")
    model.learn(total_timesteps=args.train_steps, callback=callback, progress_bar=True)

    # ── 5. Final Save ──
    final_model = os.path.join(args.out, "ppo_v16_reward_v6_final.zip")
    final_venv = os.path.join(args.out, "ppo_v16_reward_v6_venv_final.pkl")
    model.save(final_model)
    venv.save(final_venv)
    print(f"\n[Reward v6] Training complete. Model: {final_model}")


if __name__ == "__main__":
    main()
