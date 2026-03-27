import os
import pandas as pd
import json
from datetime import datetime

def run_economics_audit(ledger_path="audits/runs_audit/trades_live/trade_audit_full.csv"):
    print(f"[AUDIT] Starting Economics Audit on {ledger_path}")
    
    if not os.path.exists(ledger_path):
        print(f"[ERROR] Ledger not found: {ledger_path}")
        return

    df = pd.read_csv(ledger_path)
    
    if len(df) == 0:
        print("[WARN] Empty ledger. Skipping economics analysis.")
        return

    # 1. Core Economics
    total_net_pnl = df['net_pnl'].sum()
    total_gross_pnl = df['gross_pnl'].sum()
    total_fees = df['fees_total'].sum()
    
    gross_to_fee_ratio = abs(total_gross_pnl / total_fees) if total_fees != 0 else 0
    
    # 2. Profit Factor (Gross)
    wins = df[df['gross_pnl'] > 0]['gross_pnl'].sum()
    losses = abs(df[df['gross_pnl'] < 0]['gross_pnl'].sum())
    profit_factor_gross = wins / losses if losses != 0 else wins if wins > 0 else 0
    
    # 3. Holding Time Analysis
    # Convert holding_time_ms to seconds
    df['hold_sec'] = df['holding_time_ms'] / 1000.0
    avg_hold_sec = df['hold_sec'].mean()
    
    # 4. Fill Microstructure
    # (Assuming we have entry_type if we added it, or we infer from fees)
    # For now, we use existing columns
    
    metrics = {
        'total_trades': len(df),
        'total_net_pnl': float(total_net_pnl),
        'total_gross_pnl': float(total_gross_pnl),
        'total_fees': float(total_fees),
        'gross_to_fee_ratio': float(gross_to_fee_ratio),
        'profit_factor_gross': float(profit_factor_gross),
        'avg_holding_time_sec': float(avg_hold_sec),
        'win_rate_gross': float(len(df[df['gross_pnl'] > 0]) / len(df)),
    }

    output_dir = os.path.dirname(ledger_path)
    report_path = os.path.join(output_dir, "economics_report.md")
    metrics_path = os.path.join(output_dir, "economics_metrics.json")
    
    with open(metrics_path, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=4)
        
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("# Economics Audit Report\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        
        f.write("## Performance Scorecard\n\n")
        f.write(f"| Metric | Value | Threshold | Status |\n")
        f.write(f"|--------|-------|-----------|--------|\n")
        f.write(f"| Total Trades | {metrics['total_trades']} | > 10 | {'✅ PASS' if metrics['total_trades'] > 10 else '❌ FAIL'} |\n")
        f.write(f"| Profit Factor (Gross) | {metrics['profit_factor_gross']:.2f} | > 1.0 | {'✅ PASS' if metrics['profit_factor_gross'] > 1.0 else '⚠️ WARN'} |\n")
        f.write(f"| Gross/Fee Ratio | {metrics['gross_to_fee_ratio']:.2f} | > 2.0 | {'✅ PASS' if metrics['gross_to_fee_ratio'] > 2.0 else '⚠️ WARN'} |\n")
        f.write(f"| Avg Holding Time | {metrics['avg_holding_time_sec']:.1f}s | < 300s | {'✅ PASS' if metrics['avg_holding_time_sec'] < 300 else '⚠️ SLOW'} |\n\n")
        
        f.write("## Financial Totals\n\n")
        f.write(f"- **Gross PnL:** {metrics['total_gross_pnl']:.4f} USDT\n")
        f.write(f"- **Total Fees:** {metrics['total_fees']:.4f} USDT\n")
        f.write(f"- **Net PnL:** {metrics['total_net_pnl']:.4f} USDT\n")
        
    print(f"[AUDIT] Economics report generated at {report_path}")

if __name__ == "__main__":
    run_economics_audit()
