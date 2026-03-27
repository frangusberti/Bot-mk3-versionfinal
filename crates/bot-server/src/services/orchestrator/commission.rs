use log::{info, warn};
use serde::{Serialize, Deserialize};

// ════════════════════════════════════════════════════════════════════════
//  M6: Commission Policy + Order Policy — Deterministic, Configurable
// ════════════════════════════════════════════════════════════════════════

// ────────────────────────────────────────────────────────────
//  Maker Timeout Policy
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Default, Serialize, Deserialize)]
pub enum MakerTimeoutPolicy {
    #[default]
    CancelAndSkip,
    ConvertToTaker,
}

// ────────────────────────────────────────────────────────────
//  Order Intent & Urgency
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum OrderIntent {
    Entry,
    Exit,
    StopLoss,
    #[allow(dead_code)]
    TakeProfit,
    RiskClose,
}

impl OrderIntent {
    pub fn is_emergency(&self) -> bool {
        matches!(self, Self::Exit | Self::StopLoss | Self::RiskClose)
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum UrgencyLevel {
    Normal,
    High,
}

// ────────────────────────────────────────────────────────────
//  Configuration (Runtime, Mutable, Serializable)
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CommissionPolicyConfig {
    pub prefer_maker: bool,
    pub allow_taker: bool,
    pub maker_fee_bps: f64,
    pub taker_fee_bps: f64,
    pub max_taker_ratio: f64,
    pub max_fee_bps_per_trade: f64,
    pub maker_entry_offset_bps: f64,
    pub maker_timeout_ms: u64,
    pub maker_timeout_policy: MakerTimeoutPolicy,
    pub allow_emergency_taker: bool,
    pub allow_override_for_emergency: bool,
    pub taker_ratio_window_sec: u32,
    pub min_spread_bps_for_maker: f64,
    pub max_spread_bps_for_entry: f64,
    pub require_book_for_market_slippage_est: bool,
}

impl Default for CommissionPolicyConfig {
    fn default() -> Self {
        Self {
            prefer_maker: true,
            allow_taker: true,
            maker_fee_bps: 2.0,
            taker_fee_bps: 5.0,
            max_taker_ratio: 0.35,
            max_fee_bps_per_trade: 12.0,
            maker_entry_offset_bps: 0.2,
            maker_timeout_ms: 1500,
            maker_timeout_policy: MakerTimeoutPolicy::CancelAndSkip,
            allow_emergency_taker: true,
            allow_override_for_emergency: true,
            taker_ratio_window_sec: 3600,
            min_spread_bps_for_maker: 0.5,
            max_spread_bps_for_entry: 5.0,
            require_book_for_market_slippage_est: true,
        }
    }
}

// Backward-compat alias
pub type CommissionPolicy = CommissionPolicyConfig;

// ────────────────────────────────────────────────────────────
//  Order Type Decision
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub enum OrderTypeDecision {
    Market,
    LimitPostOnly { limit_price: f64 },
    #[allow(dead_code)]
    StopMarket { stop_price: f64 },
}

// Backward-compat alias
#[derive(Clone, Debug)]
pub enum OrderDecision {
    UseMaker { price: f64 },
    UseTaker,
    Rejected(String),
}

// ────────────────────────────────────────────────────────────
//  Cost Estimate
// ────────────────────────────────────────────────────────────

#[allow(dead_code)]
#[derive(Clone, Debug)]
pub struct CostEstimate {
    pub fee_bps: f64,
    pub slippage_bps: f64,
    pub total_bps: f64,
}

// ────────────────────────────────────────────────────────────
//  Policy Reject
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub enum PolicyReject {
    TakerNotAllowed,
    CostTooHigh { total_bps: f64 },
    SpreadTooWide { spread_bps: f64, max: f64 },
    #[allow(dead_code)]
    BookMissing,
    TakerRatioExceeded { ratio: f64, max: f64 },
    Other(String),
}

impl std::fmt::Display for PolicyReject {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::TakerNotAllowed => write!(f, "Taker orders not allowed"),
            Self::CostTooHigh { total_bps } => write!(f, "Cost too high: {:.1} bps", total_bps),
            Self::SpreadTooWide { spread_bps, max } => write!(f, "Spread too wide: {:.1} bps > {:.1} bps", spread_bps, max),
            Self::BookMissing => write!(f, "Order book required but missing"),
            Self::TakerRatioExceeded { ratio, max } => write!(f, "Taker ratio exceeded: {:.2} > {:.2}", ratio, max),
            Self::Other(s) => write!(f, "{}", s),
        }
    }
}

