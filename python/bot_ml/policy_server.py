"""
policy_server.py — gRPC Inference Server for ScalpBot.
Listens on port 50055 and serves model predictions to the Orchestrator.
"""
import grpc
from concurrent import futures
import time
import os
import numpy as np
import logging
from stable_baselines3 import PPO

# Import generated stubs
import bot_pb2
import bot_pb2_grpc

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PolicyServicer(bot_pb2_grpc.PolicyServiceServicer):
    def __init__(self, models_dir="python/runs_train"):
        self.models_dir = models_dir
        self.model_cache = {}

    def _get_model(self, policy_id):
        """Loads or retrieves a model from cache."""
        if policy_id in self.model_cache:
            return self.model_cache[policy_id]

        # Attempt to find the model zip
        # Assuming policy_id is something like 'run_123/final_model' 
        # or we search for policy_id in names.
        model_path = os.path.join(self.models_dir, policy_id)
        if not model_path.endswith(".zip"):
            model_path += ".zip"

        if not os.path.exists(model_path):
            logger.error(f"Model not found at {model_path}")
            return None

        try:
            logger.info(f"Loading model: {model_path}")
            model = PPO.load(model_path)
            self.model_cache[policy_id] = model
            return model
        except Exception as e:
            logger.error(f"Failed to load model {policy_id}: {e}")
            return None

    def Infer(self, request, context):
        """Handles inference requests from the Orchestrator."""
        policy_id = request.policy_id
        symbol = request.symbol
        
        if not request.obs or not request.obs.vec:
            logger.warning(f"Empty observation received for {symbol}")
            return bot_pb2.InferResponse()

        model = self._get_model(policy_id)
        if not model:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Policy {policy_id} not found")
            return bot_pb2.InferResponse()

        try:
            # Prepare observation
            obs_array = np.array(request.obs.vec, dtype=np.float32)
            
            # Default values
            action_idx = 0
            log_prob = 0.0
            value = 0.0
            
            # Check if it has policy attribute (SB3) to extract extra info
            if hasattr(model, "policy"):
                import torch
                obs_tensor, _ = model.policy.obs_to_tensor(obs_array.reshape(1, -1))
                with torch.no_grad():
                    # Predict deterministic action (consistent with legacy behavior)
                    action_idx_raw, _states = model.predict(obs_array, deterministic=True)
                    
                    if isinstance(action_idx_raw, (np.ndarray, list)):
                        action_idx = int(action_idx_raw[0])
                    else:
                        action_idx = int(action_idx_raw)
                        
                    # Evaluate the chosen action to get log_prob and value
                    action_tensor = torch.tensor([action_idx]).to(model.device)
                    # evaluate_actions returns (values, log_probs, entropy)
                    # Note: log_probs here is the log probability of the chosen action 
                    # under the current stochastic policy distribution.
                    values, log_probs, _ent = model.policy.evaluate_actions(obs_tensor, action_tensor)
                    
                    log_prob = float(log_probs.cpu().numpy()[0])
                    value = float(values.cpu().numpy()[0])
            else:
                # Basic prediction for non-SB3 models
                action_idx_raw, _states = model.predict(obs_array, deterministic=True)
                if isinstance(action_idx_raw, (np.ndarray, list)):
                    action_idx = int(action_idx_raw[0])
                else:
                    action_idx = int(action_idx_raw)

            logger.debug(f"Inference: {symbol} w/ policy {policy_id} -> Action {action_idx} (v={value:.2f}, lp={log_prob:.4f})")
            
            return bot_pb2.InferResponse(
                action=bot_pb2.Action(type=action_idx),
                log_prob=log_prob,
                value=value
            )
        except Exception as e:
            logger.error(f"Inference failed for {symbol}: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return bot_pb2.InferResponse()

def serve():
    # Ensure current directory is project root or relative paths work
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    bot_pb2_grpc.add_PolicyServiceServicer_to_server(PolicyServicer(), server)
    
    port = "[::]:50055"
    server.add_insecure_port(port)
    logger.info(f"Policy Server starting on {port}")
    server.start()
    
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        logger.info("Stopping Policy Server...")
        server.stop(0)

if __name__ == "__main__":
    serve()
