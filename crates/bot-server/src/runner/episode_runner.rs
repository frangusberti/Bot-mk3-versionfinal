use log::info;
use bot_data::features_v2::FeatureEngineV2;
use bot_data::strategy::{Strategy, Observation, StrategyContext, AccountSnapshot, PositionSnapshot};
use bot_core::proto::Action;
use bot_data::simulation::execution::ExecutionEngine;
use bot_data::replay::reader::BatchedReplayReader; 
use bot_data::normalization::schema::NormalizedMarketEvent;
use bot_data::simulation::structs::{Side, OrderType};
use bot_data::reporting::backtest::{BacktestReport, EquityPoint};
use bot_data::orderbook::engine::OrderBook;

pub trait Policy {
    fn act(&mut self, obs: &Observation, ctx: &mut StrategyContext) -> Action;
    fn name(&self) -> &str;
}

pub struct StrategyPolicy {
    strategy: Box<dyn Strategy>,
}

pub struct Environment {
    pub reader: BatchedReplayReader,
    pub feature_engine: FeatureEngineV2,
    pub execution_engine: ExecutionEngine,
    pub symbol: String,
    pub dataset_id: String,
    pub report: BacktestReport,
}

impl StrategyPolicy {
    pub fn new(strategy: Box<dyn Strategy>) -> Self {
        Self { strategy }
    }
}

impl Policy for StrategyPolicy {
    fn act(&mut self, obs: &Observation, ctx: &mut StrategyContext) -> Action {
        let action = self.strategy.on_observation(obs, ctx);
        // Map StrategyAction to proto Action
        // This mapping is simplified for now.
        use bot_data::strategy::StrategyAction;
        let at = match action {
            StrategyAction::Flat { .. } => bot_core::proto::ActionType::Hold as i32,
            StrategyAction::EnterLong { .. } => bot_core::proto::ActionType::OpenLong as i32,
            StrategyAction::EnterShort { .. } => bot_core::proto::ActionType::OpenShort as i32,
            StrategyAction::Exit { .. } => bot_core::proto::ActionType::Hold as i32, // Logic moved to apply_action
        };
        Action {
            r#type: at,
        }
    }
    
    fn name(&self) -> &str {
        self.strategy.name()
    }
}

pub struct EpisodeRunner<P: Policy> {
    policy: P,
}

impl<P: Policy> EpisodeRunner<P> {
    pub fn new(policy: P) -> Self {
        Self { policy }
    }

    pub async fn run(&mut self, env: &mut Environment) -> Result<(), anyhow::Error> {
        info!("Starting episode with policy: {}", self.policy.name());
        
        let mut ctx = StrategyContext { symbol: env.symbol.clone() };
        let mut ob = OrderBook::new(env.symbol.clone());
        
        // Loop through Replay
        for row in env.reader.by_ref() {
             let event = Self::convert_event(row.event, &env.dataset_id);
             
             // 1. Update OrderBook state for ExecutionEngine realism
             if event.event_type == "depthUpdate" {
                if let Ok(depth) = serde_json::from_str::<bot_data::binance::model::DepthUpdate>(&event.payload_json) {
                    ob.apply_delta(
                        event.update_id_first.unwrap_or(depth.first_update_id), 
                        event.update_id_final.unwrap_or(depth.final_update_id), 
                        event.update_id_prev.unwrap_or(depth.prev_update_id), 
                        depth.bids, 
                        depth.asks
                    );
                }
             } else if event.event_type == "bookTicker" {
                if let Ok(ticker) = serde_json::from_str::<bot_data::binance::model::BookTicker>(&event.payload_json) {
                    // Update OrderBook with BBO snapshot if not in sync (fallback)
                    if !ob.is_sync() {
                        ob.apply_snapshot(ticker.update_id, vec![(ticker.best_bid_price, ticker.best_bid_qty)], vec![(ticker.best_ask_price, ticker.best_ask_qty)]);
                        // Force InSync for backtest realism if we only have L1
                        ob.status = bot_data::orderbook::engine::OrderBookStatus::InSync;
                    }
                }
             }
             
             // Sync ob levels to execution engine
             env.execution_engine.set_book_levels(ob.top_bids(20), ob.top_asks(20));

             // 2. Update Feature Engine
             env.feature_engine.update(&event);
             
             // 3. Execution Engine Update
             // This processes market updates, triggers fills/stops for existing orders.
             let fills = env.execution_engine.update(&event);
             env.report.executions.extend(fills);
             
             // 4. Check for Decision Tick
             if let Some(feature_row) = env.feature_engine.maybe_emit(event.time_canonical) {
                  // 5. Construct Observation
                  let state = &env.execution_engine.portfolio.state;
                  // Get position for the symbol
                  let (pos_qty, entry_px, upnl) = if let Some(pos) = state.positions.get(&env.symbol) {
                      (pos.qty, pos.entry_vwap, pos.unrealized_pnl)
                  } else {
                      (0.0, 0.0, 0.0)
                  };

                  let obs = Observation {
                      ts: feature_row.t_emit,
                      symbol: env.symbol.clone(),
                      features: feature_row,
                      account: AccountSnapshot {
                          equity: state.equity_usdt,
                          cash: state.cash_usdt,
                          margin_used: state.margin_used,
                          available_margin: state.available_margin,
                          drawdown_pct: if state.equity_usdt < env.execution_engine.daily_high_equity {
                              (env.execution_engine.daily_high_equity - state.equity_usdt) / env.execution_engine.daily_high_equity
                          } else { 0.0 },
                      },
                      position: PositionSnapshot {
                          qty: if let Some(p) = state.positions.get(&env.symbol) {
                              if p.side == Side::Buy { p.qty } else { -p.qty }
                          } else { 0.0 },
                          entry_price: entry_px,
                          unrealized_pnl: upnl,
                          latent_pnl_pct: if entry_px > 0.0 && pos_qty.abs() > 0.0 { upnl / (pos_qty.abs() * entry_px) } else { 0.0 },
                          max_pnl_pct: 0.0, // TODO: Tracks highest reached pnl during this position
                          holding_ms: if let Some(pos) = state.positions.get(&env.symbol) {
                              event.time_canonical - pos.open_ts
                          } else { 0 },
                      },
                  };
                  
                  // 6. Query Policy
                  let action = self.policy.act(&obs, &mut ctx);
                  
                  // 7. Execute Action
                  // We need to translate `Action` (proto) to `submit_order` calls.
                  Self::apply_action(&mut env.execution_engine, &env.symbol, action);
                  
                  // 8. Log Equity Point
                  // Optimization: Don't log every tick if high frequency?
                  // Doing it on every decision tick is reasonable.
                  let equity = env.execution_engine.portfolio.state.equity_usdt;
                  let dd_pct = if equity < env.execution_engine.daily_high_equity {
                      (env.execution_engine.daily_high_equity - equity) / env.execution_engine.daily_high_equity
                  } else { 0.0 };
                  
                  env.report.equity_curve.push(EquityPoint {
                      ts: obs.ts,
                      equity,
                      drawdown_pct: dd_pct,
                  });
             }
        }
        
        // Finalize metrics
        env.report.reconstruct_trades_from_executions();
        env.report.compute_metrics();
        
        info!("Episode finished. Final Equity: {}", env.execution_engine.portfolio.state.equity_usdt);
        Ok(())
    }
    