// ────────────────────────────────────────────────────────────
//  Commission Statistics (runtime counters)
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, Default, Serialize)]
pub struct CommissionStats {
    pub maker_count: u64,
    pub taker_count: u64,
    pub total_fees_usdt: f64,
    pub total_notional_usdt: f64,
    // H4: Order attempt counters (incremented at decision time, not fill time)
    pub maker_order_count: u64,
    pub taker_order_count: u64,
}

#[allow(dead_code)]
impl CommissionStats {
    pub fn taker_ratio(&self) -> f64 {
        let total = self.maker_count + self.taker_count;
        if total == 0 { return 0.0; }
        self.taker_count as f64 / total as f64
    }

    pub fn avg_fee_bps(&self) -> f64 {
        if self.total_notional_usdt == 0.0 { return 0.0; }
        (self.total_fees_usdt / self.total_notional_usdt) * 10_000.0
    }

    pub fn record_fill(&mut self, is_taker: bool, fee_usdt: f64, notional_usdt: f64) {
        if is_taker {
            self.taker_count += 1;
        } else {
            self.maker_count += 1;
        }
        self.total_fees_usdt += fee_usdt;
        self.total_notional_usdt += notional_usdt;
    }

    pub fn maker_ratio(&self) -> f64 {
        1.0 - self.taker_ratio()
    }

    pub fn taker_order_ratio(&self) -> f64 {
        let total = self.maker_order_count + self.taker_order_count;
        if total == 0 { return 0.0; }
        self.taker_order_count as f64 / total as f64
    }

    pub fn record_order(&mut self, is_taker: bool) {
        if is_taker {
            self.taker_order_count += 1;
        } else {
            self.maker_order_count += 1;
        }
    }

    pub fn fees_paid(&self) -> f64 {
        self.total_fees_usdt
    }
}

// ────────────────────────────────────────────────────────────
//  Order Policy (stateful wrapper)
// ────────────────────────────────────────────────────────────

#[allow(dead_code)]
pub struct OrderPolicy {
    pub cfg: CommissionPolicyConfig,
    pub stats: CommissionStats,
}

#[allow(dead_code)]
impl OrderPolicy {
    pub fn new(cfg: CommissionPolicyConfig) -> Self {
        Self { cfg, stats: CommissionStats::default() }
    }

