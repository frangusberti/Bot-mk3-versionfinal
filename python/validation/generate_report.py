import os
import glob
import json
import pandas as pd
from datetime import datetime

# Path where Rust exports the ablated Backtest reports
RUNS_DIR = os.getenv("RUNS_DIR", "../../runs/validations")

def generate_ablation_markdown(reports_path):
    print(f"Scanning for validation JSON reports in: {reports_path}")
    pattern = os.path.join(reports_path, "*_ablation*.json")
    files = glob.glob(pattern)
    
    if not files:
        print("No JSON ablation reports found. Run backtest engine first.")
        return

    data = []
    for f in files:
        try:
            with open(f, "r") as rfile:
                report = json.load(rfile)
                # Parse breakdown
                data.append({
                    "strategy": report.get("strategy", "Unknown"),
                    "ablation_mode": report.get("ablation_mode", "FullSystem"),
                    "window_id": report.get("window_id", 0),
                    "net_pnl": report.get("net_pnl", 0.0),
                    "gross_pnl": report.get("gross_pnl", 0.0),
                    "fee_drag": report.get("fee_drag", 0.0),
                    "slippage_drag": report.get("slippage_drag", 0.0),
                    "total_trades": report.get("total_trades", 0),
                    "win_rate": report.get("win_rate", 0.0)
                })
        except Exception as e:
            print(f"Error reading {f}: {e}")

    df = pd.DataFrame(data)
    if df.empty:
        return
        
    # Aggregate metric averages across Walk-Forward windows per Ablation
    agg_df = df.groupby('ablation_mode').agg({
        'net_pnl': 'sum',
        'gross_pnl': 'sum',
        'fee_drag': 'sum',
        'slippage_drag': 'sum',
        'total_trades': 'sum',
        'win_rate': 'mean'
    }).reset_index()

    # Calculate differences against FullSystem
    baseline = agg_df[agg_df["ablation_mode"] == "FullSystem"]
    baseline_pnl = baseline.iloc[0]["net_pnl"] if not baseline.empty else 0.0
    
    agg_df['pnl_diff_from_baseline'] = agg_df['net_pnl'] - baseline_pnl
    agg_df['pct_impact'] = (agg_df['pnl_diff_from_baseline'] / abs(baseline_pnl)) * 100 if baseline_pnl != 0 else 0

    # Format Markdown
    md_lines = []
    md_lines.append(f"# BOTMK3 Ablation Matrix Report")
    md_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append("")
    md_lines.append("## Walk-Forward Ablation Summary")
    md_lines.append(f"Analyzed {len(files)} JSON window exports.")
    md_lines.append("")
    
    # Table Header
    headers = ["Ablation Mode", "Net PnL", "Gross PnL", "Fee Drag", "Slip Drag", "Trades", "WinRate", "Delta vs Baseline (%)"]
    md_lines.append("| " + " | ".join(headers) + " |")
    md_lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    
    for _, row in agg_df.iterrows():
        l_parts = [
            f"**{row['ablation_mode']}**",
            f"${row['net_pnl']:.2f}",
            f"${row['gross_pnl']:.2f}",
            f"${row['fee_drag']:.2f}",
            f"${row['slippage_drag']:.2f}",
            f"{row['total_trades']}",
            f"{row['win_rate']:.1%}",
            f"{row['pct_impact']:+.1f}%"
        ]
        md_lines.append("| " + " | ".join(l_parts) + " |")

    # Output md
    out_path = os.path.join(reports_path, "Ablation_Matrix.md")
    with open(out_path, "w") as out_f:
        out_f.write("\n".join(md_lines))
        
    print(f"Matrix Markdown Successfully generated at {out_path}")

if __name__ == "__main__":
    generate_ablation_markdown(RUNS_DIR)
