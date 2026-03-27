use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CandidateTimestamps {
    pub exchange_ts: i64,
    pub recv_ts: i64,
    pub feature_ts: i64,
    pub decision_ts: i64,
    pub order_intent_ts: i64,
    pub user_stream_ts: i64,
    pub simulated_fill_ts: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContrafactualOutcome {
    pub mode: String, // "Maker", "Taker"
    pub expected_net_edge_bps: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CandidateDecisionRecord {
    pub candidate_id: String,
    pub run_id: String,
    pub symbol: String,
    pub side_intended: String,
    pub target_qty: f64,
    pub target_notional: f64,
    
    // Features & Model
    pub regime_classification: String,
    pub exec_quality_score: f64,
    pub expected_move_bps: f64, // Keep for legacy parsers
    
    pub raw_model_value: f64,
    pub baseline_move_bps: f64,
    pub expected_move_bps_used: f64,
    pub cost_gate_mode: String,
    
    // Costs
    pub fee_bps_est: f64,
    pub spread_bps_est: f64,
    pub adverse_bps_penalty: f64,
    pub slip_bps_est: f64,
    pub expected_net_edge_bps: f64,
    
    // Outcome
    pub entry_mode_proposed: String,
    pub risk_mode: String,
    pub is_veto: bool,
    pub veto_reason: Option<String>,
    pub simulator_mode: String,
    
    pub contrafactuals: Vec<ContrafactualOutcome>,
    pub timestamps: CandidateTimestamps,
}
