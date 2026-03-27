import os
import logging
import numpy as np
from .base import BasePolicy

logger = logging.getLogger("policy_server")

class SB3PPOPolicy(BasePolicy):
    def __init__(self, model_path):
        from stable_baselines3 import PPO
        self.model = None
        if model_path and os.path.exists(model_path):
            try:
                self.model = PPO.load(model_path)
                logger.info(f"SB3PPOPolicy: Loaded model from {model_path}")
            except Exception as e:
                logger.error(f"SB3PPOPolicy: Failed to load model: {e}")
        else:
             logger.error(f"SB3PPOPolicy: Model path not found: {model_path}")

    def infer(self, symbol, obs, portfolio, risk, config):
        if not self.model:
            return "HOLD", 0.0, "model_not_loaded", 0.0, 0.0
        
        try:
            import torch
            obs_array = np.array(obs, dtype=np.float32)
            
            # 1. Deterministic Prediction
            action_idx_raw, _ = self.model.predict(obs_array, deterministic=True)
            action_idx = int(action_idx_raw)
            
            # 2. Advanced Probability & Entropy Extraction
            log_prob = 0.0
            value = 0.0
            confidence = 1.0
            
            if hasattr(self.model, "policy"):
                obs_tensor, _ = self.model.policy.obs_to_tensor(obs_array.reshape(1, -1))
                with torch.no_grad():
                    # Get the distribution from the policy
                    distribution = self.model.policy.get_distribution(obs_tensor)
                    
                    # For Categorical distribution (Discrete actions)
                    if hasattr(distribution, "distribution"):
                        probs = distribution.distribution.probs.cpu().numpy()[0] # [0.1, 0.05, 0.8, ...]
                        
                        # Confidence = Probability of the selected action
                        confidence = float(probs[action_idx])
                        
                        # Entropy for uncertainty metric (optional logging)
                        # entropy = distribution.entropy().cpu().item()
                    
                    # Original value/log_prob retrieval
                    action_tensor = torch.tensor([action_idx]).to(self.model.device)
                    values, log_probs, _ent = self.model.policy.evaluate_actions(obs_tensor, action_tensor)
                    log_prob = float(log_probs.cpu().numpy()[0])
                    value = float(values.cpu().numpy()[0])
            
            # Action Mapping
            mapping = {
                0: "HOLD",
                1: "OPEN_LONG",
                2: "OPEN_SHORT",
                3: "CLOSE_ALL",
                4: "REDUCE_25",
                5: "REDUCE_50",
                6: "REDUCE_100"
            }
            
            action = mapping.get(action_idx, "HOLD")
            return action, confidence, f"sb3_ppo_idx_{action_idx}", log_prob, value
            
        except Exception as e:
            logger.error(f"SB3PPOPolicy inference error: {e}")
            return "HOLD", 0.0, f"error: {str(e)}", 0.0, 0.0
