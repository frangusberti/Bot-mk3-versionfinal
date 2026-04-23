use super::structs::*;
use super::portfolio::PortfolioManager;
use crate::normalization::schema::NormalizedMarketEvent;
use crate::reporting::backtest::ExecutionRecord;

pub struct ExecutionEngine {
    pub config: ExecutionConfig,
    pub portfolio: PortfolioManager, 
    
    // Internal state for metrics/risk
    pub daily_high_equity: f64,
    pub day_start_ts: i64,
    
    // Order Id counter
    order_counter: u64,
    
    // Clock
    pub current_time: i64,
    
    // L2 Book levels for TopN slippage model
    // (price, qty) sorted best -> worst
    pub book_bids: Vec<(f64, f64)>,
    pub book_asks: Vec<(f64, f64)>,
    
    // Last batch of fill events (for external consumption)
    pub last_fill_events: Vec<FillEvent>,
    pub stale_expiries_in_step: u32,
    pub last_price: f64,
    pub is_in_disaster_stop: bool,
}

impl ExecutionEngine {
    pub fn new(config: ExecutionConfig) -> Self {
        let capital = config.base_capital_usdt;
        let portfolio = PortfolioManager::new(capital);
        
        Self {
            config,
            portfolio,
            daily_high_equity: capital,
            day_start_ts: 0,
            order_counter: 0,
            current_time: 0,
            book_bids: Vec::new(),
            book_asks: Vec::new(),
            last_fill_events: Vec::new(),
            stale_expiries_in_step: 0,
            last_price: 0.0,
            is_in_disaster_stop: false,
        }
    }

    pub fn from_state(config: ExecutionConfig, state: PortfolioState) -> Self {
        let equity = state.equity_usdt;
        Self {
            config,
            portfolio: PortfolioManager { state },
            daily_high_equity: equity,
            day_start_ts: 0,
            order_counter: 0,
            current_time: 0,
            book_bids: Vec::new(),
            book_asks: Vec::new(),
            last_fill_events: Vec::new(),
            stale_expiries_in_step: 0,
            last_price: 0.0,
            is_in_disaster_stop: false,
        }
    }
    
    pub fn update(&mut self, event: &NormalizedMarketEvent) -> Vec<ExecutionRecord> {
        let mut fills = Vec::new();
        let now = event.time_canonical;
        self.current_time = now;

        // Numeric Firewall - check ALL fields that affect money
        if let Some(p) = event.price {
            if !p.is_finite() || p <= 0.0 { return vec![]; }
        }
        if let Some(p) = event.best_bid {
            if !p.is_finite() || p <= 0.0 { return vec![]; }
        }
        if let Some(p) = event.best_ask {
            if !p.is_finite() || p <= 0.0 { return vec![]; }
        }
        if let Some(p) = event.mark_price {
            if !p.is_finite() || p <= 0.0 { return vec![]; }
        }

        if let Some(q) = event.qty {
             if !q.is_finite() || q < 0.0 { return vec![]; }
        }
        
        // 0. Init Day
        if self.day_start_ts == 0 { self.day_start_ts = now; }
        
        // 1. Update Portfolio Valuation (Mark to Market)
        let price = Self::get_price_for_pnl(event);
        if let Some(p) = price {
            self.last_price = p;
            self.portfolio.update_pnl(&event.symbol, p);
        }
        
        self.portfolio.recalc_equity();
        
        // 2. Risk Checks (Daily DD)
        let equity = self.portfolio.state.equity_usdt;
        if equity > self.daily_high_equity {
            self.daily_high_equity = equity;
        }
        
        let dd_pct = if self.daily_high_equity > 0.0 {
            (self.daily_high_equity - equity) / self.daily_high_equity * 100.0
        } else {
            0.0
        };

        if dd_pct > self.config.disaster_stop_dd_daily_pct {
            if !self.is_in_disaster_stop {
                self.trigger_disaster_stop(now);
                self.is_in_disaster_stop = true;
            }
        }

        // 3. Liquidation Check
        let mut liquidated_symbols = Vec::new();
        if let Some(pos) = self.portfolio.state.positions.get(&event.symbol) {
            if pos.liquidation_price > 0.0 {
                let is_liquidated = match pos.side {
                    Side::Buy => price.map(|p| p <= pos.liquidation_price).unwrap_or(false),
                    Side::Sell => price.map(|p| p >= pos.liquidation_price).unwrap_or(false),
                };
                if is_liquidated {
                    liquidated_symbols.push(event.symbol.clone());
                }
            }
        }
        for sym in liquidated_symbols {
            log::warn!("LIQUIDATION: {} at price {:?}", sym, price);
            self.trigger_liquidation(&sym, now);
        }

        // 4. Match Orders
        // Collect orders to process to avoid borrowing issues while iterating
        let order_ids: Vec<String> = self.portfolio.state.active_orders.iter()
            .filter(|(_, o)| {
                o.symbol == event.symbol && 
                (o.status == OrderStatus::New || 
                 o.status == OrderStatus::Open || 
                 o.status == OrderStatus::Penned || 
                 o.status == OrderStatus::PartiallyFilled ||
                 o.status == OrderStatus::Cancelling)
            })
            .map(|(id, _)| id.clone())
            .collect();

        for order_id in order_ids {
            if let Some(fill) = self.process_order_matching(&order_id, event) {
                fills.push(fill);
            }
        }
        
        // 3b. Handle stale orders and finalizations
        let now = event.time_canonical;
        for o in self.portfolio.state.active_orders.values_mut() {
            if o.symbol != event.symbol { continue; }

            // Stale Order Logic (30s timeout for Maker orders)
            if o.order_type == OrderType::Limit && 
               (o.status == OrderStatus::Open || o.status == OrderStatus::PartiallyFilled) &&
               now > o.created_ts + 60_000 
            {
                o.status = OrderStatus::Expired;
                self.stale_expiries_in_step += 1;
            }

            // Cancellation Finalization
            if o.status == OrderStatus::Cancelling && now >= o.pending_cancel_ts {
                o.status = OrderStatus::Cancelled;
            }
        }

        // 3c. Cleanup filled/expired/canceled orders
        self.portfolio.state.active_orders.retain(|_, o| {
            o.status == OrderStatus::New || 
            o.status == OrderStatus::Open || 
            o.status == OrderStatus::Penned || 
            o.status == OrderStatus::PartiallyFilled ||
            o.status == OrderStatus::Cancelling
        });
        
        // 4. Funding (Simplified)
        if let Some(funding) = event.funding_rate {
             if let Some(pos) = self.portfolio.state.positions.get_mut(&event.symbol) {
                 let pos_value = pos.qty * pos.entry_vwap;
                 let payment = pos_value * funding;
                 
                 let cash_delta = match pos.side {
                     Side::Buy => -payment,
                     Side::Sell => payment, 
                 };
                 
                  if cash_delta.is_finite() {
                      self.portfolio.state.cash_usdt += cash_delta;
                      self.portfolio.state.funding_pnl += cash_delta;
                      pos.realized_pnl += cash_delta; 
                      pos.realized_funding += cash_delta;
                      
                      let cum_pnl = self.portfolio.state.cumulative_pnl.entry(event.symbol.clone()).or_insert(0.0);
                      *cum_pnl += cash_delta;
                      
                      let cum_funding = self.portfolio.state.cumulative_funding.entry(event.symbol.clone()).or_insert(0.0);
                      *cum_funding += cash_delta;
                  }
             }
        }
        
        // 5. Final equity recalc (after funding adjustments)
        self.portfolio.recalc_equity();
        
        fills
    }
    
