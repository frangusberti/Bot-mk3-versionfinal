import grpc
import sys, os
sys.path.insert(0, './python/bot_ml')
import bot_pb2
import bot_pb2_grpc

def test_telemetry():
    channel = grpc.insecure_channel('localhost:50051')
    stub = bot_pb2_grpc.RLServiceStub(channel)
    
    # Start episode
    reset_req = bot_pb2.ResetRequest(
        dataset_id="stage2_eval",
        symbol="BTCUSDT",
        config=bot_pb2.RLConfig(fill_model=1)
    )
    reset_resp = stub.ResetEpisode(reset_req)
    ep_id = reset_resp.episode_id
    print(f"EPISODE ID: {ep_id}")
    
    # Take one action (OPEN_LONG)
    step_req = bot_pb2.StepRequest(
        episode_id=ep_id,
        action=bot_pb2.Action(type=1) # 1 = OPEN_LONG
    )
    resp = stub.Step(step_req)
    
    print("\n--- STEP 1 INFO (OPEN_LONG) ---")
    print(resp.info)
    print(f"accepted_as_marketable_count: {getattr(resp.info, 'accepted_as_marketable_count', 'MISSING')}")
    print(f"accepted_as_passive_count: {getattr(resp.info, 'accepted_as_passive_count', 'MISSING')}")
    
    # Take another action (HOLD)
    step_req = bot_pb2.StepRequest(
        episode_id=ep_id,
        action=bot_pb2.Action(type=0) # 0 = HOLD
    )
    resp = stub.Step(step_req)
    print("\n--- STEP 2 INFO (HOLD) ---")
    print(resp.info)
    
if __name__ == "__main__":
    test_telemetry()
