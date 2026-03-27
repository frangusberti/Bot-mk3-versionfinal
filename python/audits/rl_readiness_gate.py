import os
import json
import pandas as pd
from datetime import datetime

def run_readiness_gate():
    print("[GATE] Starting RL Readiness Gate Evaluation")
    
    results_dir = "audits/runs_audit/trades_live"
    temporal_dir = "audits/temporal_results"
    
    # Paths
    uniqueness_path = os.path.join(results_dir, "trades_uniqueness_report.csv")
    reconciliation_path = os.path.join(results_dir, "trades_reconciliation_report.md")
    economics_path = os.path.join(results_dir, "economics_metrics.json")
    temporal_path = os.path.join(temporal_dir, "feature_temporal_metrics.json")
    
    checks = []
    
    # 1. Diversity Check
    if os.path.exists(uniqueness_path):
        unique_trades = pd.read_csv(uniqueness_path)
        unique_count = len(unique_trades)
        checks.append({
            'name': 'Replay Diversity (Unique Trade Patterns)',
            'metric': unique_count,
            'threshold': '> 10',
            'status': 'PASS' if unique_count > 10 else 'FAIL'
        })
    else:
        checks.append({'name': 'Replay Diversity', 'metric': 'N/A', 'threshold': '> 10', 'status': 'FAIL (Missing File)'})

    # 2. Temporal Check
    if os.path.exists(temporal_path):
        with open(temporal_path, 'r') as f:
            temporal_metrics = json.load(f)
        quality = temporal_metrics.get('avg_obs_quality', 0)
        checks.append({
            'name': 'Feature Temporal Stability (Avg Quality)',
            'metric': f"{quality:.4f}",
            'threshold': '> 0.99',
            'status': 'PASS' if quality > 0.99 else 'FAIL'
        })
    else:
        checks.append({'name': 'Feature Temporal', 'metric': 'N/A', 'threshold': '> 0.99', 'status': 'FAIL (Missing File)'})

    # 3. Economics Check
    if os.path.exists(economics_path):
        with open(economics_path, 'r') as f:
            econ = json.load(f)
        pf = econ.get('profit_factor_gross', 0)
        checks.append({
            'name': 'Economics (Profit Factor Gross)',
            'metric': f"{pf:.2f}",
            'threshold': '> 1.0',
            'status': 'PASS' if pf > 1.0 else 'WARN'
        })
    else:
        checks.append({'name': 'Economics', 'metric': 'N/A', 'threshold': '> 1.0', 'status': 'FAIL (Missing File)'})

    # 4. Ledger Reconciliation
    if os.path.exists(reconciliation_path):
        with open(reconciliation_path, 'r') as f:
            content = f.read()
        reconciled = "STATUS: PASS" in content
        checks.append({
            'name': 'Ledger Reconciliation',
            'metric': 'PASS' if reconciled else 'FAIL',
            'threshold': 'PASS',
            'status': 'PASS' if reconciled else 'FAIL'
        })
    else:
        checks.append({'name': 'Ledger Reconciliation', 'metric': 'N/A', 'threshold': 'PASS', 'status': 'FAIL (Missing File)'})

    # Final Verdict
    all_pass = all(c['status'] == 'PASS' for c in checks)
    verdict = "GO" if all_pass else "NO-GO"

    # Export Report
    report_path = os.path.join("audits", "rl_readiness_report.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("# RL Readiness Gate Report\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        f.write(f"## Final Verdict: {verdict}\n\n")
        
        f.write("## Gate Multi-Check Results\n\n")
        f.write("| Requirement | Metric | Threshold | Status |\n")
        f.write("|-------------|--------|-----------|--------|\n")
        for c in checks:
            f.write(f"| {c['name']} | {c['metric']} | {c['threshold']} | {c['status']} |\n")
            
        f.write("\n\n### Next Steps\n")
        if verdict == "GO":
            f.write("- [ ] Continue to PPO Fine-tuning Phase 1\n")
            f.write("- [ ] Enable higher-fidelity fill models\n")
        else:
            f.write("- [ ] Fix FAIL status items before proceeding\n")
            
    print(f"[GATE] Final verdict: {verdict}")
    print(f"[GATE] Report generated at {report_path}")

if __name__ == "__main__":
    run_readiness_gate()
