import json

def generate_scorecard(metrics_rl: dict, metrics_bc: dict = None) -> dict:
    """
    Evaluates RL metrics against hard thresholds and (optional) BC baseline.
    Returns structurally identical evaluation dictionary with PASS/WARN/FAIL verdicts.
    """
    scorecard = {
        "status": "PASS", # Can be downgraded
        "reasons": [],
        "pathologies": [],
        "details": {}
    }

    # Extract deterministic metrics (fallback to stochastic if needed)
    det = metrics_rl.get("deterministic", metrics_rl)
    sto = metrics_rl.get("stochastic", metrics_rl)

    def _get_rate(dist, action):
        return dist.get(action, 0.0)

    hold_det = _get_rate(det.get("dist", {}), "HOLD")
    hold_sto = _get_rate(sto.get("dist", {}), "HOLD")
    
    maker_acts_det = sum([_get_rate(det.get("dist", {}), a) for a in ["POST_BID", "JOIN_BID", "POST_ASK", "JOIN_ASK"]])
    
    maker_fills = det.get("maker_fills", 0)
    toxic_fills = det.get("toxic_fills", 0)
    stale_expiries = det.get("stale_expiries", 0)
    pnl = det.get("pnl_pct", 0.0)
    
    # Check 1: Conservative Collapse
    if hold_det >= 99.0 and hold_sto >= 90.0:
        scorecard["pathologies"].append("CONSERVATIVE_COLLAPSE")
        scorecard["reasons"].append(f"Deterministic HOLD > 99% ({hold_det:.2f}%) and Stochastic HOLD > 90% ({hold_sto:.2f}%).")
        scorecard["status"] = "FAIL"
    elif hold_det >= 99.0:
        scorecard["status"] = "WARN" if scorecard["status"] == "PASS" else scorecard["status"]
        scorecard["reasons"].append(f"Deterministic is locked at HOLD ({hold_det:.2f}%), but stochastic explores. Needs more time.")

    # Check 2: Useless Hyperactivity
    cancels = _get_rate(det.get("dist", {}), "CLEAR_QUOTES")
    if maker_acts_det > 50.0 and maker_fills == 0:
        scorecard["pathologies"].append("USELESS_HYPERACTIVITY")
        scorecard["reasons"].append(f"Maker actions > 50% ({maker_acts_det:.2f}%) but yielding 0 maker fills.")
        scorecard["status"] = "FAIL"
    elif cancels > 20.0 and maker_fills == 0:
        scorecard["pathologies"].append("USELESS_HYPERACTIVITY")
        scorecard["reasons"].append(f"Spamming CLEAR_QUOTES ({cancels:.2f}%) with 0 fills.")
        scorecard["status"] = "FAIL"

    # Check 3: Microstructural Degradation
    if maker_fills > 0:
        toxic_ratio = toxic_fills / maker_fills
        if toxic_ratio >= 1.0:
            scorecard["pathologies"].append("MICROSTRUCTURAL_DEGRADATION")
            scorecard["reasons"].append(f"Toxic fills ({toxic_fills}) >= Maker Fills ({maker_fills}). Severe adverse selection.")
            scorecard["status"] = "FAIL"
        elif toxic_ratio > 0.5:
            scorecard["status"] = "WARN" if scorecard["status"] == "PASS" else scorecard["status"]
            scorecard["reasons"].append(f"Toxic ratio elevated: {toxic_ratio:.2f} (Toxic: {toxic_fills}, Maker: {maker_fills})")

    # Check 4: Action Degeneration
    for action in ["CLEAR_QUOTES", "CLOSE_POSITION", "OPEN_LONG", "OPEN_SHORT"]:
        rate = _get_rate(det.get("dist", {}), action)
        if rate > 40.0:
            scorecard["pathologies"].append("ACTION_DEGENERATION")
            scorecard["reasons"].append(f"Absurd single-action dominance: {action} is at {rate:.2f}%")
            scorecard["status"] = "FAIL"

    # Relative Checks (vs BC Baseline)
    if metrics_bc is not None:
        bc_det = metrics_bc.get("deterministic", metrics_bc)
        bc_pnl = bc_det.get("pnl_pct", 0.0)
        bc_maker_fills = bc_det.get("maker_fills", 0)

        # Better than baseline logic
        improved_pnl = pnl > bc_pnl
        improved_fills = maker_fills > bc_maker_fills

        if scorecard["status"] == "PASS" and not improved_pnl and pnl < -1.0:
            # If everything else is theoretically PASS but it's losing >1% and worse than BC
            scorecard["status"] = "WARN"
            scorecard["reasons"].append(f"Policy meets structural constraints but PnL degraded ({pnl:.2f}% vs BC {bc_pnl:.2f}%).")
            
        scorecard["details"]["vs_bc"] = {
            "pnl_diff": pnl - bc_pnl,
            "maker_fills_diff": maker_fills - bc_maker_fills
        }

    # Recommendation
    if scorecard["status"] == "PASS":
        scorecard["recommendation"] = "CONTINUE: Policy is structurally bound and extracting edge."
    elif scorecard["status"] == "WARN":
        scorecard["recommendation"] = "CONTINUE_WITH_CAUTION: Policy is structurally intact but degrading slightly or locked in exploration."
    else:
        scorecard["recommendation"] = "ABORT: Re-evaluate learning rate, reward geometry, or entropy coefficient."

    return scorecard

if __name__ == "__main__":
    # Test collapse
    fake_metrics = {"deterministic": {"dist": {"HOLD": 100.0}, "maker_fills": 0}, "stochastic": {"dist": {"HOLD": 95.0}}}
    print(generate_scorecard(fake_metrics))
