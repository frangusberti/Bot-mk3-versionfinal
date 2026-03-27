import json

def generate_temporal_scorecard(metrics: dict) -> dict:
    sc = {"status": "PASS", "flags": [], "reasons": []}
    
    if metrics.get("causal_violations", 0) > 0:
        sc["status"] = "FAIL"
        sc["reasons"].append(f"{metrics['causal_violations']} Causal Violations detected (recv_ts > decision_ts).")
        
    if metrics.get("stale_ob_count", 0) > (0.05 * metrics.get("total_snapshots", 1)):
        sc["status"] = "FAIL" if sc["status"] == "PASS" else sc["status"]
        sc["reasons"].append(">5% of snapshots had un-masked STALE orderbook.")
        
    return sc

def generate_trade_scorecard(metrics: dict) -> dict:
    sc = {"status": "PASS", "flags": [], "reasons": []}
    
    if metrics.get("accounting_errors_count", 0) > 0:
        sc["status"] = "FAIL"
        sc["reasons"].append(f"FATAL: {metrics['accounting_errors_count']} trades failed PnL-Equity accounting match.")
        
    if metrics.get("net_pnl_total", 0) < 0 and sc["status"] == "PASS":
        sc["status"] = "WARN"
        sc["reasons"].append("Session netted structurally negative PnL. Trade logics functioning but uneconomic.")
        
    return sc

def build_master_report(temp_sc: dict, trade_sc: dict, parity_sc: dict = None) -> str:
    md = "# BotMK3 Master Audit Report\n\n"
    
    md += f"## 1. Temporal Constraints: **{temp_sc['status']}**\n"
    for r in temp_sc["reasons"]: md += f"- {r}\n"
    
    if parity_sc:
        md += f"\n## 2. Replay Parity: **{parity_sc['status']}**\n"
        if "reason" in parity_sc: md += f"- {parity_sc['reason']}\n"
        for f in parity_sc.get("failed_features", []): md += f"- Deviation on {f['feature']}\n"
        
    md += f"\n## 3. Trade Accounting & PnL: **{trade_sc['status']}**\n"
    for r in trade_sc["reasons"]: md += f"- {r}\n"
        
    return md
