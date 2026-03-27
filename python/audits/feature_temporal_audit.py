import os
import sys
import time
import grpc
import pandas as pd
import json
from datetime import datetime

# Add paths for proto imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot_pb2
import bot_pb2_grpc

def run_temporal_audit(dataset_id="stage2_eval", symbol="BTCUSDT", steps=1000):
    print(f"[AUDIT] Starting Feature Temporal Audit for {symbol} on {dataset_id}")
    
    channel = grpc.insecure_channel('localhost:50051')
    stub = bot_pb2_grpc.RLServiceStub(channel)
    
    # 1. Setup Environment
    rl_config = bot_pb2.RLConfig(
        clock_mode=bot_pb2.ReplayConfig.CLOCK_EXCHANGE,
        replay_speed=0.0,
        decision_interval_ms=1000,
        initial_equity=10000.0,
        market=symbol,
        random_start_offset=True,
        min_episode_events=2000
    )
    
    reset_req = bot_pb2.ResetRequest(
        symbol=symbol,
        dataset_id=dataset_id,
        config=rl_config
    )
    
    try:
        resp = stub.ResetEpisode(reset_req)
        episode_id = resp.episode_id
    except grpc.RpcError as e:
        print(f"[ERROR] Reset failed: {e}")
        return

    health_records = []
    
    def record_health(ts, h):
        health_records.append({
            'ts_event': ts,
            'book_age_ms': h.book_age_ms,
            'trades_age_ms': h.trades_age_ms,
            'mark_age_ms': h.mark_age_ms,
            'funding_age_ms': h.funding_age_ms,
            'oi_age_ms': h.oi_age_ms,
            'obs_quality': h.obs_quality
        })

    # Record first step
    if resp.feature_health:
        record_health(resp.obs.ts, resp.feature_health)

    # 2. Run Steps
    for i in range(steps):
        step_req = bot_pb2.StepRequest(
            episode_id=episode_id,
            action=bot_pb2.Action(type=bot_pb2.HOLD)
        )
        try:
            step_resp = stub.Step(step_req)
            if step_resp.feature_health:
                record_health(step_resp.obs.ts, step_resp.feature_health)
            
            if step_resp.done:
                print(f"[AUDIT] Episode done at step {i}")
                break
                
            if i % 100 == 0:
                print(f"  Step {i}/{steps}...")
        except grpc.RpcError as e:
            print(f"[ERROR] Step failed: {e}")
            break

    # 3. Analyze and Export
    df = pd.DataFrame(health_records)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "temporal_results")
    os.makedirs(output_dir, exist_ok=True)
    
    csv_path = os.path.join(output_dir, "feature_temporal_raw.csv")
    df.to_csv(csv_path, index=False)
    print(f"[AUDIT] Saved raw results to {csv_path}")
    
    # Summary Metrics
    metrics = {
        'total_samples': len(df),
        'p50_book_age': float(df['book_age_ms'].median()),
        'p95_book_age': float(df['book_age_ms'].quantile(0.95)),
        'p50_trades_age': float(df['trades_age_ms'].median()),
        'max_trades_age': float(df['trades_age_ms'].max()),
        'min_obs_quality': float(df['obs_quality'].min()),
        'avg_obs_quality': float(df['obs_quality'].mean()),
    }
    
    metrics_path = os.path.join(output_dir, "feature_temporal_metrics.json")
    with open(metrics_path, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=4)
        
    # Generate Markdown Report
    report_path = os.path.join(output_dir, "feature_temporal_report.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"# Feature Temporal Audit Report\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        f.write(f"## Summary Metrics\n\n")
        f.write(f"- Total Samples: {metrics['total_samples']}\n")
        f.write(f"- Book Age (p50/p95): {metrics['p50_book_age']} / {metrics['p95_book_age']} ms\n")
        f.write(f"- Trades Age (p50/max): {metrics['p50_trades_age']} / {metrics['max_trades_age']} ms\n")
        f.write(f"- Obs Quality (avg/min): {metrics['avg_obs_quality']:.4f} / {metrics['min_obs_quality']:.4f}\n\n")
        
        f.write("## Evaluation against Contract\n\n")
        
        # Simple evaluation logic
        book_pass = metrics['p95_book_age'] < 50
        quality_pass = metrics['avg_obs_quality'] > 0.99
        
        f.write(f"| Requirement | Metric | Result |\n")
        f.write(f"|-------------|--------|--------|\n")
        f.write(f"| Book Freshness (p95 < 50ms) | {metrics['p95_book_age']}ms | {'✅ PASS' if book_pass else '❌ FAIL'} |\n")
        f.write(f"| Obs Quality (Avg > 0.99) | {metrics['avg_obs_quality']:.4f} | {'✅ PASS' if quality_pass else '❌ FAIL'} |\n")
    
    print(f"[AUDIT] Report generated at {report_path}")

if __name__ == "__main__":
    run_temporal_audit()
