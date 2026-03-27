use bot_data::features_v2::{FeatureEngineV2, FeatureEngineV2Config};
use bot_data::normalization::schema::NormalizedMarketEvent;
use sha2::{Sha256, Digest};
use std::fmt::Write;

const NUM_TICKS: usize = 600;

fn make_bookticker(ts: i64, bid: f64, ask: f64) -> NormalizedMarketEvent {
    NormalizedMarketEvent {
        time_canonical: ts,
        stream_name: "bookTicker".to_string(),
        event_type: "bookTicker".to_string(),
        best_bid: Some(bid),
        best_ask: Some(ask),
        payload_json: format!("{{\"b\":\"{}\",\"a\":\"{}\",\"B\":\"1.0\",\"A\":\"1.0\"}}", bid, ask),
        ..Default::default()
    }
}

fn make_aggtrade(ts: i64, price: f64, qty: f64, is_buyer_maker: bool) -> NormalizedMarketEvent {
    NormalizedMarketEvent {
        time_canonical: ts,
        stream_name: "aggTrade".to_string(),
        event_type: "trade".to_string(),
        price: Some(price),
        qty: Some(qty),
        payload_json: format!("{{\"m\":{}}}", is_buyer_maker),
        ..Default::default()
    }
}

fn make_mark_price(ts: i64, mark: f64, funding: f64) -> NormalizedMarketEvent {
    NormalizedMarketEvent {
        time_canonical: ts,
        stream_name: "markPrice".to_string(),
        event_type: "markPrice".to_string(),
        mark_price: Some(mark),
        funding_rate: Some(funding),
        ..Default::default()
    }
}

fn make_oi(ts: i64, oi_val: f64) -> NormalizedMarketEvent {
    NormalizedMarketEvent {
        time_canonical: ts,
        stream_name: "openInterest".to_string(),
        event_type: "openInterest".to_string(),
        open_interest: Some(oi_val),
        ..Default::default()
    }
}

fn make_orderbook(mid: f64, tick_size: f64, base_qty: f64, imbalance: f64) -> (Vec<(f64, f64)>, Vec<(f64, f64)>) {
    let mut bids = Vec::with_capacity(10);
    let mut asks = Vec::with_capacity(10);
    for i in 0..10 {
        let offset = tick_size * (i + 1) as f64;
        let bid_qty = base_qty * (1.0 + imbalance * 0.3) * (1.0 - i as f64 * 0.05);
        let ask_qty = base_qty * (1.0 - imbalance * 0.3) * (1.0 - i as f64 * 0.05);
        bids.push((mid - offset, bid_qty.max(0.01)));
        asks.push((mid + offset, ask_qty.max(0.01)));
    }
    (bids, asks)
}

#[test]
fn test_golden_schema_v6_hash() {
    let mut config = FeatureEngineV2Config::default();
    config.symbol = "BTCUSDT".to_string();
    config.interval_ms = 1000; // 1 second emit window

    let mut engine = FeatureEngineV2::new(config);
    
    // We must mimic the way account state is initialized in diagnostics
    engine.set_account_state(bot_data::features_v2::compute_account::AccountState {
        position_flag: 0.0,
        latent_pnl_pct: 0.0,
        max_pnl_pct: 0.0,
        current_drawdown_pct: 0.0,
    });
    engine.set_orderbook_in_sync(true);
    let mut hasher = Sha256::new();

    let base_ts = 1700000000000;
    let mut current_mid = 50000.0;
    
    // Deterministic state generation
    for i in 0..NUM_TICKS {
        let ts = base_ts + (i as i64 * 1000);
        let cycle = (i as f64 / 60.0) * std::f64::consts::PI;
        current_mid += cycle.sin() * 10.0;
        
        // 1. Send Orderbook
        let spread = 2.0 + (i as f64 % 5.0).abs();
        let bid = current_mid - (spread / 2.0);
        let ask = current_mid + (spread / 2.0);
        
        let (bids, asks) = make_orderbook(current_mid, 0.1, 10.0, cycle.cos());
        engine.set_orderbook_levels(bids, asks);

        // 2. Send Book Ticker
        engine.update(&make_bookticker(ts, bid, ask));
        
        // 3. Send Trades
        if i % 2 == 0 {
            engine.update(&make_aggtrade(ts, bid, 1.5, true)); // maker buy
        } else {
            engine.update(&make_aggtrade(ts, ask.max(bid + 0.1), 2.5, false)); // maker sell
        }
        
        // 4. Mark / Funding occasionally
        if i % 60 == 0 {
            engine.update(&make_mark_price(ts, current_mid + 1.0, 0.0001));
            engine.update(&make_oi(ts, 10000.0 + i as f64 * 10.0));
        }

        // Try to emit at end of window
        if let Some(row) = engine.maybe_emit(ts) {
            let (obs, _clamped) = row.to_obs_vec();
            
            // Serialize precisely to accumulate hash
            let mut row_str = String::new();
            for val in &obs {
                write!(&mut row_str, "{:.6},", val).unwrap();
            }
            hasher.update(row_str.as_bytes());
        }
    }

    let hash_result = hasher.finalize();
    let golden_hash = format!("{:x}", hash_result);

    println!("Golden Schema v6 Hash: {}", golden_hash);

    // Established golden signature for Schema v6 (148 dimensions) over 600 ticks
    let expected_golden_hash = "bfda6dbb894e8662e32b07b0a5b22c85fa8c83f6949cb8c2b6a2c348ca635df5";
    
    assert_eq!(
        golden_hash, 
        expected_golden_hash,
        "CRITICAL FAILURE: the schema / feature generation logic has drifted. Re-run golden baseline explicitly if changes were intended."
    );
}