    pub fn get_price_for_pnl(event: &NormalizedMarketEvent) -> Option<f64> {
        if let Some(mp) = event.mark_price { return Some(mp); }
        if let (Some(b), Some(a)) = (event.best_bid, event.best_ask) { return Some((b+a)/2.0); }
        event.price
    }
    
    fn process_order_matching(&mut self, order_id: &str, event: &NormalizedMarketEvent) -> Option<ExecutionRecord> {
        let mut fill_info: Option<(String, Side, f64, f64, f64, i64, OrderType, f64, String)> = None;
        let mut is_toxic = false;

        if let Some(order) = self.portfolio.state.active_orders.get_mut(order_id) {
            
            // Latency Check
            if event.time_canonical < order.active_from_ts {
                if order.status == OrderStatus::New { order.status = OrderStatus::Penned; }
                return None;
            }
            if order.status == OrderStatus::Penned || order.status == OrderStatus::New { 
                order.status = OrderStatus::Open; 
            }
            
            // Timeout Check
            if let Some(exp) = order.expires_ts {
                if event.time_canonical > exp {
                    order.status = OrderStatus::Expired; 
                    return None;
                }
            }

            // Queue Update (Conservative Maker)
            let mut qty_filled_from_queue = 0.0;
            if order.order_type == OrderType::Limit {
                if let SlippageModel::ConservativeMaker(ref queue_config) = self.config.slippage_model {
                    if order.queue_state.is_none() {
                        // Initialize queue on first evaluation after pennant
                        if order.status == OrderStatus::Open {
                            // Simple heuristic: behind 50% of top level or arbitrary size if no L2
                            // STRICT: No depth available, cannot initialize queue position.
                            // We will wait for the next snapshot/update if this is a live run,
                            // or Fail if this is a backtest without L2.
                            let ahead = 0.0;
                            if order.side == Side::Buy {
                                if let Some(best) = self.book_bids.first() {
                                    if (best.0 - order.price).abs() < 1e-6 {
                                        let mut ahead = best.1 * (if queue_config.assume_half_queue { 0.5 } else { 1.0 });
                                        
                                        // V3: Apply SemiOptimistic scaling
                                        if self.config.maker_fill_model == MakerFillModel::SemiOptimistic {
                                            ahead *= 0.1;
                                        }
                                        
                                        order.queue_state = Some(QueueState {
                                            position_ahead: ahead,
                                            original_price: order.price,
                                        });
                                    }
                                }
                            } else {
                                if let Some(best) = self.book_asks.first() {
                                    if (best.0 - order.price).abs() < 1e-6 {
                                        let mut ahead = best.1 * (if queue_config.assume_half_queue { 0.5 } else { 1.0 });
                                        
                                        // V3: Apply SemiOptimistic scaling
                                        if self.config.maker_fill_model == MakerFillModel::SemiOptimistic {
                                            ahead *= 0.1;
                                        }
                                        
                                        order.queue_state = Some(QueueState {
                                            position_ahead: ahead,
                                            original_price: order.price,
                                        });
                                    }
                                }
                            }
                        }
                    }

                    if let Some(ref mut queue) = order.queue_state {
                        // Deplete queue based on tape volume at our price
                        if let Some(trade_px) = event.price {
                            if (trade_px - queue.original_price).abs() < 1e-6 {
                                let vol = event.qty.unwrap_or(0.0);
                                if queue.position_ahead > 0.0 {
                                    queue.position_ahead -= vol;
                                }
                                
                                // If queue depleted, capture remaining tape volume for ourselves
                                if queue.position_ahead <= 0.0 {
                                    let available = -queue.position_ahead; // what's left after clearing the queue
                                    if available > 0.0 {
                                        qty_filled_from_queue = available.min(order.remaining);
                                        queue.position_ahead = 0.0; // reset the overrun
                                    }
                                }
                            }
                        }

                        // Adverse selection / Price crossed (we definitely got filled, but might be toxic)
                        let price_crossed = match order.side {
                            Side::Buy => event.best_ask.map_or(
                                event.price.map_or(false, |p| p < queue.original_price), 
                                |ask| ask <= queue.original_price
                            ),
                            Side::Sell => event.best_bid.map_or(
                                event.price.map_or(false, |p| p > queue.original_price), 
                                |bid| bid >= queue.original_price
                            ),
                        };

                        if price_crossed {
                            qty_filled_from_queue = order.remaining;
                            is_toxic = true;
                        }
                    }
                }
            }

            // Matching Logic
            let match_found = match order.order_type {
                OrderType::Limit => {
                     // Adjust based on Fill Model
                     match self.config.maker_fill_model {
                         MakerFillModel::Optimistic => {
                             // "Fill on BBO touch" — correct passive maker semantic.
                             // A limit buy fills when best_ask descends to our price.
                             // A limit sell fills when best_bid rises to our price.
                             // Falls back to aggTrade price if BBO is unavailable.
                             match order.side {
                                 Side::Buy => {
                                     event.best_ask.map_or(
                                         event.price.map_or(false, |p| p <= order.price),
                                         |ask| ask <= order.price,
                                     )
                                 },
                                 Side::Sell => {
                                     event.best_bid.map_or(
                                         event.price.map_or(false, |p| p >= order.price),
                                         |bid| bid >= order.price,
                                     )
                                 },
                             }
                         },
                         MakerFillModel::SemiOptimistic | MakerFillModel::Conservative => {
                             // Use queue-based result (SemiOptimistic has a smaller queue ahead)
                             qty_filled_from_queue > 0.0
                         }
                     }
                 },
                OrderType::Market => true, 
                _ => false,
            };
            
            // --- Fill Debug Log (controlled by RUST_LOG=debug or FILL_DEBUG=1) ---
            {
                let matched_by = if match_found {
                    match self.config.maker_fill_model {
                        MakerFillModel::Optimistic => {
                            if order.order_type == OrderType::Limit {
                                match order.side {
                                    Side::Buy  => if event.best_ask.map_or(false, |a| a <= order.price) { "BBO_TOUCH" } else { "TRADE_PRICE_FALLBACK" },
                                    Side::Sell => if event.best_bid.map_or(false, |b| b >= order.price) { "BBO_TOUCH" } else { "TRADE_PRICE_FALLBACK" },
                                }
                            } else { "MARKET_IMMEDIATE" }
                        },
                        _ => if qty_filled_from_queue > 0.0 { "QUEUE_DEPLETED" } else { "PRICE_CROSSED" },
                    }
                } else { "NONE" };
                let bid_depth: f64 = self.book_bids.iter().map(|(_, q)| q).sum();
                let ask_depth: f64 = self.book_asks.iter().map(|(_, q)| q).sum();
                log::debug!(
                    "[FILL_CHECK] order={} side={:?} model={:?} order_px={:.2} \
                     best_bid={:?} best_ask={:?} trade_px={:?} \
                     bid_depth_total={:.4} ask_depth_total={:.4} \
                     queue_fill_qty={:.6} matched={} matched_by={}",
                    order_id, order.side, self.config.maker_fill_model,
                    order.price,
                    event.best_bid, event.best_ask, event.price,
                    bid_depth, ask_depth,
                    qty_filled_from_queue,
                    match_found, matched_by
                );
            }

            if match_found {
                 let fill_price_opt = if order.order_type == OrderType::Market {
                     if order.side == Side::Buy { event.best_ask.or(event.price) }
                     else { event.best_bid.or(event.price) }
                 } else {
                     Some(order.price)
                 };

                 let mut fill_price = match fill_price_opt {
                     Some(p) if p.is_finite() && p > 0.0 => p,
                     _ => return None, // No valid price to match against
                 };
                 
                 // Determine if order was providing liquidity (Maker) or taking it (Taker)
                 let is_taker = order.was_marketable_on_arrival || 
                                order.resting_since_ts.map_or(true, |ts| event.time_canonical < ts);
                 
                 let mut final_liquidity_flag = if is_taker { LiquidityFlag::Taker } else { LiquidityFlag::Maker };
                 
                 if !order.accepted_as_passive && !order.was_marketable_on_arrival {
                     final_liquidity_flag = LiquidityFlag::Unknown;
                 }
                 
                 let fee_rate = match final_liquidity_flag { LiquidityFlag::Maker => self.config.maker_fee_bps, _ => self.config.taker_fee_bps };
                 let liq_str = match final_liquidity_flag { LiquidityFlag::Maker => "Maker", LiquidityFlag::Taker => "Taker", LiquidityFlag::Unknown => "Unknown" }.to_string();
                 
                  // Apply slippage
                  match &self.config.slippage_model {
                      SlippageModel::TopN(_depth) => {
                          let levels = if order.side == Side::Buy { &self.book_asks } else { &self.book_bids };
                          if !levels.is_empty() {
                              let (vwap, filled_qty) = Self::consume_book_levels_static(levels, order.remaining);
                              if filled_qty > 0.0 {
                                  fill_price = vwap;
                                  let mid = Self::get_price_for_pnl(event).unwrap_or(fill_price);
                                  let slip_bps = if mid > 0.0 { ((fill_price - mid) / mid * 10000.0).abs() } else { 0.0 };
                                  let fee = (fill_price * filled_qty) * (fee_rate / 10000.0);

                                  if filled_qty < order.remaining {
                                      // Partial fill
                                      order.remaining -= filled_qty;
                                      order.status = OrderStatus::PartiallyFilled;
                                      
                                      self.last_fill_events.push(FillEvent {
                                          order_id: order.id.clone(),
                                          symbol: order.symbol.clone(),
                                          side: order.side,
                                          qty_filled: filled_qty,
                                          price: fill_price,
                                          fee_paid: fee,
                                          liquidity_flag: final_liquidity_flag,
                                          slippage_bps: slip_bps,
                                          event_time: event.time_canonical,
                                          cost_source: CostSource::Simulated,
                                          is_toxic: false,
                                      });
                                      fill_info = Some((order.symbol.clone(), order.side, filled_qty, fill_price, fee, event.time_canonical, order.order_type, slip_bps, liq_str.clone()));
                                  } else {
                                      // Full fill from L2
                                      order.remaining = 0.0;
                                      order.status = OrderStatus::Filled;
                                      
                                      self.last_fill_events.push(FillEvent {
                                          order_id: order.id.clone(),
                                          symbol: order.symbol.clone(),
                                          side: order.side,
                                          qty_filled: filled_qty,
                                          price: fill_price,
                                          fee_paid: fee,
                                          liquidity_flag: final_liquidity_flag,
                                          slippage_bps: slip_bps,
                                          event_time: event.time_canonical,
                                          cost_source: CostSource::Simulated,
                                          is_toxic: false,
                                      });
                                      fill_info = Some((order.symbol.clone(), order.side, filled_qty, fill_price, fee, event.time_canonical, order.order_type, slip_bps, liq_str.clone()));
                                  }
                              }
                          } else {
                              // Fallback to flat slippage
                              let slip = fill_price * (self.config.slip_bps / 10000.0);
                              if order.side == Side::Buy { fill_price += slip; } else { fill_price -= slip; }
                              
                              let fee = (fill_price * order.qty) * (fee_rate / 10000.0);
                              let mid = Self::get_price_for_pnl(event).unwrap_or(fill_price);
                              let slip_bps = if mid > 0.0 { ((fill_price - mid) / mid * 10000.0).abs() } else { 0.0 };
                              
                              order.status = OrderStatus::Filled;
                              order.remaining = 0.0;
                              
                              self.last_fill_events.push(FillEvent {
                                  order_id: order.id.clone(),
                                  symbol: order.symbol.clone(),
                                  side: order.side,
                                  qty_filled: order.qty,
                                  price: fill_price,
                                  fee_paid: fee,
                                  liquidity_flag: final_liquidity_flag,
                                  slippage_bps: slip_bps,
                                  event_time: event.time_canonical,
                                  cost_source: CostSource::Simulated,
                                  is_toxic: false,
                              });
                              fill_info = Some((order.symbol.clone(), order.side, order.qty, fill_price, fee, event.time_canonical, order.order_type, slip_bps, liq_str.clone()));
                          }
                      },
                      SlippageModel::Flat(bps) => {
                          let slip = fill_price * (bps / 10000.0);
                          if order.side == Side::Buy { fill_price += slip; } else { fill_price -= slip; }
                          
                          let fee = (fill_price * order.qty) * (fee_rate / 10000.0);
                          let mid = Self::get_price_for_pnl(event).unwrap_or(fill_price);
                          let slip_bps = if mid > 0.0 { ((fill_price - mid) / mid * 10000.0).abs() } else { 0.0 };
                          
                          order.status = OrderStatus::Filled;
                          order.remaining = 0.0;
                          
                          self.last_fill_events.push(FillEvent {
                              order_id: order.id.clone(),
                              symbol: order.symbol.clone(),
                              side: order.side,
                              qty_filled: order.qty,
                              price: fill_price,
                              fee_paid: fee,
                              liquidity_flag: final_liquidity_flag,
                              slippage_bps: slip_bps,
                              event_time: event.time_canonical,
                              cost_source: CostSource::Simulated,
                              is_toxic: false,
                          });
                          fill_info = Some((order.symbol.clone(), order.side, order.qty, fill_price, fee, event.time_canonical, order.order_type, slip_bps, liq_str.clone()));
                      },
                      SlippageModel::ConservativeMaker(_) => {
                          // Taker orders bypass queue logic and assume Flat(1bps) for simplicity
                          // or TopN if combined (we're keeping it simple for now)
                          let filled = if is_taker { order.remaining } else { qty_filled_from_queue };
                          
                          if filled > 0.0 {
                              let slip = if is_taker { fill_price * (self.config.slip_bps / 10000.0) } else { 0.0 };
                              if is_taker { 
                                  if order.side == Side::Buy { fill_price += slip; } else { fill_price -= slip; }
                              }
                              
                              let fee = (fill_price * filled) * (fee_rate / 10000.0);
                              let mid = Self::get_price_for_pnl(event).unwrap_or(fill_price);
                              let slip_bps = if mid > 0.0 { ((fill_price - mid) / mid * 10000.0).abs() } else { 0.0 };
                              
                              if filled < order.remaining {
                                  order.remaining -= filled;
                                  order.status = OrderStatus::PartiallyFilled;
                              } else {
                                  order.remaining = 0.0;
                                  order.status = OrderStatus::Filled;
                                  order.queue_state = None; // Reset if fully filled
                              }
                              
                              self.last_fill_events.push(FillEvent {
                                  order_id: order.id.clone(),
                                  symbol: order.symbol.clone(),
                                  side: order.side,
                                  qty_filled: filled,
                                  price: fill_price,
                                  fee_paid: fee,
                                  liquidity_flag: final_liquidity_flag,
                                  slippage_bps: slip_bps,
                                  event_time: event.time_canonical,
                                  cost_source: CostSource::Simulated,
                                  is_toxic,
                              });
                              fill_info = Some((order.symbol.clone(), order.side, filled, fill_price, fee, event.time_canonical, order.order_type, slip_bps, liq_str.clone()));
                          }
                      }
                  }
            }
        }
        
        // Apply fill outside of borrow scope
        if let Some((symbol, side, qty, price, fee, ts, o_type, slippage_bps, liquidity_flag)) = fill_info {
            log::debug!(
                "[FILL_MATERIALIZED] order={} symbol={} side={:?} fill_qty={:.6} fill_price={:.2} fee={:.4} slip_bps={:.2} liq={} type={:?}",
                order_id, symbol,
                side, qty, price, fee, slippage_bps,
                liquidity_flag,
                o_type,
            );
            self.portfolio.on_fill(&symbol, side, qty, price, fee, ts);
            self.portfolio.state.stats.total_trades += 1;
            
            // Construct ExecutionRecord
            return Some(ExecutionRecord {
                 symbol: symbol.clone(),
                 side: match side { Side::Buy => "Buy".to_string(), Side::Sell => "Sell".to_string() },
                 qty,
                 price,
                 fee,
                 ts,
                 order_type: match o_type { OrderType::Limit => "Limit".to_string(), OrderType::Market => "Market".to_string(), _ => "Other".to_string() }, 
                 slippage_bps,
                 liquidity_flag,
            });
        }
        None
    }
    
