use bot_data::recorder::Recorder;
use bot_data::normalization::schema::{NormalizedMarketEvent, TimeMode};
use bot_data::replay::engine::ReplayEngine;
use bot_core::schema::{Trade, Exchange, Side};
use chrono::{Utc, TimeZone};
use rust_decimal::Decimal;
use std::str::FromStr;

#[test]
fn test_record_replay_loop() -> anyhow::Result<()> {
    // 1. Setup temp dir
    let temp_dir = tempfile::tempdir()?;
    let path = temp_dir.path().to_path_buf();

    // 2. Create Recorder
    let mut recorder = Recorder::new(path.clone(), 2); // Buffer limit 2 to force flushes

    // 3. Generate Trades
    let t1 = Trade {
        exchange: Exchange::Binance,
        symbol: "BTCUSDT".to_string(),
        trade_id: "1001".to_string(),
        price: Decimal::from_str("50000.50")?,
        quantity: Decimal::from_str("0.1")?,
        side: Side::Buy,
        timestamp: Utc.timestamp_opt(1600000000, 0).unwrap(),
        is_liquidation: false,
    };

    let t2 = Trade {
        exchange: Exchange::Binance,
        symbol: "BTCUSDT".to_string(),
        trade_id: "1002".to_string(),
        price: Decimal::from_str("50010.00")?,
        quantity: Decimal::from_str("0.5")?,
        side: Side::Sell,
        timestamp: Utc.timestamp_opt(1600000001, 100).unwrap(), // with nanos
        is_liquidation: true,
    };
    
    // 4. Record
    recorder.record_trade(t1.clone())?;
    recorder.record_trade(t2.clone())?;
    // Buffer limit is 2, so it should have flushed automatically or we flush manually to be safe
    recorder.flush()?;

    // 5. Replay
    let mut replayer = bot_data::replay::engine::ReplayEngine::new(
        path.clone(),
        bot_data::replay::types::ReplayConfig::default()
    )?;
    
    let rt1 = replayer.next_event().expect("Should have event 1");
    let rt2 = replayer.next_event().expect("Should have event 2");
    let rt3 = replayer.next_event();

    // 6. Assert
    assert_eq!(t1.price, rust_decimal::Decimal::from_f64_retain(rt1.price.unwrap()).unwrap(), "Trade 1 price mismatch");
    assert_eq!(t2.price, rust_decimal::Decimal::from_f64_retain(rt2.price.unwrap()).unwrap(), "Trade 2 price mismatch");
    assert!(rt3.is_none(), "Should have no more trades");

    Ok(())
}
