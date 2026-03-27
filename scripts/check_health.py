# scripts/check_health.py
import os
import sys
import grpc
import time

# Add bot_ml path for protos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python', 'bot_ml'))
import bot_pb2
import bot_pb2_grpc

def check_health():
    server_addr = "localhost:50051"
    print(f"Connecting to {server_addr}...")
    
    try:
        channel = grpc.insecure_channel(server_addr)
        
        # 1. GetStatus from ControlService
        control_stub = bot_pb2_grpc.ControlServiceStub(channel)
        status = control_stub.GetStatus(bot_pb2.Empty())
        
        print("\n--- System Status ---")
        print(f"Recorder Active: {status.recorder_active}")
        print(f"Current Run ID:  {status.current_run_id}")
        print(f"Events Recorded: {status.events_recorded:,}")
        print(f"Uptime:          {status.uptime_seconds:.0f}s")

        # 2. Get Health from HealthService (Stream 1 message)
        health_stub = bot_pb2_grpc.HealthServiceStub(channel)
        health_iter = health_stub.StreamHealth(bot_pb2.Empty())
        
        # We only need the first update
        report = next(health_iter)
        
        print("\n--- Component Health ---")
        print(f"Overall Status: {report.system_status}")
        for name, health in report.components.items():
            print(f"[{name}] {health.status} - {health.message}")
            if health.metrics:
                for k, v in health.metrics.items():
                    print(f"  - {k}: {v}")
                    
    except StopIteration:
        print("Health stream closed before first report.")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    check_health()