    pub fn submit_order(&mut self, symbol: &str, side: Side, px: f64, qty: f64, order_type: OrderType) -> String {
        // --- Margin & Leverage Gate ---
        let current_equity = self.portfolio.state.equity_usdt;
        let price = if px > 0.0 { px } else { self.current_time_price() };
        let order_notional = qty * price;
        
        // Calculate Total Portfolio Notional AFTER this order
        let current_notional: f64 = self
            .portfolio
            .state
            .positions
            .values()
            .map(|pos| pos.notional_value)
            .sum();
        let total_notional_after = current_notional + order_notional;
        let total_leverage = if current_equity > 0.0 { total_notional_after / current_equity } else { 999.0 };

        // Hard Cap: 20x Total Portfolio Leverage or Bankrupt
        if current_equity <= 0.0 || total_leverage > 20.0 {
            log::warn!("MARGIN_REJECT: lev={:.2}, equity={:.2}, order_not={:.2}", total_leverage, current_equity, order_notional);
            return "REJECTED_MARGIN".to_string();
        }

        if price <= 0.0 {
            log::warn!("PRICE_REJECT: Cannot submit order with zero price fallback.");
            return "REJECTED_PRICE".to_string();
        }

        self.order_counter += 1;
        let id = format!("ord_{}", self.order_counter);
        
        let mut was_marketable = false;
        let mut has_bbo = false;
        if order_type == OrderType::Market {
            was_marketable = true;
            has_bbo = true;
        } else {
            match side {
                Side::Buy => {
                    if let Some(ask) = self.book_asks.first() {
                        has_bbo = true;
                        was_marketable = px >= ask.0;
                    }
                },
                Side::Sell => {
                    if let Some(bid) = self.book_bids.first() {
                        has_bbo = true;
                        was_marketable = px <= bid.0;
                    }
                }
            }
        }
        
        // If we don't have BBO (e.g. trade-only dataset), we assume it's passive if Limit
        let accepted_as_passive = (!was_marketable && order_type == OrderType::Limit) || (!has_bbo && order_type == OrderType::Limit);
        
        let now = self.current_time;
        
        let order = OrderState {
            id: id.clone(),
            symbol: symbol.to_string(),
            side,
            order_type,
            price: px,
            qty,
            remaining: qty,
            status: OrderStatus::New,
            created_ts: now, 
            active_from_ts: now + self.config.latency_ms,
            pending_cancel_ts: 0,
            expires_ts: None,
            queue_state: None,
            was_marketable_on_arrival: was_marketable,
            accepted_as_passive,
            resting_since_ts: if accepted_as_passive { Some(now + self.config.latency_ms) } else { None },
        };
        
        self.portfolio.state.active_orders.insert(id.clone(), order);
        id
    }
    