    /// Core decision engine: given market state and intent, decide order type and estimate cost.
    #[allow(clippy::too_many_arguments)]
    pub fn decide(
        &mut self,
        _now_ms: i64,
        intent: &OrderIntent,
        urgency: &UrgencyLevel,
        side: &str,
        best_bid: f64,
        best_ask: f64,
        slippage_bps_est: f64,
        expected_net_edge_bps: Option<f64>,
        is_dead_regime: bool,
    ) -> Result<(OrderTypeDecision, CostEstimate), PolicyReject> {
        let mid = if best_bid > 0.0 && best_ask > 0.0 {
            (best_bid + best_ask) / 2.0
        } else {
            return Err(PolicyReject::Other("No valid bid/ask".into()));
        };

        let spread_bps = if mid > 0.0 {
            ((best_ask - best_bid) / mid) * 10_000.0
        } else {
            0.0
        };

        // ── A) Emergency / High Urgency ──
        if *urgency == UrgencyLevel::High || intent.is_emergency() {
            let cost = CostEstimate {
                fee_bps: self.cfg.taker_fee_bps,
                slippage_bps: slippage_bps_est,
                total_bps: self.cfg.taker_fee_bps + slippage_bps_est,
            };

            // Fee guard (but emergencies override)
            if cost.total_bps > self.cfg.max_fee_bps_per_trade {
                if intent.is_emergency() && self.cfg.allow_override_for_emergency {
                    warn!(
                        r#"{{"event":"order_policy_decision","intent":"{:?}","urgency":"{:?}","decision":"Market","cost_bps":{:.1},"spread_bps":{:.1},"reason":"emergency_override"}}"#,
                        intent, urgency, cost.total_bps, spread_bps
                    );
                } else {
                    warn!(
                        r#"{{"event":"order_policy_reject","reason":"CostTooHigh","total_cost_bps":{:.1},"spread_bps":{:.1}}}"#,
                        cost.total_bps, spread_bps
                    );
                    return Err(PolicyReject::CostTooHigh { total_bps: cost.total_bps });
                }
            }

            if self.cfg.allow_emergency_taker || *urgency == UrgencyLevel::High {
                info!(
                    r#"{{"event":"order_policy_decision","intent":"{:?}","urgency":"{:?}","decision":"Market","cost_bps":{:.1},"spread_bps":{:.1},"reason":"emergency"}}"#,
                    intent, urgency, cost.total_bps, spread_bps
                );
                return Ok((OrderTypeDecision::Market, cost));
            }
        }

        // ── B) Entry with Normal urgency ──

        // Edge Gate
        let taker_cost_est = self.cfg.taker_fee_bps + slippage_bps_est;
        let mut force_maker_due_to_edge = false;
        
        if let Some(edge) = expected_net_edge_bps {
            if edge < 0.0 {
                return Err(PolicyReject::Other("Negative Net Edge pre-trade".to_string()));
            }
            if edge < taker_cost_est * 1.5 || is_dead_regime {
                force_maker_due_to_edge = true;
            }
        }

        // Spread gating
        if spread_bps > self.cfg.max_spread_bps_for_entry && *intent == OrderIntent::Entry {
            warn!(
                r#"{{"event":"order_policy_reject","reason":"SpreadTooWide","spread_bps":{:.1},"max":{:.1}}}"#,
                spread_bps, self.cfg.max_spread_bps_for_entry
            );
            return Err(PolicyReject::SpreadTooWide {
                spread_bps,
                max: self.cfg.max_spread_bps_for_entry,
            });
        }

        // H4: Taker ratio check uses ORDER ratio (not fill ratio) for earlier detection
        let total_orders = self.stats.maker_order_count + self.stats.taker_order_count;
        let taker_ratio_exceeded = total_orders >= 10 && self.stats.taker_order_ratio() > self.cfg.max_taker_ratio;

        if self.cfg.prefer_maker || force_maker_due_to_edge {
            // Check if spread is too tight to bother making
            if force_maker_due_to_edge && spread_bps < self.cfg.min_spread_bps_for_maker && !self.cfg.prefer_maker {
                // If edge is weak but spread is tight, and we don't naturally prefer maker, this trade is just unviable.
                 return Err(PolicyReject::Other(format!("Weak edge ({:?}) and spread too tight for Maker ({:.1} < {:.1})", expected_net_edge_bps, spread_bps, self.cfg.min_spread_bps_for_maker)));
            }

            // H3: Maker path — price based on best_bid/best_ask, NOT mid
            let limit_price = if side == "Buy" {
                // Place at best_bid + offset (slight improvement for fill probability)
                let base = best_bid;
                let offset = base * self.cfg.maker_entry_offset_bps / 10_000.0;
                let candidate = base + offset;
                // SAFETY: never cross the spread (must stay < best_ask)
                if candidate >= best_ask { best_bid } else { candidate }
            } else {
                // Place at best_ask - offset
                let base = best_ask;
                let offset = base * self.cfg.maker_entry_offset_bps / 10_000.0;
                let candidate = base - offset;
                // SAFETY: never cross the spread (must stay > best_bid)
                if candidate <= best_bid { best_ask } else { candidate }
            };

            let cost = CostEstimate {
                fee_bps: self.cfg.maker_fee_bps,
                slippage_bps: 0.0,
                total_bps: self.cfg.maker_fee_bps,
            };

            // Fee guard
            if cost.total_bps > self.cfg.max_fee_bps_per_trade {
                warn!(
                    r#"{{"event":"order_policy_reject","reason":"CostTooHigh","total_cost_bps":{:.1}}}"#,
                    cost.total_bps
                );
                return Err(PolicyReject::CostTooHigh { total_bps: cost.total_bps });
            }

            info!(
                r#"{{"event":"order_policy_decision","intent":"{:?}","urgency":"Normal","decision":"LimitPostOnly","limit_price":{:.2},"cost_bps":{:.1},"spread_bps":{:.1}}}"#,
                intent, limit_price, cost.total_bps, spread_bps
            );

            // H4: Record maker order attempt
            self.stats.record_order(false);

            return Ok((OrderTypeDecision::LimitPostOnly { limit_price }, cost));
        }

        // Non-maker-preferred path
        if taker_ratio_exceeded {
            warn!(
                r#"{{"event":"taker_ratio_exceeded","ratio":{:.2},"max_ratio":{:.2},"action":"block_entry"}}"#,
                self.stats.taker_ratio(), self.cfg.max_taker_ratio
            );
            return Err(PolicyReject::TakerRatioExceeded {
                ratio: self.stats.taker_ratio(),
                max: self.cfg.max_taker_ratio,
            });
        }

        if !self.cfg.allow_taker {
            return Err(PolicyReject::TakerNotAllowed);
        }

        // Taker fallback
        let cost = CostEstimate {
            fee_bps: self.cfg.taker_fee_bps,
            slippage_bps: slippage_bps_est,
            total_bps: self.cfg.taker_fee_bps + slippage_bps_est,
        };

        if cost.total_bps > self.cfg.max_fee_bps_per_trade {
            return Err(PolicyReject::CostTooHigh { total_bps: cost.total_bps });
        }

        info!(
            r#"{{"event":"order_policy_decision","intent":"{:?}","urgency":"Normal","decision":"Market","cost_bps":{:.1},"spread_bps":{:.1}}}"#,
            intent, cost.total_bps, spread_bps
        );

        // H4: Record taker order attempt
        self.stats.record_order(true);

        Ok((OrderTypeDecision::Market, cost))
    }

