import os
import sys
import argparse
import pandas as pd
import numpy as np
import torch
from stable_baselines3 import PPO

from stable_baselines3.common.vec_env import VecNormalize, DummyVecEnv

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

def run_bc_audit(model_path, dataset_id, venv_path=None, episodes=1, server="localhost:50051"):
    print(f"\n=== BC VALIDATION AUDIT ({dataset_id}) ===")
    
    # Needs a dummy env to load VecNormalize correctly
    dummy_env = GrpcTradingEnv(server_addr=server, dataset_id=dataset_id, symbol="BTCUSDT", fill_model=2)
    venv = DummyVecEnv([lambda: dummy_env])
    
    if venv_path and os.path.exists(venv_path):
        print(f"[Audit] Loading normalization stats from {venv_path}")
        venv = VecNormalize.load(venv_path, venv)
        venv.training = False
        venv.norm_reward = False
    
    model = PPO.load(model_path, env=venv)
    model.policy.eval()
    
    all_actions = []
    all_entropies = []
    trades = []
    
    for ep in range(episodes):
        obs = venv.reset() # This is already normalized by VecNormalize
        done = False
        step = 0
        
        while not done:
            # Predict
            with torch.no_grad():
                # obs is already normalized if coming from venv.reset() or venv.step()
                obs_t = torch.tensor(obs, dtype=torch.float32).to(model.device)
                dist = model.policy.get_distribution(obs_t)
                action_t = dist.get_actions(deterministic=True)
                entropy = dist.entropy().item()
            
            action = int(action_t[0].item())
            all_actions.append(action)
            all_entropies.append(entropy)
            
            # Step
            obs, reward, done, info = venv.step(np.array([action]))
            
            # Record trades if any
            info0 = info[0]
            if "last_fill" in info0 and info0["last_fill"]:
                trades.append(info0["last_fill"])
                
            step += 1
            if step % 5000 == 0:
                print(f"  Step {step}...")
                
        print(f"  Episode {ep+1} done.")

    venv.close()
    
    # 1. Action Distribution
    df_actions = pd.Series(all_actions)
    dist = df_actions.value_counts(normalize=True)
    labels = {0: "HOLD", 1: "POST_BID", 2: "POST_ASK", 3: "REPRICE_BID", 4: "REPRICE_ASK", 5: "CLEAR_QUOTES", 6: "CLOSE_POSITION"}
    
    print("\n[Audit] Action Distribution:")
    for act, freq in dist.items():
        print(f"  {labels.get(act, f'ID_{act}'):15s}: {freq*100:.2f}%")
        
    # 2. Confidence/Entropy
    avg_entropy = np.mean(all_entropies)
    print(f"\n[Audit] Average Entropy: {avg_entropy:.4f}")
    
    # 3. Trade Metrics
    print(f"\n[Audit] Trade Metrics:")
    print(f"  Total steps:  {len(all_actions)}")
    print(f"  Total trades: {len(trades)}")
    
    if len(trades) > 0:
        df_trades = pd.DataFrame(trades)
        # Side dist
        side_counts = df_trades["side"].value_counts()
        print(f"  Side dist:    {side_counts.to_dict()}")
        
        # Maker ratio (if fill_type in info)
        # Assuming our env info or logs distinguish maker vs taker
        # For now, let's look at average hold time if we have entry/exit pairs (simpler to check in server logs or just report raw trades)
        pass

    # Degeneracy check
    is_collapsed = len(dist) < 2 or dist.iloc[0] > 0.99
    print(f"\n[Audit] Degeneracy Check: {'FAIL (Collapsed)' if is_collapsed else 'PASS'}")

    return {
        "dist": dist.to_dict(),
        "entropy": avg_entropy,
        "n_trades": len(trades)
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--venv", type=str, required=True)
    parser.add_argument("--dataset", type=str, default="golden_l2_v1_val")
    parser.add_argument("--server", type=str, default="localhost:50051")
    args = parser.parse_args()
    
    run_bc_audit(args.model, args.dataset, venv_path=args.venv, server=args.server)
