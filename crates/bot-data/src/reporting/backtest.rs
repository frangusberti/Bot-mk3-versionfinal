use serde::{Serialize, Deserialize};


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestReport {
    pub brain_id: String,
    pub dataset_id: String,
    pub symbol: String,
    pub start_ts: i64,
    pub end_ts: i64,
    
    // Core Metrics
    pub total_trades: u64,
    pub win_rate: f64,
    pub profit_factor: f64,
    pub gross_pnl: f64,
    pub fee_drag: f64,
    pub slippage_drag: f64,
    pub net_pnl: f64,
    pub max_drawdown_pct: f64,
    pub max_drawdown_abs: f64,
    pub sharpe_ratio: f64,
    pub volatility_annualized: f64,
    
    // Trade Stats
    pub avg_win: f64,
    pub avg_loss: f64,
    pub avg_trade: f64,
    pub fees_paid: f64,
    
    // Time Stats
    pub exposure_time_pct: f64,
    
    // Artifacts / Series (Simplified)
    pub equity_curve: Vec<EquityPoint>,
    pub trades: Vec<TradeRecord>,
    pub executions: Vec<ExecutionRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionRecord {
    pub symbol: String,
    pub side: String,
    pub qty: f64,
    pub price: f64,
    pub fee: f64,
    pub ts: i64,
    pub order_type: String,
    pub slippage_bps: f64,
    pub liquidity_flag: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EquityPoint {
    pub ts: i64,
    pub equity: f64,
    pub drawdown_pct: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradeRecord {
    pub entry_ts: i64,
    pub exit_ts: i64,
    pub side: String,
    pub entry_price: f64,
    pub exit_price: f64,
    pub qty: f64,
    pub pnl: f64,
    pub fees: f64,
    pub exit_reason: String,
    pub entry_slippage_bps: f64,
    pub exit_slippage_bps: f64,
    pub entry_liquidity_flag: String,
    pub exit_liquidity_flag: String,
}

impl BacktestReport {
    pub fn new(brain_id: String, dataset_id: String, symbol: String) -> Self {
        Self {
            brain_id,
            dataset_id,
            symbol,
            start_ts: 0,
            end_ts: 0,
            total_trades: 0,
            win_rate: 0.0,
            profit_factor: 0.0,
            gross_pnl: 0.0,
            fee_drag: 0.0,
            slippage_drag: 0.0,
            net_pnl: 0.0,
            max_drawdown_pct: 0.0,
            max_drawdown_abs: 0.0,
            sharpe_ratio: 0.0,
            volatility_annualized: 0.0,
            avg_win: 0.0,
            avg_loss: 0.0,
            avg_trade: 0.0,
            fees_paid: 0.0,
            exposure_time_pct: 0.0,
            equity_curve: Vec::new(),
            trades: Vec::new(),
            executions: Vec::new(),
        }
    }
    
    pub fn compute_metrics(&mut self) {
        if self.trades.is_empty() {
             return;
        }
        
        self.total_trades = self.trades.len() as u64;
        let mut wins = 0;
        let mut gross_profit = 0.0;
        let mut gross_loss = 0.0;
        
        for t in &self.trades {
            if t.pnl > 0.0 {
                wins += 1;
                gross_profit += t.pnl;
            } else {
                gross_loss += t.pnl.abs();
            }
            self.fees_paid += t.fees;
            self.slippage_drag += t.qty * t.entry_price * (t.entry_slippage_bps / 10000.0) 
                                + t.qty * t.exit_price * (t.exit_slippage_bps / 10000.0);
        }
        
        self.gross_pnl = gross_profit - gross_loss;
        self.fee_drag = self.fees_paid;
        self.net_pnl = self.gross_pnl - self.fees_paid;
        
        // Assuming TradeRecord.pnl is NET.
        // Let's assume PnL in TradeRecord is Gross for now, or Net?
        // Standard convention: Net PnL.
        
        if self.total_trades > 0 {
            self.win_rate = wins as f64 / self.total_trades as f64;
            self.avg_trade = self.net_pnl / self.total_trades as f64;
        }
        
        if gross_loss > 0.0 {
            self.profit_factor = gross_profit / gross_loss;
        } else if gross_profit > 0.0 {
            self.profit_factor = 999.0;
        }
        
        // Max DD
        let mut peak = -9999999.0;
        let mut max_dd_val = 0.0;
        let mut max_dd_pct = 0.0;
        
        for pt in &self.equity_curve {
            if pt.equity > peak {
                peak = pt.equity;
            }
            let dd = peak - pt.equity;
            let dd_pct = if peak > 0.0 { dd / peak } else { 0.0 };
            
            if dd > max_dd_val { max_dd_val = dd; }
            if dd_pct > max_dd_pct { max_dd_pct = dd_pct; }
        }
        self.max_drawdown_abs = max_dd_val;
        self.max_drawdown_pct = max_dd_pct;
        
        // Sharpe (simplified: based on daily returns or per-trade?)
        // Per-trade sharpe is common in HFT but daily in funds.
        // We'll calculate "per-trade" stability for now or equity curve returns.
    }
    
    pub fn reconstruct_trades_from_executions(&mut self) {
        // FIFO matching
        // Simple implementation: assume single position tracking for simplicity (no hedging mode supported here)
        // If side flips, close previous and open new.
        
        // We will match fills to create TradeRecords without full accounting re-implementation
        // Assuming executions are chronological.
        
        // Let's implement FIFO queue of open lots.
        struct Lot {
            qty: f64,
            price: f64,
            ts: i64,
            side: String,
            slippage_bps: f64,
            liquidity_flag: String,
            fee_per_unit: f64,
        }
        
        let mut lots: std::collections::VecDeque<Lot> = std::collections::VecDeque::new();
        
        for exec in &self.executions {
            let exec_side = exec.side.as_str();
            
            let mut qty_remaining = exec.qty;
            
            while qty_remaining > 1e-9 {
                if lots.is_empty() {
                    lots.push_back(Lot {
                        qty: qty_remaining,
                        price: exec.price,
                        ts: exec.ts,
                        side: exec.side.clone(),
                        slippage_bps: exec.slippage_bps,
                        liquidity_flag: exec.liquidity_flag.clone(),
                        fee_per_unit: exec.fee / exec.qty,
                    });
                    qty_remaining = 0.0;
                } else {
                    let front = lots.front().unwrap();
                    if front.side == exec_side {
                        // Add to position
                        lots.push_back(Lot {
                            qty: qty_remaining,
                            price: exec.price,
                            ts: exec.ts,
                            side: exec.side.clone(),
                            slippage_bps: exec.slippage_bps,
                            liquidity_flag: exec.liquidity_flag.clone(),
                            fee_per_unit: exec.fee / exec.qty,
                        });
                        qty_remaining = 0.0;
                    } else {
                        // Close position (Match)
                        let front_qty = front.qty;
                        let match_qty = f64::min(front_qty, qty_remaining);
                        
                        // Create Closed Trade Record
                        let entry_val = match_qty * front.price;
                        let exit_val = match_qty * exec.price;
                        
                        let pnl = if front.side == "Buy" {
                            exit_val - entry_val
                        } else {
                            entry_val - exit_val
                        };
                        
                        // Fee attribution: Entry + Exit
                        let entry_fee = front.fee_per_unit * match_qty;
                        let closing_fee = (exec.fee / exec.qty) * match_qty;
                        let total_trade_fee = entry_fee + closing_fee;
                        
                        self.trades.push(TradeRecord {
                            entry_ts: front.ts,
                            exit_ts: exec.ts,
                            side: front.side.clone(),
                            entry_price: front.price,
                            exit_price: exec.price,
                            qty: match_qty,
                            pnl,
                            fees: total_trade_fee,
                            exit_reason: "Match".to_string(),
                            entry_slippage_bps: front.slippage_bps,
                            exit_slippage_bps: exec.slippage_bps,
                            entry_liquidity_flag: front.liquidity_flag.clone(),
                            exit_liquidity_flag: exec.liquidity_flag.clone(),
                        });
                        
                        qty_remaining -= match_qty;
                        
                        // Update Lot
                        if (front.qty - match_qty).abs() < 1e-9 {
                            lots.pop_front();
                        } else if let Some(f) = lots.front_mut() {
                            f.qty -= match_qty;
                        }
                    }
                }
            }
        }
    }
}