    /// Record a fill event to update stats
    pub fn on_fill(&mut self, is_taker: bool, fee_usdt: f64, notional_usdt: f64) {
        self.stats.record_fill(is_taker, fee_usdt, notional_usdt);
    }

    /// Hot-update config
    pub fn update_config(&mut self, cfg: CommissionPolicyConfig) {
        info!(
            r#"{{"event":"commission_config_update","maker_fee":"{:.1}→{:.1}","taker_fee":"{:.1}→{:.1}"}}"#,
            self.cfg.maker_fee_bps, cfg.maker_fee_bps,
            self.cfg.taker_fee_bps, cfg.taker_fee_bps,
        );
        self.cfg = cfg;
    }

    /// Status snapshot for GUI
    pub fn status(&self) -> &CommissionStats {
        &self.stats
    }
}

// ────────────────────────────────────────────────────────────
//  Backward-Compat: Legacy decide_order_type Function
// ────────────────────────────────────────────────────────────

/// Legacy compatibility wrapper around OrderPolicy.decide()
#[allow(clippy::too_many_arguments)]
pub fn decide_order_type(
    policy: &CommissionPolicy,
    stats: &CommissionStats,
    intent: &OrderIntent,
    urgency: &UrgencyLevel,
    side: &str,
    best_bid: f64,
    best_ask: f64,
    _fee_maker_bps: f64,
    _fee_taker_bps: f64,
    slippage_bps: f64,
    expected_net_edge_bps: Option<f64>,
    is_dead_regime: bool,
) -> OrderDecision {
    let mut op = OrderPolicy {
        cfg: policy.clone(),
        stats: stats.clone(),
    };

    match op.decide(0, intent, urgency, side, best_bid, best_ask, slippage_bps, expected_net_edge_bps, is_dead_regime) {
        Ok((OrderTypeDecision::LimitPostOnly { limit_price }, _)) => {
            OrderDecision::UseMaker { price: limit_price }
        }
        Ok((OrderTypeDecision::Market, _)) | Ok((OrderTypeDecision::StopMarket { .. }, _)) => {
            OrderDecision::UseTaker
        }
        Err(e) => {
            OrderDecision::Rejected(e.to_string())
        }
    }
}

