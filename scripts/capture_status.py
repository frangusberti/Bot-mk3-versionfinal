# scripts/capture_status.py
import os
import sys
import grpc
import time
from datetime import datetime, timedelta

# Add bot_ml path for protos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python', 'bot_ml'))
import bot_pb2
import bot_pb2_grpc

# User-defined capture start (based on Run ID: 20260317_2352_BTCUSDT)
START_TIME = datetime(2026, 3, 17, 23, 52) # UTC
DURATION_HOURS = 48
END_TIME = START_TIME + timedelta(hours=DURATION_HOURS)

RUN_DIR = r"c:\Bot mk3\runs\20260317_2352_BTCUSDT\events"

def get_status():
    now_utc = datetime.utcnow()
    remaining = END_TIME - now_utc
    elapsed = now_utc - START_TIME
    
    print("=== GOLDEN L2 CAPTURE STATUS ===")
    print(f"Run ID:    20260317_2352_BTCUSDT")
    print(f"Start:     {START_TIME} UTC")
    print(f"End:       {END_TIME} UTC")
    print(f"Progress:  {(elapsed.total_seconds() / (DURATION_HOURS * 3600)) * 100:.2f}%")
    
    if remaining.total_seconds() > 0:
        days = remaining.days
        hours, remainder = divmod(remaining.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        print(f"Remaining: {days}d {hours}h {minutes}m")
    else:
        print("Status:    COMPLETED (Waiting for Audit)")

    # 1. Query gRPC for Liveness
    is_active = False
    eps = 0
    try:
        channel = grpc.insecure_channel("localhost:50051")
        health_stub = bot_pb2_grpc.HealthServiceStub(channel)
        health_iter = health_stub.StreamHealth(bot_pb2.Empty())
        report = next(health_iter)
        
        rec_health = report.components.get("Recorder")
        if rec_health:
            is_active = rec_health.status == "OK"
            eps = int(rec_health.metrics.get("events_per_sec", 0))
            print(f"Heartbeat: ACTIVE (Rate: {eps} events/sec) [Source: gRPC]")
        else:
            print("Heartbeat: NO RECORDER COMPONENT FOUND")
    except Exception as e:
        print(f"Heartbeat: DISCONNECTED (Server down?)")

    # 2. Check file growth
    if os.path.exists(RUN_DIR):
        files = [os.path.join(RUN_DIR, f) for f in os.listdir(RUN_DIR) if f.endswith(".parquet")]
        if files:
            total_size = sum(os.path.getsize(f) for f in files)
            print(f"Disk Usage: {total_size / (1024*1024):.2f} MB ({len(files)} files)")
            latest_file = max(files, key=os.path.getmtime)
            last_mod = datetime.fromtimestamp(os.path.getmtime(latest_file))
            print(f"Last File Update: {last_mod.strftime('%H:%M:%S')} (Note: Parquet buffers row groups)")
        else:
            print("Disk Usage: No data files found yet.")
    else:
        print("Error:     Run directory not found.")

if __name__ == "__main__":
    get_status()
