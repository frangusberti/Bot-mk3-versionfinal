use super::structs::*;
use std::collections::HashMap;

pub struct PortfolioManager {
    pub state: PortfolioState,
}

impl PortfolioManager {
    pub fn new(capital: f64) -> Self {
        Self {
            state: PortfolioState {
                cash_usdt: capital,
                equity_usdt: capital,
                margin_used: 0.0,
                available_margin: capital,
                positions: HashMap::new(),
                active_orders: HashMap::new(),
                stats: PortfolioStats {
                     value_at_risk: 0.0,
                     max_drawdown_daily: 0.0,
                     total_trades: 0,
                     win_rate: 0.0,
                },
                cumulative_fees: HashMap::new(),
                cumulative_funding: HashMap::new(),
                cumulative_pnl: HashMap::new(),
                trading_fees_entry: 0.0,
                trading_fees_exit: 0.0,
                funding_pnl: 0.0,
                slippage_cost: 0.0,
                leverage_map: HashMap::new(),
            }
        }
    }

    pub fn update_pnl(&mut self, symbol: &str, current_price: f64) {
        if let Some(pos) = self.state.positions.get_mut(symbol) {
             let diff = current_price - pos.entry_vwap;
             let mut pnl = match pos.side {
                 Side::Buy => diff * pos.qty,
                 Side::Sell => -diff * pos.qty,
             };
             if !pnl.is_finite() { pnl = 0.0; }
             pos.unrealized_pnl = pnl;
        }
    }
    
    pub fn recalibrate_margin(&mut self) {
        let mut total_margin = 0.0;
        for pos in self.state.positions.values() {
            total_margin += pos.margin_used;
        }
        self.state.margin_used = total_margin;
        self.state.available_margin = (self.state.equity_usdt - total_margin).max(0.0);
    }

     pub fn recalc_equity(&mut self) {
          let mut unrealized = 0.0;
          for pos in self.state.positions.values() {
              if pos.unrealized_pnl.is_finite() {
                  unrealized += pos.unrealized_pnl;
              }
          }
          let equity = self.state.cash_usdt + unrealized;
          if equity.is_finite() {
              self.state.equity_usdt = equity;
          } else {
              // Fallback to cash if unrealized is corrupted
              self.state.equity_usdt = self.state.cash_usdt;
          }
          self.recalibrate_margin();
     }

     /// Apply funding rate to a position.
     /// rate is the absolute decimal rate (e.g. 0.0001 for 0.01%).
     /// Longs pay when rate > 0, shorts receive.
     pub fn apply_funding(&mut self, symbol: &str, rate: f64) {
         if let Some(pos) = self.state.positions.get_mut(symbol) {
             let funding_charge = pos.qty * pos.entry_vwap * rate;
             let actual_pnl = match pos.side {
                 Side::Buy => -funding_charge,
                 Side::Sell => funding_charge,
             };
             
             if actual_pnl.is_finite() {
                 self.state.cash_usdt += actual_pnl;
                 pos.realized_funding += actual_pnl;
                 self.state.funding_pnl += actual_pnl;
                 
                 let cum_funding = self.state.cumulative_funding.entry(symbol.to_string()).or_insert(0.0);
                 *cum_funding += actual_pnl;
                 
                 self.recalc_equity();
             }
         }
     }
    