    pub fn cancel_order(&mut self, id: &str) -> bool {
        if let Some(order) = self.portfolio.state.active_orders.get_mut(id) {
            if order.status == OrderStatus::Cancelled || order.status == OrderStatus::Filled || order.status == OrderStatus::Expired || order.status == OrderStatus::Cancelling {
                return false;
            }
            order.status = OrderStatus::Cancelling;
            order.pending_cancel_ts = self.current_time + self.config.latency_ms;
            return true;
        }
        false
    }

    fn trigger_disaster_stop(&mut self, now: i64) {
        log::error!("DISASTER_STOP: Daily drawdown exceeded ({:.2}%). Aggressive exit triggered.", self.config.disaster_stop_dd_daily_pct);
        let symbols: Vec<String> = self.portfolio.state.positions.keys().cloned().collect();
        for sym in symbols {
            self.trigger_liquidation(&sym, now);
        }
    }

    fn trigger_liquidation(&mut self, symbol: &str, now: i64) {
        if let Some(pos) = self.portfolio.state.positions.get(symbol) {
             let qty = pos.qty;
             let side = pos.side;
             let price = self.current_time_price(); 
             
             // Market exit
             self.portfolio.on_fill(symbol, side.opposite(), qty, price, 0.0, now);
             log::info!("LIQUIDATION_EXEC: {} {} @ {}", symbol, qty, price);
        }
        self.clear_all_orders();
    }