// ════════════════════════════════════════════════════════════════════════
//  Tests
// ════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    fn default_cfg() -> CommissionPolicyConfig {
        CommissionPolicyConfig::default()
    }

    fn make_policy() -> OrderPolicy {
        OrderPolicy::new(default_cfg())
    }

    // ── Entry Maker Decision ──

    #[test]
    fn test_entry_maker_decision() {
        let mut op = make_policy();
        let result = op.decide(0, &OrderIntent::Entry, &UrgencyLevel::Normal, "Buy", 50000.0, 50010.0, 1.0, None, false);
        match result {
            Ok((OrderTypeDecision::LimitPostOnly { limit_price }, cost)) => {
                // H3: Buy limit = best_bid + offset (not mid-based)
                let expected = 50000.0 + 50000.0 * 0.2 / 10_000.0; // 50001.0
                assert!((limit_price - expected).abs() < 0.01, "Got {}, expected {}", limit_price, expected);
                assert_eq!(cost.fee_bps, 2.0);
                assert_eq!(cost.slippage_bps, 0.0);
            }
            other => panic!("Expected LimitPostOnly, got {:?}", other),
        }
    }

    #[test]
    fn test_entry_sell_maker() {
        let mut op = make_policy();
        let result = op.decide(0, &OrderIntent::Entry, &UrgencyLevel::Normal, "Sell", 50000.0, 50010.0, 1.0, None, false);
        match result {
            Ok((OrderTypeDecision::LimitPostOnly { limit_price }, _)) => {
                // H3: Sell limit = best_ask - offset (not mid-based)
                let expected = 50010.0 - 50010.0 * 0.2 / 10_000.0; // 50009.0
                assert!((limit_price - expected).abs() < 0.01, "Got {}, expected {}", limit_price, expected);
            }
            other => panic!("Expected LimitPostOnly, got {:?}", other),
        }
    }

    // ── Emergency Exit ──

    #[test]
    fn test_exit_emergency_forces_market() {
        let mut op = make_policy();
        let result = op.decide(0, &OrderIntent::StopLoss, &UrgencyLevel::High, "Sell", 50000.0, 50010.0, 1.0, None, false);
        match result {
            Ok((OrderTypeDecision::Market, cost)) => {
                assert_eq!(cost.fee_bps, 5.0);
                assert_eq!(cost.total_bps, 6.0);
            }
            other => panic!("Expected Market, got {:?}", other),
        }
    }

    #[test]
    fn test_risk_close_always_market() {
        let mut op = OrderPolicy::new(CommissionPolicyConfig {
            prefer_maker: true,
            allow_taker: false,
            max_fee_bps_per_trade: 1.0,
            ..default_cfg()
        });
        let result = op.decide(0, &OrderIntent::RiskClose, &UrgencyLevel::High, "Sell", 50000.0, 50010.0, 5.0, None, false);
        assert!(matches!(result, Ok((OrderTypeDecision::Market, _))));
    }

    // ── Spread Gating ──

    #[test]
    fn test_spread_too_wide_rejected() {
        let mut op = OrderPolicy::new(CommissionPolicyConfig {
            max_spread_bps_for_entry: 3.0,
            ..default_cfg()
        });
        // Spread = (50100 - 50000) / 50050 * 10000 ≈ 20 bps >> 3 bps
        let result = op.decide(0, &OrderIntent::Entry, &UrgencyLevel::Normal, "Buy", 50000.0, 50100.0, 1.0, None, false);
        assert!(matches!(result, Err(PolicyReject::SpreadTooWide { .. })));
    }

    // ── Cost Guard ──

    #[test]
    fn test_cost_too_high_non_emergency() {
        let mut op = OrderPolicy::new(CommissionPolicyConfig {
            prefer_maker: false,
            max_fee_bps_per_trade: 3.0,
            ..default_cfg()
        });
        // taker 5 bps + slip 2 bps = 7 bps > 3 bps limit
        let result = op.decide(0, &OrderIntent::Entry, &UrgencyLevel::Normal, "Buy", 50000.0, 50001.0, 2.0, None, false);
        assert!(matches!(result, Err(PolicyReject::CostTooHigh { .. })));
    }

    #[test]
    fn test_emergency_overrides_cost_guard() {
        let mut op = OrderPolicy::new(CommissionPolicyConfig {
            max_fee_bps_per_trade: 3.0,
            allow_override_for_emergency: true,
            ..default_cfg()
        });
        // taker 5 + slip 2 = 7 bps > 3 limit, but emergency override
        let result = op.decide(0, &OrderIntent::StopLoss, &UrgencyLevel::High, "Sell", 50000.0, 50001.0, 2.0, None, false);
        assert!(matches!(result, Ok((OrderTypeDecision::Market, _))));
    }

    // ── Taker Ratio ──

    #[test]
    fn test_taker_ratio_exceeded_blocks_entry() {
        let mut op = OrderPolicy::new(CommissionPolicyConfig {
            prefer_maker: false,
            allow_taker: true,
            max_taker_ratio: 0.30,
            ..default_cfg()
        });
        // H4: Ratio enforcement uses ORDER counts, not fill counts
        op.stats = CommissionStats {
            maker_order_count: 6,
            taker_order_count: 4, // ratio = 0.40 > 0.30
            ..Default::default()
        };
        let result = op.decide(0, &OrderIntent::Entry, &UrgencyLevel::Normal, "Buy", 50000.0, 50001.0, 1.0, None, false);
        assert!(matches!(result, Err(PolicyReject::TakerRatioExceeded { .. })));
    }

    // ── Stats Tracking ──

    #[test]
    fn test_commission_stats_tracking() {
        let mut stats = CommissionStats::default();
        stats.record_fill(true, 2.0, 50000.0);   // taker
        stats.record_fill(false, 1.0, 50000.0);  // maker
        stats.record_fill(true, 2.0, 50000.0);   // taker

        assert_eq!(stats.taker_count, 2);
        assert_eq!(stats.maker_count, 1);
        assert!((stats.taker_ratio() - 0.6667).abs() < 0.01);
        assert_eq!(stats.total_fees_usdt, 5.0);
        assert!((stats.avg_fee_bps() - 0.333).abs() < 0.01);
    }

    #[test]
    fn test_on_fill_updates_stats() {
        let mut op = make_policy();
        op.on_fill(true, 2.5, 50000.0);
        op.on_fill(false, 1.0, 50000.0);
        assert_eq!(op.stats.taker_count, 1);
        assert_eq!(op.stats.maker_count, 1);
        assert_eq!(op.stats.total_fees_usdt, 3.5);
    }

    // ── Legacy Compat ──

    #[test]
    fn test_legacy_decide_order_type_maker() {
        let policy = CommissionPolicy::default();
        let stats = CommissionStats::default();
        let decision = decide_order_type(
            &policy, &stats, &OrderIntent::Entry, &UrgencyLevel::Normal,
            "Buy", 50000.0, 50010.0, 2.0, 4.0, 1.0, None, false
        );
        match decision {
            OrderDecision::UseMaker { .. } => {},
            _ => panic!("Expected UseMaker, got {:?}", decision),
        }
    }

    #[test]
    fn test_legacy_decide_stoploss_taker() {
        let policy = CommissionPolicy { prefer_maker: true, allow_taker: false, ..CommissionPolicy::default() };
        let stats = CommissionStats::default();
        let decision = decide_order_type(
            &policy, &stats, &OrderIntent::StopLoss, &UrgencyLevel::High,
            "Sell", 50000.0, 50010.0, 2.0, 4.0, 1.0, None, false
        );
        match decision {
            OrderDecision::UseTaker => {},
            _ => panic!("Expected UseTaker, got {:?}", decision),
        }
    }

    // ── Config Update ──

    #[test]
    fn test_config_update() {
        let mut op = make_policy();
        op.update_config(CommissionPolicyConfig {
            maker_fee_bps: 1.5,
            taker_fee_bps: 3.5,
            ..default_cfg()
        });
        assert_eq!(op.cfg.maker_fee_bps, 1.5);
        assert_eq!(op.cfg.taker_fee_bps, 3.5);
    }
}
