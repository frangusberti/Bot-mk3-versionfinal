import os, sys
import grpc
sys.path.insert(0, os.path.join(os.getcwd(), 'python'))
sys.path.insert(0, os.path.join(os.getcwd(), 'python', 'bot_ml'))

import bot_pb2
import bot_pb2_grpc
import time

def run_audit():
    channel = grpc.insecure_channel('localhost:50051')
    stub = bot_pb2_grpc.RLServiceStub(channel)

    print("--- Getting Env Info ---")
    try:
        info = stub.GetEnvInfo(bot_pb2.EnvInfoRequest())
        print(f"Server OBS_DIM: {info.obs_dim}")
    except Exception as e:
        print(f"GetEnvInfo failed: {e}")

    print("\n--- Resetting Episode ---")
    reset_req = bot_pb2.ResetRequest(
        dataset_id="stage2_eval",
        symbol="BTCUSDT",
        seed=42
    )
    
    try:
        reset_resp = stub.ResetEpisode(reset_req)
        print(f"Episode ID: {reset_resp.episode_id}")
        obs = reset_resp.obs.vec
        print(f"Initial Obs Len: {len(obs)}")
        
        print("\n--- Stepping (5 steps) ---")
        for i in range(5):
            step_req = bot_pb2.StepRequest(
                episode_id=reset_resp.episode_id,
                action=bot_pb2.Action(type=0) # Hold
            )
            step_resp = stub.Step(step_req)
            print(f"Step {i} Reward: {step_resp.reward:.4f}")
            time.sleep(0.1)
            
    except Exception as e:
        print(f"Audit failed: {e}")

if __name__ == "__main__":
    run_audit()