    fn current_time_price(&self) -> f64 {
        if let Some(bid) = self.book_bids.first() { return bid.0; }
        if let Some(ask) = self.book_asks.first() { return ask.0; }
        self.last_price
    }
    
    pub fn force_set_last_price(&mut self, price: f64) {
        if price > 0.0 {
            self.last_price = price;
        }
    }
    
    /// Inject L2 book levels from external source (e.g., OrderBook::top_bids/top_asks).
    pub fn set_book_levels(&mut self, bids: Vec<(f64, f64)>, asks: Vec<(f64, f64)>) {
        self.book_bids = bids;
        self.book_asks = asks;
    }
    
    pub fn clear_all_orders(&mut self) -> u32 {
        let mut cancelled = 0;
        let ids: Vec<String> = self.portfolio.state.active_orders.iter()
            .map(|(id, _)| id.clone())
            .collect();
        
        for id in ids {
            if self.cancel_order(&id) {
                cancelled += 1;
            }
        }
        cancelled
    }

    pub fn clear_step_stats(&mut self) {
        self.last_fill_events.clear();
        self.stale_expiries_in_step = 0;
    }

    /// Walk L2 levels consuming liquidity, returns (vwap_price, qty_filled).
    /// Levels are sorted best -> worst (highest bid first, lowest ask first).
    fn consume_book_levels_static(levels: &[(f64, f64)], qty_needed: f64) -> (f64, f64) {
        let mut remaining = qty_needed;
        let mut cost = 0.0;
        let mut filled = 0.0;
        
        for &(price, level_qty) in levels {
            if remaining <= 0.0 { break; }
            let take = remaining.min(level_qty);
            cost += take * price;
            filled += take;
            remaining -= take;
        }
        
        if filled > 0.0 {
            (cost / filled, filled)
        } else {
            (0.0, 0.0)
        }
    }
}

