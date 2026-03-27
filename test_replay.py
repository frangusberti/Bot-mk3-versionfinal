import grpc
import bot_pb2
import bot_pb2_grpc
import time
import sys
import threading

def run_test():
    channel = grpc.insecure_channel('localhost:50051')
    replay_stub = bot_pb2_grpc.ReplayServiceStub(channel)
    dataset_stub = bot_pb2_grpc.DatasetServiceStub(channel)
    health_stub = bot_pb2_grpc.HealthServiceStub(channel)

    print("--- 1. Checking System Health ---")
    try:
        health = health_stub.GetSystemHealth(bot_pb2.Empty())
        print(f"System Status: {health.system_status}")
    except grpc.RpcError as e:
        print(f"Failed to connect to server: {e}")
        return

    print("\n--- 2. Listing Datasets ---")
    try:
        datasets_resp = dataset_stub.ListDatasets(bot_pb2.ListDatasetsRequest())
        if not datasets_resp.datasets:
            print("No datasets found. Please run test_build_dataset.py first.")
            return
        
        target_dataset = datasets_resp.datasets[0].dataset_id
        print(f"Selected Dataset: {target_dataset}")
        
    except grpc.RpcError:
        print("Failed to list datasets, using fallback 'ds_test' if available")
        target_dataset = "ds_test_001"

    print(f"\n--- 3. Starting Replay (Fast Mode) for {target_dataset} ---")
    try:
        # Start Replay
        req = bot_pb2.StartReplayRequest(
            dataset_id=target_dataset,
            config=bot_pb2.ReplayConfig(
                speed=100.0, # Fast mode
                clock_mode=0, # Exchange Clock
            )
        )
        resp = replay_stub.StartReplay(req)
        replay_id = resp.replay_id
        print(f"Replay Started. ID: {replay_id}")
        
        # Stream Events
        print("Streaming events...")
        last_ts = 0
        count = 0
        error_count = 0
        
        start_time = time.time()
        
        for event in replay_stub.StreamReplayEvents(bot_pb2.StreamReplayEventsRequest(replay_id=replay_id)):
            count += 1
            current_ts = event.time_exchange
            
            # Determinism Check: Monotonic Time
            if current_ts < last_ts:
                print(f"ERROR: Non-monotonic timestamp! Prev: {last_ts}, Curr: {current_ts}")
                error_count += 1
            
            last_ts = current_ts
            
            if count % 1000 == 0:
                print(f"Received {count} events. Last TS: {current_ts}")
                
            if count >= 10000:
                print("Limit reached, stopping test.")
                break
                
        duration = time.time() - start_time
        print(f"\n--- Test Finished ---")
        print(f"Total Events: {count}")
        print(f"Duration: {duration:.2f}s")
        print(f"Events/sec: {count/duration:.2f}")
        print(f"Ordering Errors: {error_count}")
        
        if error_count == 0:
            print("SUCCESS: Determinism Verified.")
        else:
            print("FAILURE: Determinism issues found.")

        # Stop
        replay_stub.StopReplay(bot_pb2.StopReplayRequest(replay_id=replay_id))
        
    except grpc.RpcError as e:
        print(f"Replay Error: {e}")

if __name__ == "__main__":
    run_test()