    fn convert_event(re: bot_data::replay::events::ReplayEvent, _dataset_id: &str) -> NormalizedMarketEvent {
        // Map fields
        NormalizedMarketEvent {
            schema_version: 1,
            run_id: "backtest".to_string(), // TODO
            exchange: "binance".to_string(),
            market_type: "future".to_string(),
            symbol: re.symbol,
            stream_name: re.stream_name,
            event_type: re.event_type,
            time_exchange: re.ts_exchange,
            time_local: re.ts_local,
            time_canonical: re.ts_canonical,
            recv_time: None,
            price: re.price,
            qty: re.quantity,
            side: re.side,
            best_bid: re.best_bid,
            best_ask: re.best_ask,
            mark_price: re.mark_price,
            funding_rate: re.funding_rate,
            liquidation_price: re.liquidation_price,
            liquidation_qty: re.liquidation_qty,
            open_interest: re.open_interest,
            open_interest_value: re.open_interest_value,
            update_id_first: None,
            update_id_final: None,
            update_id_prev: None,
            payload_json: re.payload_json.unwrap_or_default(),
        }
    }
    
    fn apply_action(engine: &mut ExecutionEngine, symbol: &str, action: Action) {
        use bot_core::proto::ActionType;
        
        let at = match ActionType::try_from(action.r#type).unwrap_or(ActionType::Hold) {
            ActionType::Hold => return,
            t => t,
        };
        
        let quantity = 0.01; // TODO: Configurable sizing
        
        match at {
            ActionType::OpenLong | ActionType::AddLong => {
                 engine.submit_order(symbol, Side::Buy, 0.0, quantity, OrderType::Market);
            },
            ActionType::OpenShort | ActionType::AddShort => {
                 engine.submit_order(symbol, Side::Sell, 0.0, quantity, OrderType::Market);
            },
            ActionType::ReduceLong | ActionType::CloseLong => {
                if let Some(pos) = engine.portfolio.state.positions.get(symbol) {
                    if pos.side == Side::Buy && pos.qty > 0.0 {
                        let reduce_qty = if at == ActionType::ReduceLong { pos.qty * 0.5 } else { pos.qty };
                        engine.submit_order(symbol, Side::Sell, 0.0, reduce_qty, OrderType::Market);
                    }
                }
            },
            ActionType::ReduceShort | ActionType::CloseShort => {
                if let Some(pos) = engine.portfolio.state.positions.get(symbol) {
                    if pos.side == Side::Sell && pos.qty > 0.0 {
                        let reduce_qty = if at == ActionType::ReduceShort { pos.qty * 0.5 } else { pos.qty };
                        engine.submit_order(symbol, Side::Buy, 0.0, reduce_qty, OrderType::Market);
                    }
                }
            },
            _ => {}
        }
    }
}