use crate::execution::{ExecutionInterface, PositionInfo};
use async_trait::async_trait;
use std::sync::{Arc, Mutex};

pub struct SimExecutionAdapter {
    pub engine: Arc<Mutex<ExecutionEngine>>,
}

impl SimExecutionAdapter {
    pub fn new(engine: Arc<Mutex<ExecutionEngine>>) -> Self {
        Self { engine }
    }
}

#[async_trait]
impl ExecutionInterface for SimExecutionAdapter {
    async fn submit_order(&mut self, symbol: &str, side: &str, qty: f64, price: f64, type_: &str) -> Result<String, String> {
        let mut engine = self.engine.lock().map_err(|e| e.to_string())?;
        let s = match side { "Buy" => Side::Buy, "Sell" => Side::Sell, _ => return Err("Invalid side".into()) };
        let t = match type_ { "MARKET" => OrderType::Market, "LIMIT" => OrderType::Limit, _ => OrderType::Market };
        
        let mut new_margin_req = 0.0;
        if let Some(pos) = engine.portfolio.state.positions.get(symbol) {
            if pos.side != s {
                if qty > pos.qty {
                    let flip_qty = qty - pos.qty;
                    let lev = engine.portfolio.state.leverage_map.get(symbol).copied().unwrap_or(1.0);
                    let eff_lev = if lev > 0.0 { lev } else { 1.0 };
                    new_margin_req = (flip_qty * price) / eff_lev;
                }
            } else {
                let lev = engine.portfolio.state.leverage_map.get(symbol).copied().unwrap_or(1.0);
                let eff_lev = if lev > 0.0 { lev } else { 1.0 };
                new_margin_req = (qty * price) / eff_lev;
            }
        } else {
            let lev = engine.portfolio.state.leverage_map.get(symbol).copied().unwrap_or(1.0);
            let eff_lev = if lev > 0.0 { lev } else { 1.0 };
            new_margin_req = (qty * price) / eff_lev;
        }

        if new_margin_req > 0.0 {
             let mut pending_margin = 0.0;
             for o in engine.portfolio.state.active_orders.values() {
                  if o.status == OrderStatus::New || o.status == OrderStatus::Penned || o.status == OrderStatus::Open {
                       let o_eff_lev = {
                           let l = engine.portfolio.state.leverage_map.get(&o.symbol).copied().unwrap_or(1.0);
                           if l > 0.0 { l } else { 1.0 }
                       };
                       
                       if let Some(p) = engine.portfolio.state.positions.get(&o.symbol) {
                            if p.side != o.side {
                                if o.qty > p.qty {
                                     pending_margin += ((o.qty - p.qty) * o.price) / o_eff_lev;
                                }
                            } else {
                                pending_margin += (o.qty * o.price) / o_eff_lev;
                            }
                       } else {
                            pending_margin += (o.qty * o.price) / o_eff_lev;
                       }
                  }
             }
             
             let free_margin = engine.portfolio.state.equity_usdt - engine.portfolio.state.margin_used - pending_margin;
             if new_margin_req > free_margin {
                 return Err(format!("Insufficient margin. Req: {:.2}, Free: {:.2}", new_margin_req, free_margin));
             }
        }

        // Sim engine handles id generation
        Ok(engine.submit_order(symbol, s, price, qty, t))
    }

