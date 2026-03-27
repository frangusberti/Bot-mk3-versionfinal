"""
offline_train.py — Train PPO agent on offline historical data windows.
"""
import sys
import os
# Add parent directory to sys.path to find bot_pb2
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import argparse
import os
import time
import random
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecMonitor
from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback, CallbackList

class ProgressCallback(BaseCallback):
    def __init__(self, total_steps, verbose=0):
        super(ProgressCallback, self).__init__(verbose)
        self.total_steps = total_steps

    def _on_step(self) -> bool:
        # Update progress every 10 steps and flush to ensure GUI updates
        # Optimization: GC collect every 10 complete episodes (approx) or 5000 steps to keep RAM stable
        if self.n_calls % 5000 == 0:
            import gc
            gc.collect()

        if self.n_calls % 100 == 0 or self.n_calls == 1:
            progress = self.num_timesteps / self.total_steps
            print(f"__PROGRESS__:{progress:.4f}", flush=True)
            
        return True

import window_env
from episode_builder import EpisodeBuilder
from model_registry import ModelRegistry

def evaluate_model(model, env, num_episodes=5):
    """
    Evaluates model on the environment and returns aggregated metrics.
    Assumes env is a DummyVecEnv wrapping a WindowTradingEnv/GrpcTradingEnv.
    """
    total_pnl = 0.0
    max_drawdowns = []
    total_trades = 0
    
    # Get Initial Equity from config if possible, else assume 10000.0 or wait for first info
    initial_equity = 10000.0
    try:
        initial_equity = env.envs[0].rl_config.initial_equity
    except:
        pass
        
    obs = env.reset()
    
    # Trackers for the single environment in DummyVecEnv
    current_equity = initial_equity
    peak_equity = initial_equity
    episode_dd = 0.0
    episode_trades = 0
    
    count = 0
    while count < num_episodes:
        action, _ = model.predict(obs, deterministic=True)
        obs, rewards, dones, infos = env.step(action)
        
        # We assume 1 env for evaluation simplicity
        done = dones[0]
        info = infos[0]
        
        if 'equity' in info:
            current_equity = info['equity']
            peak_equity = max(peak_equity, current_equity)
            if peak_equity > 0:
                dd = (peak_equity - current_equity) / peak_equity
                episode_dd = max(episode_dd, dd)
        
        if 'trades_executed' in info:
            episode_trades += info['trades_executed']
            
        if done:
            count += 1
            # Calculate PnL for this episode
            # Using current_equity might be slightly off if reset happened immediately? 
            # In SB3, 'obs' is the NEXT episode's first obs, but 'info' is from the COMPLETED episode.
            # So current_equity should be the final equity.
            
            pnl = (current_equity - initial_equity) / initial_equity
            total_pnl += pnl
            max_drawdowns.append(episode_dd)
            total_trades += episode_trades
            
            # Reset trackers
            current_equity = initial_equity
            peak_equity = initial_equity
            episode_dd = 0.0
            episode_trades = 0
            
    # Aggregate
    avg_pnl = total_pnl / count if count > 0 else 0.0
    avg_dd = sum(max_drawdowns) / count if count > 0 else 0.0
    avg_trades = total_trades / count if count > 0 else 0.0
    
    return {
        "net_pnl": avg_pnl, # Return as Fraction (e.g. 0.05 for 5%)
        "max_dd": avg_dd,   # Fraction
        "trade_count": avg_trades
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=1_000_000, help="Total timesteps to train")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol(s), comma separated")
    parser.add_argument("--index", type=str, default="data/index/datasets_index.json", help="Path to index")
    parser.add_argument("--window", type=int, default=1800, help="Window size in seconds (e.g. 30min)")
    parser.add_argument("--stride", type=int, default=300, help="Window stride in seconds (e.g. 5min)")
    parser.add_argument("--run_name", type=str, default=f"offline_{int(time.time())}", help="Name for this training run")
    parser.add_argument("--server", type=str, default="localhost:50051", help="gRPC server address")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--ent_coef", type=float, default=0.01, help="Entropy coefficient")
    parser.add_argument("--learning_rate", type=float, default=1e-4, help="Learning rate (conservative default)")
    parser.add_argument("--target_kl", type=float, default=0.02, help="Target KL divergence for stability")
    parser.add_argument("--load_model", type=str, default="", help="Path to pre-trained model to load")
    parser.add_argument("--threads", type=int, default=0, help="Max CPU threads (0 = auto)")
    parser.add_argument("--low-priority", action="store_true", help="Run with below-normal priority")
    parser.add_argument("--leverage", type=float, default=5.0, help="Max leverage")
    parser.add_argument("--pos_frac", type=float, default=0.20, help="Max position fraction")
    parser.add_argument("--disaster_dd", type=float, default=0.15, help="Hard disaster drawdown limit (0.15 = 15%%)")
    parser.add_argument("--exp_dirs", type=str, nargs="+", default=[], help="Directories with recorded experience (parquet)")
    parser.add_argument("--profile", type=str, default="Rich", help="Feature profile (Simple or Rich)")
    
    args = parser.parse_args()

    # Create logs directory
    log_dir = f"python/runs_train/{args.run_name}"
    os.makedirs(log_dir, exist_ok=True)
    
    # Resource Management
    if args.threads == 0:
        # Dynamic Auto-tune
        total_cores = os.cpu_count() or 4
        # Use 50% of cores, min 2, max 8 (diminishing returns on PPO CPU)
        args.threads = max(2, min(8, int(total_cores * 0.5)))
        print(f"Auto-tuning: Using {args.threads} CPU threads (50% of {total_cores})")
    
    if args.threads > 0:
        try:
            import torch
            torch.set_num_threads(args.threads)
            torch.set_num_interop_threads(max(1, int(args.threads/2)))
            os.environ["OMP_NUM_THREADS"] = str(args.threads)
            os.environ["MKL_NUM_THREADS"] = str(args.threads)
            print(f"Limiting CPU threads to {args.threads}")
        except ImportError:
            print("Warning: torch not found, cannot limit threads.")

    if args.low_priority:
        try:
            import psutil
            p = psutil.Process(os.getpid())
            # Windows: BELOW_NORMAL_PRIORITY_CLASS
            if os.name == 'nt':
                p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            else:
                p.nice(10) # Unix nice
            print("Running with BELOW NORMAL priority")
        except ImportError:
            print("Warning: psutil not found, cannot set priority.")
    
    symbols = args.symbol.split(",")
    
    # 1. Build Episode List or Load Experience
    if args.exp_dirs:
        from data.experience_loader import ExperienceLoader, ExperienceEnv
        print(f"Loading experience from {args.exp_dirs}...")
        all_episodes = []
        for d in args.exp_dirs:
            if os.path.exists(d):
                loader = ExperienceLoader(d)
                all_episodes.extend(loader.get_episodes())
        
        episodes = all_episodes
        print(f"Loaded {len(episodes)} recorded episodes from {len(args.exp_dirs)} directories.")
        
        def make_env():
            return ExperienceEnv(episodes)
    else:
        import window_env
        from episode_builder import EpisodeBuilder
        builder = EpisodeBuilder(args.index)
        episodes = builder.build_windows(symbols, window_len_secs=args.window, stride_secs=args.stride)
        
        if not episodes:
            print(f"No episodes found for {symbols} in {args.index}. Exiting.")
            return

        # Shuffle episodes for better training distribution
        random.seed(args.seed)
        random.shuffle(episodes)
        
        def make_env():
            return window_env.WindowTradingEnv(
                episodes=episodes,
                server_addr=args.server,
                seed=args.seed,
                max_leverage=args.leverage,
                max_pos_frac=args.pos_frac,
                hard_disaster_dd=args.disaster_dd,
                feature_profile=args.profile
            )

    print(f"Starting training run: {args.run_name}")
    print(f"Total sequences/windows: {len(episodes)}")
    print(f"Logs: {log_dir}")

    # 2. Create Window Environment
    def make_env():
        return window_env.WindowTradingEnv(
            episodes=episodes,
            server_addr=args.server,
            seed=args.seed,
            max_leverage=args.leverage,
            max_pos_frac=args.pos_frac,
            hard_disaster_dd=args.disaster_dd,
            feature_profile=args.profile
        )

    vec_env = DummyVecEnv([make_env])
    vec_env = VecMonitor(vec_env, log_dir)

    # 3. Define or Load Model
    if args.load_model:
        print(f"Loading model from {args.load_model}")
        model = PPO.load(args.load_model, env=vec_env)
        # Update config if needed
        model.ent_coef = args.ent_coef
        model.learning_rate = args.learning_rate
    else:
        # Optimized PPO for CPU with Guardrails
        model = PPO(
            "MlpPolicy",
            vec_env,
            verbose=0, # Reduced logging
            tensorboard_log=log_dir,
            ent_coef=args.ent_coef,
            learning_rate=args.learning_rate,
            seed=args.seed,
            batch_size=256,   
            n_steps=2048,     # Increased for better stability estimation? No, keep user prefs.
            n_epochs=10,       # More epochs with lower LR?
            clip_range=0.2,
            target_kl=args.target_kl, # STOP update if KL too high
        )

    # Save checkpoint every 10k steps (Optimized from 20k, but user asked for 10k min)
    checkpoint_callback = CheckpointCallback(
        save_freq=10000,
        save_path=f"{log_dir}/checkpoints",
        name_prefix="ppo_offline"
    )

    # Combined callbacks
    progress_callback = ProgressCallback(args.steps)
    callbacks = CallbackList([checkpoint_callback, progress_callback])

    # 4. Train
    try:
        model.learn(total_timesteps=args.steps, callback=callbacks)
        
        # Save Candidate
        candidate_path = f"{log_dir}/candidate_model"
        model.save(candidate_path)
        print("Training completed.")
        
        # --- Model Governance / Acceptance Gate ---
        print("\n--- Running Acceptance Gate ---")
        registry = ModelRegistry()
        
        # A. Create Eval Env (Reuse similar config)
        # Use a subset of episodes for validation? Or same set (Recalling)
        # "Replay last training window" -> Use the episodes we just trained on?
        # User said "Replay LAST training window".
        # Let's use the last N episodes from the list for validation.
        eval_episodes = episodes[-20:] if len(episodes) > 20 else episodes
        print(f"Evaluating on {len(eval_episodes)} episodes.")
        
        def make_eval_env():
             return window_env.WindowTradingEnv(
                episodes=eval_episodes,
                server_addr=args.server,
                seed=args.seed, # Same seed for fair comparison
                max_leverage=args.leverage,
                max_pos_frac=args.pos_frac,
                hard_disaster_dd=args.disaster_dd,
                feature_profile=args.profile
            )
        eval_env = DummyVecEnv([make_eval_env])
        # Note: We need to ensure WindowTradingEnv populates 'metrics' in info on done.
        
        # B. Benchmark New Model
        print("Benchmarking New Model...")
        metrics_new = evaluate_model(model, eval_env, num_episodes=len(eval_episodes))
        print(f"New Model Metrics: {metrics_new}")
        
        # C. Benchmark Old Model
        metrics_old = {"net_pnl": 0.0, "max_dd": 0.0, "trade_count": 0}
        parent_id = None
        
        if args.load_model and os.path.exists(args.load_model):
            print(f"Benchmarking Old Model ({args.load_model})...")
            try:
                old_model = PPO.load(args.load_model)
                metrics_old = evaluate_model(old_model, eval_env, num_episodes=len(eval_episodes))
                print(f"Old Model Metrics: {metrics_old}")
                parent_id = os.path.basename(args.load_model) # Rough ID
            except Exception as e:
                print(f"Failed to load/eval old model: {e}")
        else:
             print("No old model to compare against. Assuming baseline 0.")

        # D. Register and Judge
        model_id = registry.register_model(
            model_path=candidate_path + ".zip", # SB3 adds .zip
            metrics=metrics_new,
            parent_model_id=parent_id,
            train_window={"start": "?", "end": "?"}, # Populate if data known
            feature_profile=args.profile
        )
        
        accepted = registry.judge_model(
            model_id=model_id,
            old_metrics=metrics_old,
            new_metrics=metrics_new
        )
        
        if accepted:
            print(f"Model {model_id} ACCEPTED. Promoting to LIVE.")
            registry.promote_to_live(model_id)
        else:
            print(f"Model {model_id} REJECTED.")

    except KeyboardInterrupt:
        print("Training interrupted. Saving current model...")
        model.save(f"{log_dir}/interrupted_model")
    except Exception as e:
        print(f"Training failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        vec_env.close()


if __name__ == "__main__":
    main()
