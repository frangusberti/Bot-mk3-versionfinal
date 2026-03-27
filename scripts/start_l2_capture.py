# scripts/start_l2_capture.py
import os
import sys
import grpc
import time

# Add bot_ml path for protos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python', 'bot_ml'))
import bot_pb2
import bot_pb2_grpc

def start_capture():
    server_addr = "localhost:50051"
    print(f"Connecting to {server_addr}...")
    
    try:
        channel = grpc.insecure_channel(server_addr)
        stub = bot_pb2_grpc.ControlServiceStub(channel)
        
        # Configure for 48h (2880 minutes) of BTCUSDT L2 capture
        config = bot_pb2.RecorderConfig(
            symbol="BTCUSDT",
            enabled_streams=["aggTrade", "depthUpdate", "bookTicker"],
            rotation_interval_minutes=2880, # 48h
            auto_normalize=True
        )
        
        print(f"Starting Golden L2 Capture for BTCUSDT...")
        response = stub.StartRecorder(config)
        
        if response.success:
            print(f"SUCCESS: Recorder started. Run ID: {response.run_id}")
            print(f"Message: {response.message}")
        else:
            print(f"FAILED: {response.message}")
            if response.run_id:
                print(f"Existing Run ID: {response.run_id}")
                
    except Exception as e:
        print(f"ERROR: Could not connect to bot-server: {e}")
        print("Check if the bot-server is running (cargo run --release --bin bot-server)")

if __name__ == "__main__":
    start_capture()