    async fn cancel_order(&mut self, _symbol: &str, order_id: &str) -> Result<(), String> {
        let mut engine = self.engine.lock().map_err(|e| e.to_string())?;
        engine.cancel_order(order_id);
        Ok(())
    }

    async fn get_position(&self, symbol: &str) -> Result<PositionInfo, String> {
        let engine = self.engine.lock().map_err(|e| e.to_string())?;
        let fees = engine.portfolio.state.cumulative_fees.get(symbol).copied().unwrap_or(0.0);

        if let Some(pos) = engine.portfolio.state.positions.get(symbol) {
             let cum_pnl = engine.portfolio.state.cumulative_pnl.get(symbol).copied().unwrap_or(0.0);
             Ok(PositionInfo {
                 symbol: symbol.to_string(),
                 side: match pos.side { Side::Buy => "Buy".to_string(), Side::Sell => "Sell".to_string() },
                 qty: pos.qty,
                 entry_price: pos.entry_vwap,
                 unrealized_pnl: pos.unrealized_pnl,
                 realized_fees: fees,
                 realized_funding: pos.realized_funding,
                 realized_pnl: cum_pnl,
                 margin_used: pos.margin_used,
                 notional_value: pos.notional_value,
             })
        } else {
             let cum_pnl = engine.portfolio.state.cumulative_pnl.get(symbol).copied().unwrap_or(0.0);
             Ok(PositionInfo {
                 symbol: symbol.to_string(),
                 side: "Flat".to_string(),
                 qty: 0.0,
                 entry_price: 0.0,
                 unrealized_pnl: 0.0,
                 realized_fees: fees,
                 realized_funding: 0.0,
                 realized_pnl: cum_pnl,
                 margin_used: 0.0,
                 notional_value: 0.0,
             })
        }
    }

    async fn get_equity(&self) -> Result<f64, String> {
        let engine = self.engine.lock().map_err(|e| e.to_string())?;
        Ok(engine.portfolio.state.equity_usdt)
    }

    async fn set_leverage(&mut self, symbol: &str, leverage: f64) -> Result<(), String> {
        let mut engine = self.engine.lock().map_err(|e| e.to_string())?;
        engine.portfolio.update_leverage(symbol, leverage);
        Ok(())
    }
}

