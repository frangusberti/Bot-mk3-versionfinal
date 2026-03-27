import grpc
import sys
import os
import time

# Add bot_gui to path
current_dir = os.path.dirname(os.path.abspath(__file__))
bot_gui_path = os.path.join(current_dir, 'bot_gui')
if bot_gui_path not in sys.path:
    sys.path.append(bot_gui_path)

import bot_pb2
import bot_pb2_grpc

def run():
    print("Connecting to server...")
    channel = grpc.insecure_channel('localhost:50051')
    stub = bot_pb2_grpc.DatasetServiceStub(channel)

    run_id = "0068b064-b157-46df-be65-a42b634df3a0"
    print(f"Requesting build for run_id: {run_id}")
    
    try:
        resp = stub.BuildDataset(bot_pb2.BuildDatasetRequest(run_id=run_id))
        dataset_id = resp.dataset_id
        print(f"Build started. Dataset ID: {dataset_id}. Initial status: {resp.status}")
        
        # Poll status
        for _ in range(10):
            status = stub.GetDatasetStatus(bot_pb2.GetDatasetStatusRequest(dataset_id=dataset_id))
            print(f"Status: {status.state} - {status.message} ({status.progress*100:.1f}%)")
            if status.state in ["COMPLETED", "FAILED"]:
                if status.state == "COMPLETED":
                    report = stub.GetQualityReport(bot_pb2.GetQualityReportRequest(dataset_id=dataset_id))
                    print(f"\n--- QUALITY REPORT ---")
                    print(f"Overall Status: {report.overall_status}")
                    print(f"Usable for Training: {report.usable_for_training}")
                    print(f"Usable for Backtest: {report.usable_for_backtest}")
                    print(f"Total Gaps: {report.total_gaps}")
                    print(f"Missing Streams: {report.missing_streams}")
                    for name, s in report.streams.items():
                        print(f"Stream {name}: Lag p99={s.lag_p99_ms:.1f}ms, Drift={s.drift_ms_avg:.1f}ms")
                break
            time.sleep(1)
            
    except grpc.RpcError as e:
        print(f"RPC Error: {e}")

if __name__ == "__main__":
    run()
