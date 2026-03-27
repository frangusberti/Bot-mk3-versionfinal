use log::{warn, error};
use std::collections::VecDeque;

use crate::services::analytics::candidate::CandidateDecisionRecord;
use crate::services::analytics::engine::SimVsRealDivergence;

#[derive(Debug, Default)]
pub struct AlertMonitor {
    pub consecutive_vetoes: usize,
    pub divergence_slips_bps: VecDeque<f64>,
}

impl AlertMonitor {
    pub fn new() -> Self {
        Self {
            consecutive_vetoes: 0,
            divergence_slips_bps: VecDeque::with_capacity(50),
        }
    }

    pub fn check_candidate(&mut self, cand: &CandidateDecisionRecord) {
        // 1. Latency Spike (Exchange -> Recv)
        if cand.timestamps.exchange_ts > 0 && cand.timestamps.recv_ts > 0 {
            let latency_ms = cand.timestamps.recv_ts - cand.timestamps.exchange_ts;
            // Handle possible negative due to slight clock drift if not same machine
            let safe_latency = latency_ms.max(0);
            if safe_latency > 250 {
                warn!("[ALERT] Exchange->Recv latency spike: {}ms on {}", safe_latency, cand.symbol);
            }
        }

        // Processing latency (Recv -> Decision)
        if cand.timestamps.recv_ts > 0 && cand.timestamps.decision_ts > 0 {
            let processing_ms = cand.timestamps.decision_ts - cand.timestamps.recv_ts;
            let safe_proc = processing_ms.max(0);
            if safe_proc > 150 {
                warn!("[ALERT] High local processing latency: {}ms on {}", safe_proc, cand.symbol);
            }
        }

        // 2. High Vetoes
        // DeadMarket regime is a valid reason to veto continually.
        let is_dead = cand.regime_classification.contains("Dead:") && !cand.regime_classification.contains("Trend:0.00");
        
        if cand.is_veto {
            if !is_dead {
                self.consecutive_vetoes += 1;
            }
        } else {
            self.consecutive_vetoes = 0;
        }

        if self.consecutive_vetoes >= 150 {
            error!("[ALERT] Over 150 consecutive candidate vetoes in active market for {}. Check Alpha/Cost calibration.", cand.symbol);
            self.consecutive_vetoes = 0; // Reset to avoid spam
        }
    }

    pub fn check_divergence(&mut self, div: &SimVsRealDivergence) {
        // 3. Fill Divergence Bias
        if let Some(real_px) = div.realized_price {
            // Signed bps difference (Positive means real price was WORSE than expected)
            let raw_slip = if div.side == bot_data::simulation::structs::Side::Buy {
                (real_px - div.expected_price) / div.expected_price
            } else {
                (div.expected_price - real_px) / div.expected_price
            };
            
            let slip_bps = raw_slip * 10000.0;
            self.divergence_slips_bps.push_back(slip_bps);
            if self.divergence_slips_bps.len() > 100 {
                self.divergence_slips_bps.pop_front();
            }

            if self.divergence_slips_bps.len() >= 20 {
                let avg_bias: f64 = self.divergence_slips_bps.iter().sum::<f64>() / self.divergence_slips_bps.len() as f64;
                if avg_bias > 3.0 {
                    error!("[ALERT] Persistent Slippage Bias: Real fills are >3 bps WORSE than Sim on average (N={}).", self.divergence_slips_bps.len());
                } else if avg_bias < -3.0 {
                    warn!("[ALERT] Conservative Bias: Real fills are >3 bps BETTER than Sim on average (N={}).", self.divergence_slips_bps.len());
                }
            }
        }

        if div.delay_ms.abs() > 3000 {
            warn!("[ALERT] Fill Event delay between Sim/Real is unusually high: {}ms on {}", div.delay_ms, div.symbol);
        }
    }
}