// ============================================================================
//  Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    fn make_test_event(mid: f64) -> NormalizedMarketEvent {
        NormalizedMarketEvent {
            symbol: "BTCUSDT".to_string(),
            time_canonical: 1000,
            price: Some(mid),
            best_bid: Some(mid - 0.5),
            best_ask: Some(mid + 0.5),
            ..Default::default()
        }
    }

    #[test]
    fn test_market_order_flat_slippage() {
        let config = ExecutionConfig {
            slippage_model: SlippageModel::Flat(10.0), // 10 bps
            taker_fee_bps: 5.0,
            symbol_whitelist: vec!["BTCUSDT".to_string()],
            latency_ms: 0,
            ..Default::default()
        };
        let mut engine = ExecutionEngine::new(config);
        engine.current_time = 900;
        
        // Submit Market Buy
        let _order_id = engine.submit_order("BTCUSDT", Side::Buy, 0.0, 1.0, OrderType::Market);
        
        let event = make_test_event(100.0); // mid=100, ask=100.5
        let fills = engine.update(&event);
        
        assert_eq!(fills.len(), 1);
        let fill = &fills[0];
        // expected: ask (100.5) + 10bps (0.1005) = 100.6005
        assert!((fill.price - 100.6005).abs() < 1e-6);
        assert_eq!(fill.qty, 1.0);
        
        // Check FillEvent
        assert_eq!(engine.last_fill_events.len(), 1);
        let fe = &engine.last_fill_events[0];
        assert_eq!(fe.liquidity_flag, LiquidityFlag::Taker);
        assert!((fe.slippage_bps - 60.05).abs() < 1e-2); // (100.6005 - 100)/100 * 10000 = 60.05 bps
    }

    #[test]
    fn test_market_order_l2_slippage() {
        let config = ExecutionConfig {
            slippage_model: SlippageModel::TopN(5),
            symbol_whitelist: vec!["BTCUSDT".to_string()],
            latency_ms: 0,
            ..Default::default()
        };
        let mut engine = ExecutionEngine::new(config);
        engine.current_time = 1000;
        
        // Inject L2 Book
        // Buy 10 units. Ask levels:
        // 100.5 @ 4.0
        // 110.0 @ 4.0
        // 120.0 @ 4.0
        engine.set_book_levels(
            vec![(99.5, 10.0)],
            vec![(100.5, 4.0), (110.0, 4.0), (120.0, 4.0)]
        );
        
        engine.submit_order("BTCUSDT", Side::Buy, 0.0, 10.0, OrderType::Market);
        
        let event = make_test_event(100.0);
        let fills = engine.update(&event);
        
        assert_eq!(fills.len(), 1);
        let fill = &fills[0];
        // VWAP: (4*100.5 + 4*110.0 + 2*120.0) / 10 = (402 + 440 + 240) / 10 = 108.2
        assert_eq!(fill.price, 108.2);
        assert_eq!(fill.qty, 10.0);
    }

    #[test]
    fn test_partial_fill_l2_exhaustion() {
        let config = ExecutionConfig {
            slippage_model: SlippageModel::TopN(5),
            symbol_whitelist: vec!["BTCUSDT".to_string()],
            latency_ms: 0,
            ..Default::default()
        };
        let mut engine = ExecutionEngine::new(config);
        engine.current_time = 1000;
        
        // Ask levels: only 5 units available at 100.5
        engine.set_book_levels(
            vec![(99.5, 10.0)],
            vec![(100.5, 5.0)]
        );
        
        let order_id = engine.submit_order("BTCUSDT", Side::Buy, 0.0, 10.0, OrderType::Market);
        
        let event = make_test_event(100.0);
        let fills = engine.update(&event);
        
        assert_eq!(fills.len(), 1);
        let fill = &fills[0];
        assert_eq!(fill.price, 100.5);
        assert_eq!(fill.qty, 5.0);
        
        // Check order state
        let order = engine.portfolio.state.active_orders.get(&order_id).unwrap();
        assert_eq!(order.status, OrderStatus::PartiallyFilled);
        assert_eq!(order.remaining, 5.0);
    }

    #[test]
    fn test_conservative_maker_queue_depletion() {
        let config = ExecutionConfig {
            slippage_model: SlippageModel::ConservativeMaker(MakerQueueConfig {
                default_latency_ms: 0,
                assume_half_queue: true,
            }),
            symbol_whitelist: vec!["BTCUSDT".to_string()],
            latency_ms: 0,
            slip_bps: 0.0,
            ..Default::default()
        };
        let mut engine = ExecutionEngine::new(config);
        engine.current_time = 1000;
        
        // 10 units at BBO (so 5 units ahead of us due to half_queue)
        engine.set_book_levels(
            vec![(99.5, 10.0)],
            vec![]
        );
        
        let order_id = engine.submit_order("BTCUSDT", Side::Buy, 99.5, 1.0, OrderType::Limit);
        
        // Event 1: No Trades, just initializing the queue position at BBO
        let mut event1 = make_test_event(100.0);
        event1.price = None; // No trade happened
        event1.best_bid = Some(99.5);
        let fills1 = engine.update(&event1);
        assert_eq!(fills1.len(), 0);
        
        let q_state = engine.portfolio.state.active_orders.get(&order_id).unwrap().queue_state.as_ref().unwrap();
        assert_eq!(q_state.position_ahead, 5.0);
        
        // Event 2: Trade of 3.0 at our price. Queue drops to 2.0. No fills.
        let mut event2 = make_test_event(100.0);
        event2.price = Some(99.5);
        event2.qty = Some(3.0);
        event2.best_bid = Some(99.5);
        let fills2 = engine.update(&event2);
        assert_eq!(fills2.len(), 0);
        
        let q_state2 = engine.portfolio.state.active_orders.get(&order_id).unwrap().queue_state.as_ref().unwrap();
        assert_eq!(q_state2.position_ahead, 2.0);
        
        // Event 3: Trade of 3.0 at our price. Queue depleted entirely. We capture 1.0 of the overflow.
        let mut event3 = make_test_event(100.0);
        event3.price = Some(99.5);
        event3.qty = Some(3.0); // clears remaining 2.0 queue + 1.0 spillover = fills us
        event3.best_bid = Some(99.5);
        let fills3 = engine.update(&event3);
        
        assert_eq!(fills3.len(), 1);
        assert_eq!(fills3[0].qty, 1.0);
        assert_eq!(fills3[0].price, 99.5);
        
        let final_order = engine.portfolio.state.active_orders.get(&order_id);
        assert!(final_order.is_none(), "Filled order should be removed from active_orders");
    }
}
