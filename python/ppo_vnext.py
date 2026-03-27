"""
PPO vNext - Reward/Constraint Architecture
===========================================
Clean separation of reward (4 terms) from constraints (hard gates).

Reward:  delta log equity - fee cost - AS penalty - inventory risk
Gates:   CLOSE_POSITION emergency-only, min quote offset, imbalance block
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


# -- vNext Config --
VNEXT_CONFIG = dict(
    # Hard Gates (Layer 3)
    close_position_loss_threshold=0.003,  # 0.3% uPnL loss to allow CLOSE_POSITION
    min_post_offset_bps=0.3,              # Min 0.3 bps from mid to place order
    imbalance_block_threshold=0.6,        # Block posting if |imbalance| > 0.6
    post_delta_threshold_bps=0.5,         # Min reprice delta (existing gate)

    # Simplified Reward (Layer 1) - 4 terms only
    reward_fee_cost_weight=0.1,           # Amplify fee signal
    reward_as_penalty_weight=0.5,         # Adverse selection penalty (no favorable bonus)
    reward_as_horizon_ms=3000,            # 3s deferred evaluation window
    reward_inventory_risk_weight=0.0005,  # Quadratic inventory risk

    # Legacy reward params - ALL ZEROED (vNext path auto-detected)
    reward_maker_fill_bonus=0.0,
    reward_taker_fill_penalty=0.0,
    reward_toxic_fill_penalty=0.0,
    reward_idle_posting_penalty=0.0,
    reward_distance_to_mid_penalty=0.0,
    reward_reprice_penalty_bps=0.0,
    reward_mtm_penalty_window_ms=0,
    reward_mtm_penalty_multiplier=0.0,
    reward_adverse_selection_bonus_multiplier=0.0,
    reward_skew_penalty_weight=0.0,
    reward_inventory_change_penalty=0.0,
    reward_two_sided_bonus=0.0,
    reward_realized_pnl_multiplier=0.0,
    reward_cancel_all_penalty=0.0,
    reward_taker_action_penalty=0.0,
    reward_quote_presence_bonus=0.0,
)


class VNextCallback(BaseCallback):
    """Checkpoint callback with causal scorecard for vNext pilot."""

    CHECKPOINTS = [50_000, 100_000, 200_000, 300_000]

    def __init__(self, val_dataset="golden_l2_v1_val",
                 out_dir="python/runs_train/vnext/ppo_vnext", verbose=0):
        super().__init__(verbose)
        self.val_dataset = val_dataset
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)

    def _on_step(self) -> bool:
        step = self.n_calls
        if step in self.CHECKPOINTS:
            label = f"{step // 1000}k"
            print(f"\n[vNext] Step {step}: Saving checkpoint {label}...")

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
                **VNEXT_CONFIG,
            )

            report_path = os.path.join(self.out_dir, f"report_{label}.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)

            ad = report.get("action_dist", {})
            hold_pct = ad.get("HOLD", 0)
            maker = report.get("maker_fills", 0)
            toxic = report.get("toxic_fills", 0)
            toxic_ratio = toxic / maker if maker > 0 else 0
            close_pct = ad.get("CLOSE_POSITION", 0)

            print(f"\n[vNext] === {label} CAUSAL SCORECARD ===")
            print(f"  -- Economics --")
            print(f"  Net PnL:           {report.get('net_pnl', 0):.4f}%")
            print(f"  Gross Profit:      ${report.get('gross_profit', 0):.4f}")
            print(f"  Gross Loss:        ${report.get('gross_loss', 0):.4f}")
            print(f"  Total Fees:        ${report.get('total_fees', 0):.4f}")
            print(f"  Profit Factor:     {report.get('profit_factor', 0):.2f}")
            print(f"  -- Fill Quality --")
            print(f"  Spread Capture:    {report.get('avg_spread_capture_bps', 0):.2f} bps")
            print(f"  Signed AS (5s):    {report.get('mtm_favorable_pct', 0):.1f}% favorable")
            print(f"    AS Favorable:    {report.get('mtm_favorable_events', 0)} events")
            print(f"    AS Adverse:      {report.get('mtm_adverse_events', 0)} events")
            print(f"  Toxic Fill Ratio:  {toxic_ratio:.0%} ({toxic}/{maker})")
            print(f"  -- Activity --")
            print(f"  Maker Fills:       {maker} (bid={report.get('maker_fills_bid',0)}, ask={report.get('maker_fills_ask',0)})")
            print(f"  HOLD:              {hold_pct:.1f}%")
            print(f"  POST BID/ASK:      {ad.get('POST_BID', 0):.1f}% / {ad.get('POST_ASK', 0):.1f}%")
            print(f"  REPRICE:           {ad.get('REPRICE_BID', 0):.1f}% / {ad.get('REPRICE_ASK', 0):.1f}%")
            print(f"  CLOSE_POSITION:    {close_pct:.1f}%")
            print(f"  -- Risk --")
            print(f"  Inventory Skew:    mean={report.get('skew_mean_position', 0):.6f}")
            print(f"  -- Gate Telemetry --")
            print(f"  CLOSE blocked:     {report.get('gate_close_blocked', 0)}")
            print(f"  Offset blocked:    {report.get('gate_offset_blocked', 0)}")
            print(f"  Imbalance blocked: {report.get('gate_imbalance_blocked', 0)}")

            alerts = []
            if maker == 0 and steps >= 100_000:
                alerts.append("[FAIL-FAST] Zero maker fills! Gates too aggressive.")
            if hold_pct > 90 and steps >= 100_000:
                alerts.append(f"[FAIL-FAST] HOLD={hold_pct:.0f}% - passivity collapse!")
            if toxic_ratio > 0.6 and maker > 5 and steps >= 200_000:
                alerts.append(f"[FAIL-FAST] Toxic={toxic_ratio:.0%} - AS penalty too low!")
            if close_pct > 5:
                alerts.append(f"[WARN] CLOSE={close_pct:.1f}% - gate not tight enough")
            if alerts:
                print(f"  -- ALERTS --")
                for a in alerts:
                    print(f"  {a}")
            print(f"[vNext] ===============================\n")

        except Exception as e:
            print(f"[vNext] Eval FAILED at {label}: {e}")
            import traceback
            traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="PPO vNext - Reward/Constraint Architecture")
    parser.add_argument("--train_steps", type=int, default=300_000)
    parser.add_argument("--out", type=str, default="python/runs_train/vnext/ppo_vnext")
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--ent_coef", type=float, default=0.05)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # Save config for reproducibility
    config_record = {
        "version": "vnext_reward_constraint",
        "params": VNEXT_CONFIG,
        "ppo": {"lr": args.lr, "ent_coef": args.ent_coef, "n_steps": 2048, "batch_size": 64, "n_epochs": 10},
        "train_steps": args.train_steps,
        "warm_start": False,
        "fill_model": 2,
    }
    with open(os.path.join(args.out, "config_vnext.json"), "w") as f:
        json.dump(config_record, f, indent=2)

    # Initialize Environment
    print("[vNext] Initializing environment with hard gates + simplified reward...")
    raw_env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="golden_l2_v1_train",
        symbol="BTCUSDT",
        fill_model=2,
        **VNEXT_CONFIG,
    )
    venv = DummyVecEnv([lambda: raw_env])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    # Fresh PPO Agent
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[vNext] Creating fresh PPO agent on {device}")
    model = PPO(
        "MlpPolicy", venv,
        learning_rate=args.lr, ent_coef=args.ent_coef,
        n_steps=2048, batch_size=64, n_epochs=10,
        gamma=0.99, gae_lambda=0.95, clip_range=0.2,
        max_grad_norm=0.5, vf_coef=0.5,
        verbose=1, device=device,
        policy_kwargs=dict(net_arch=dict(pi=[256, 256], vf=[256, 256])),
    )

    # Train
    callback = VNextCallback(out_dir=args.out)
    print(f"[vNext] Starting {args.train_steps} steps...")
    model.learn(total_timesteps=args.train_steps, callback=callback, progress_bar=True)

    # Save
    model.save(os.path.join(args.out, "ppo_vnext_final.zip"))
    venv.save(os.path.join(args.out, "ppo_vnext_venv_final.pkl"))
    print(f"\n[vNext] Training complete.")


if __name__ == "__main__":
    main()