    // ... handling fills ...
    pub fn on_fill(&mut self, symbol: &str, side: Side, qty: f64, price: f64, fee: f64, ts: i64) {
         if !qty.is_finite() || !price.is_finite() || qty <= 0.0 || price <= 0.0 { return; }
         
         // Bankruptcy Check: Cannot OPEN or ADD to positions if equity <= 0
         if self.state.equity_usdt <= 0.0 {
             let is_reducing = if let Some(pos) = self.state.positions.get(symbol) {
                 pos.side != side
             } else {
                 false
             };
             if !is_reducing {
                 log::warn!("BANKRUPT: Rejecting {} fill for {} due to zero equity", symbol, qty);
                 return;
             }
         }

         if fee.is_finite() {
             self.state.cash_usdt -= fee;
             let current_fee = self.state.cumulative_fees.entry(symbol.to_string()).or_insert(0.0);
             *current_fee += fee;
         }
         
         // Borrow checker friendly update
         // We might insert a new position, so we can't just get_mut.
         let has_pos = self.state.positions.contains_key(symbol);
         
         if !has_pos {
             self.state.positions.insert(symbol.to_string(), PositionState {
                 symbol: symbol.to_string(),
                 side,
                 qty,
                 entry_vwap: price,
                 realized_pnl: 0.0,
                 unrealized_pnl: 0.0,
                 realized_fees: fee,
                 realized_funding: 0.0,
                 open_ts: ts,
                 last_update_ts: ts,
                 liquidation_price: 0.0,
                 margin_used: 0.0,
                 notional_value: qty * price,
                 leverage: 1.0,
             });
             self.state.trading_fees_entry += fee;
             self.update_risk_metrics(symbol); 
         } else {
             let pos = self.state.positions.get_mut(symbol).unwrap();
             pos.last_update_ts = ts;
             if pos.side == side {
                 // Add to position
                 let cost_old = pos.qty * pos.entry_vwap;
                 let cost_new = qty * price;
                 let new_qty = pos.qty + qty;
                 let vwap = (cost_old + cost_new) / new_qty;
                 
                 pos.qty = new_qty;
                 if vwap.is_finite() && vwap > 0.0 {
                     pos.entry_vwap = vwap;
                 }
                 
                 // Segment Fee: same side addition is "Entry"
                 self.state.trading_fees_entry += fee;
                 pos.realized_fees += fee;
             } else {
                 // Close/Reduce
                 let close_qty = f64::min(pos.qty, qty);
                 let diff = price - pos.entry_vwap; 
                 let mut pnl = match pos.side {
                     Side::Buy => diff * close_qty,
                     Side::Sell => -diff * close_qty,
                 };
                 if !pnl.is_finite() { pnl = 0.0; }
                 
                  self.state.cash_usdt += pnl;
                  pos.realized_pnl += pnl;
                  let cum_pnl = self.state.cumulative_pnl.entry(symbol.to_string()).or_insert(0.0);
                  *cum_pnl += pnl;
                  pos.qty -= close_qty;

                  if pnl.abs() > 1000.0 {
                      log::info!("HIGH_PNL: sym={} side={:?} qty={} entry={:.2} exit={:.2} pnl={:.2}", 
                          symbol, pos.side, close_qty, pos.entry_vwap, price, pnl);
                  }

                 // If flip
                 let fee_rem = if qty > close_qty {
                     fee * ( (qty - close_qty) / qty )
                 } else {
                     0.0
                 };
                 let fee_close = fee - fee_rem;

                 // Segment Fee: opposite side is "Exit"
                 self.state.trading_fees_exit += fee_close;
                 pos.realized_fees += fee_close;
                 
                 if qty > close_qty {
                     log::warn!("BLIND_REVERSAL_CLIPPED: sym={} side={:?} qty={} close_qty={}", 
                         symbol, side, qty, close_qty);
                     pos.qty = 0.0;
                 }
              }
              self.update_risk_metrics(symbol); 
          }
         
         // Cleanup: remove positions with zero qty
         self.state.positions.retain(|_, p| p.qty > 1e-9);
    }

    pub fn update_leverage(&mut self, symbol: &str, leverage: f64) {
        self.state.leverage_map.insert(symbol.to_string(), leverage);
        self.update_risk_metrics(symbol);
    }

    pub fn update_risk_metrics(&mut self, symbol: &str) {
        let leverage = *self.state.leverage_map.get(symbol).unwrap_or(&1.0);
        
        if let Some(pos) = self.state.positions.get_mut(symbol) {
             pos.leverage = leverage;
            let mm = 0.005; // 0.5% Maint. Margin
            
            // 1. Notional & Margin
            pos.notional_value = pos.qty * pos.entry_vwap;
            pos.margin_used = if leverage > 0.0 { pos.notional_value / leverage } else { pos.notional_value };

            // 2. Liquidation Price
            if pos.qty.abs() > 0.0 && leverage > 0.0 {
                pos.liquidation_price = match pos.side {
                    Side::Buy => pos.entry_vwap * (1.0 - 1.0/leverage + mm),
                    Side::Sell => pos.entry_vwap * (1.0 + 1.0/leverage - mm),
                };
            } else {
                pos.liquidation_price = 0.0;
            }
        }
        self.recalibrate_margin();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_blind_reversal_clipped() {
        let mut portfolio = PortfolioManager::new(1000.0);
        let symbol = "BTCUSDT".to_string();
        
        // 1. Open a Long position (1.0 BTC)
        portfolio.state.positions.insert(symbol.clone(), PositionState {
            symbol: symbol.clone(),
            side: Side::Buy,
            qty: 1.0,
            entry_vwap: 50000.0,
            open_ts: 1000,
            last_update_ts: 1000,
            realized_pnl: 0.0,
            realized_fees: 0.0,
            realized_funding: 0.0,
            unrealized_pnl: 0.0,
            liquidation_price: 0.0,
            margin_used: 0.0,
            notional_value: 50000.0,
            leverage: 1.0,
        });

        // 2. Simulate a Sell fill that exceeds the Long position (1.5 BTC)
        // Expected behavior: The position should be CLOSED (qty=0), NOT flipped to Short.
        portfolio.on_fill(&symbol, Side::Sell, 1.5, 51000.0, 1.0, 2000);

        let pos_opt = portfolio.state.positions.get(&symbol);
        assert!(pos_opt.is_none(), "Position should be removed after being clipped to 0 (flat).");
        
        println!("SUCCESS: Blind reversal clipped for {}", symbol);
    }
}
