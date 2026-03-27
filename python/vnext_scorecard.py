import json

def generate_vnext_scorecard(metrics_rl: dict, steps: int) -> dict:
    """
    Evaluates vNext (Phase 3.5) metrics against architectural hard gates.
    Supports 10-action lifecycle space.
    """
    scorecard = {
        "status": "PASS",
        "reasons": [],
        "pathologies": [],
        "details": {}
    }

    # Action Mapping (vNext 10-action)
    # 0:HOLD, 1:OPEN_L, 2:ADD_L, 3:RED_L, 4:CLOSE_L, 5:OPEN_S, 6:ADD_S, 7:RED_S, 8:CLOSE_S, 9:REPRICE
    
    ad = metrics_rl.get("action_dist", {})
    hold_pct = ad.get("HOLD", 0.0)
    open_pct = ad.get("OPEN_LONG", 0.0) + ad.get("OPEN_SHORT", 0.0)
    add_pct = ad.get("ADD_LONG", 0.0) + ad.get("ADD_SHORT", 0.0)
    close_pct = ad.get("CLOSE_LONG", 0.0) + ad.get("CLOSE_SHORT", 0.0)
    
    maker_fills = metrics_rl.get("maker_fills", 0)
    toxic_fills = metrics_rl.get("toxic_fills", 0)
    pnl = metrics_rl.get("net_pnl", 0.0)

    # 1. Fail-Fast: Zero Activity
    if steps >= 50000 and maker_fills == 0:
        scorecard["status"] = "FAIL"
        scorecard["pathologies"].append("GATE_SUFFOCATION")
        scorecard["reasons"].append(f"Zero maker fills @ {steps} steps. Gates are too restrictive.")

    # 2. Fail-Fast: Conservative Collapse
    if hold_pct > 98.0 and steps >= 100000:
        scorecard["status"] = "FAIL"
        scorecard["pathologies"].append("CONSERVATIVE_COLLAPSE")
        scorecard["reasons"].append(f"HOLD level too high ({hold_pct:.2f}%). Policy failed to break deterministic floor.")

    # 3. Fail-Fast: Toxic Selection
    if maker_fills > 10:
        toxic_ratio = toxic_fills / maker_fills
        if toxic_ratio > 0.6 and steps >= 100000:
            scorecard["status"] = "FAIL"
            scorecard["pathologies"].append("MICROSTRUCTURAL_DEGENERATION")
            scorecard["reasons"].append(f"Toxic Fill Ratio ({toxic_ratio:.1%}) exceeds safety threshold (60%).")

    # 4. Fail-Fast: Semantic Collapse (User Requirement)
    exit_intent_pct = close_pct + ad.get("REDUCE_LONG", 0.0) + ad.get("REDUCE_SHORT", 0.0)
    if steps >= 50000 and exit_intent_pct < 0.05:
        scorecard["status"] = "FAIL"
        scorecard["pathologies"].append("SEMANTIC_COLLAPSE")
        scorecard["reasons"].append(f"Exit intent (REDUCE/CLOSE) dropped below 0.05% ({exit_intent_pct:.3f}%). Policy is ignoring exit lifecycle.")

    # 5. Fail-Fast: Out-of-Control ADD (User Requirement)
    if steps >= 50000 and add_pct > 15 * (open_pct + exit_intent_pct + 1e-8):
        scorecard["status"] = "FAIL"
        scorecard["pathologies"].append("ADD_DOMINANCE")
        scorecard["reasons"].append(f"ADD actions ({add_pct:.1f}%) dominating OPEN/EXIT. Potential winner trapping / trend bias.")

    # 6. Warn: Emergency Exit Abuse
    if close_pct > 5.0:
        scorecard["status"] = "WARN" if scorecard["status"] == "PASS" else scorecard["status"]
        scorecard["reasons"].append(f"CLOSE actions ({close_pct:.1f}%) high. Emergency gate might be too loose.")

    # 5. Economics
    if pnl < -2.0:
        scorecard["status"] = "FAIL"
        scorecard["reasons"].append(f"PnL Critical Drawdown: {pnl:.2f}%")

    # Recommendation
    if scorecard["status"] == "FAIL":
        scorecard["recommendation"] = "ABORT: Gate calibration or reward scaling failure."
    elif scorecard["status"] == "WARN":
        scorecard["recommendation"] = "ADJUST: Monitoring lifecycle transitions and gate sensitivity."
    else:
        scorecard["recommendation"] = "CONTINUE: vNext architecture is stable."

    return scorecard

if __name__ == "__main__":
    test_metrics = {"action_dist": {"HOLD": 99.5}, "maker_fills": 0, "net_pnl": -0.1}
    print(json.dumps(generate_vnext_scorecard(test_metrics, 100000), indent=2))
