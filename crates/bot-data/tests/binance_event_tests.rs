use bot_data::binance::model::{BinanceEvent, BookTicker, AggTrade, DepthUpdate};
use rust_decimal::Decimal;
use std::str::FromStr;

#[test]
fn test_parse_book_ticker() {
    let json = r#"
    {
      "e": "bookTicker",
      "u": 400900217,
      "s": "BNBUSDT",
      "b": "25.35190000",
      "B": "31.21000000",
      "a": "25.36520000",
      "A": "40.66000000",
      "T": 1560966035651,
      "E": 1560966035652
    }
    "#;

    let event: BinanceEvent = serde_json::from_str(json).expect("Failed to parse bookTicker");
    
    match event {
        BinanceEvent::BookTicker(t) => {
            assert_eq!(t.symbol, "BNBUSDT");
            assert_eq!(t.best_bid_price, Decimal::from_str("25.3519").unwrap());
            assert_eq!(t.best_ask_qty, Decimal::from_str("40.66").unwrap());
            assert_eq!(t.event_time, 1560966035652);
        },
        _ => panic!("Expected BookTicker"),
    }
}

#[test]
fn test_parse_agg_trade() {
    let json = r#"
    {
      "e": "aggTrade",
      "E": 1560966035652,
      "s": "BNBUSDT",
      "a": 12345,
      "p": "0.001",
      "q": "100",
      "f": 100,
      "l": 105,
      "T": 123456785,
      "m": true
    }
    "#;

    let event: BinanceEvent = serde_json::from_str(json).expect("Failed to parse aggTrade");
    
    match event {
        BinanceEvent::AggTrade(t) => {
            assert_eq!(t.symbol, "BNBUSDT");
            assert_eq!(t.price, Decimal::from_str("0.001").unwrap());
            assert_eq!(t.quantity, Decimal::from_str("100").unwrap());
            assert!(t.is_buyer_maker);
        },
        _ => panic!("Expected AggTrade"),
    }
}

#[test]
fn test_parse_depth_update() {
    let json = r#"
    {
      "e": "depthUpdate",
      "E": 1560966035652,
      "T": 1560966035650,
      "s": "BNBUSDT",
      "U": 157,
      "u": 160,
      "pu": 156,
      "b": [
        ["0.0024", "10"],
        ["0.0025", "5"]
      ],
      "a": [
        ["0.0026", "100"]
      ]
    }
    "#;

    let event: BinanceEvent = serde_json::from_str(json).expect("Failed to parse depthUpdate");
    
    match event {
        BinanceEvent::DepthUpdate(d) => {
            assert_eq!(d.symbol, "BNBUSDT");
            assert_eq!(d.first_update_id, 157);
             assert_eq!(d.bids.len(), 2);
            assert_eq!(d.bids[0].0, Decimal::from_str("0.0024").unwrap());
            assert_eq!(d.bids[0].1, Decimal::from_str("10").unwrap());
            assert_eq!(d.asks.len(), 1);
        },
        _ => panic!("Expected DepthUpdate"),
    }
}

#[test]
fn test_parse_mark_price_update() {
    let json = r#"
    {
      "e": "markPriceUpdate",
      "E": 1560966035652,
      "s": "BNBUSDT",
      "p": "25.35190000",
      "i": "25.35190000",
      "P": "25.35190000",
      "r": "0.00010000",
      "T": 1560966035651
    }
    "#;

    let event: BinanceEvent = serde_json::from_str(json).expect("Failed to parse markPriceUpdate");
    
    match event {
        BinanceEvent::MarkPriceUpdate(m) => {
            assert_eq!(m.symbol, "BNBUSDT");
            assert_eq!(m.mark_price, Decimal::from_str("25.3519").unwrap());
            assert_eq!(m.funding_rate, Decimal::from_str("0.0001").unwrap());
        },
        _ => panic!("Expected MarkPriceUpdate"),
    }
}
