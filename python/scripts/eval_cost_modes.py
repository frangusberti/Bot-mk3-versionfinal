import os
import sys
import time
import subprocess
import grpc
import json

# Add proto files so we can talk to the server
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import bot_pb2
import bot_pb2_grpc

from concurrent import futures

DATASET = "synthetic_train"
SYMBOL = "BTCUSDT"

def run_mode(mode_name):
    print(f"\n==============================================")
    print(f"RUNNING BOTMK3_COST_MODE = {mode_name}")
    print(f"==============================================")
    
    # Start bot-server with env var
    env = os.environ.copy()
    env["BOTMK3_COST_MODE"] = mode_name
    env["RUST_LOG"] = "warn" # Reduce spam
    
    print("Starting python policy server...")
    policy_process = subprocess.Popen(
        [sys.executable, "python/bot_policy/policy_server.py"],
        env=env,
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')),
        stdout=None,
        stderr=None
    )
    
    print("Starting bot-server...")
    # Adjust path if needed
    server_process = subprocess.Popen(
        ["cargo", "run", "--bin", "bot-server"], 
        env=env,
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')),
        stdout=None,
        stderr=None
    )
    
    # Wait for startup
    print("Waiting for server to boot...")
    time.sleep(15)
    
    channel = grpc.insecure_channel('localhost:50051')
    stub = bot_pb2_grpc.OrchestratorServiceStub(channel)
    analytics_stub = bot_pb2_grpc.AnalyticsServiceStub(channel)
    
    try:
        # Start with retries
        print(f"Sending StartOrchestrator request for mode: {mode_name}")
        req = bot_pb2.StartOrchestratorRequest(
            mode="PAPER",
            symbols=[bot_pb2.SymbolConfig(symbol=SYMBOL, decision_interval_ms=1000, max_pos_frac=0.20, policy_id="PPO_BASE", exec_mode="MAKER")],
            dataset_id=DATASET,
            allow_live=False
        )
        resp = None
        for attempt in range(5):
            try:
                resp = stub.StartOrchestrator(req)
                break
            except Exception as e:
                print(f"Retry {attempt+1}/5 connecting to grpc...")
                time.sleep(3)
        if not resp:
            raise Exception("Failed to start orchestrator after retries.")
            
        run_id = resp.run_id
        print(f"Started Run ID: {run_id}")
        
        # Poll for completion. The orchestrator stops when dataset is exhausted.
        # But wait, does it stop automatically? We will check status.
        for _ in range(60): # Max wait 60s
            status_resp = stub.GetOrchestratorStatus(bot_pb2.GetOrchestratorStatusRequest())
            if status_resp.state == "STOPPED":
                break
            time.sleep(1)
            
        print("Paper run completed or stopped.")
            
        # Get Candidate Analytics
        time.sleep(2) # Allow writers to flush
        
        print(f"Fetching analytics for run {run_id}")
        query = bot_pb2.QueryCandidatesRequest(
            run_id=run_id,
        )
        candidates_resp = analytics_stub.QueryCandidates(query)
        candidates_json = candidates_resp.json_payload
        
        candidates = []
        if candidates_json:
            for line in candidates_json.split('\n'):
                if line.strip():
                    candidates.append(json.loads(line))
        
        total_evaled = len(candidates)
        vetoes = sum(1 for c in candidates if c.get("is_veto", False))
        veto_reasons = {}
        for c in candidates:
             if c.get("is_veto"):
                 reason = c.get("veto_reason", "Unknown")
                 veto_reasons[reason] = veto_reasons.get(reason, 0) + 1
                 
        approved = total_evaled - vetoes
        
        veto_rate = (vetoes / total_evaled) * 100 if total_evaled > 0 else 0
        
        print(f"Results for {mode_name}:")
        print(f"  Total Evaluated: {total_evaled}")
        print(f"  Approved:        {approved}")
        print(f"  Vetoed:          {vetoes} ({veto_rate:.2f}%)")
        print(f"  Veto Breakdown:  {veto_reasons}")
        
    except Exception as e:
        print(f"Test failed for {mode_name}: {e}")
    finally:
        server_process.terminate()
        server_process.wait()
        policy_process.terminate()
        policy_process.wait()
        print("Bot server & Policy Server terminated.")

if __name__ == "__main__":
    modes = ["LegacyRaw", "ScaledX10000", "BaselineOnly"]
    for m in modes:
        run_mode(m)
        time.sleep(3) # cooldown between tests
