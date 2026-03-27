use arrow::array::{
    Float64Array, StringArray, TimestampMicrosecondArray, BooleanArray
};
use arrow::datatypes::{DataType, Field, Schema, TimeUnit};
use arrow::record_batch::RecordBatch;
use bot_core::schema::{Trade, Exchange, Side};
use chrono::{TimeZone, Utc};
use rust_decimal::prelude::{FromPrimitive, ToPrimitive};
use rust_decimal::Decimal;
use std::sync::Arc;

pub fn trades_to_record_batch(trades: &[Trade]) -> anyhow::Result<RecordBatch> {
    let exchanges: Vec<String> = trades.iter().map(|t| format!("{:?}", t.exchange)).collect();
    let symbols: Vec<String> = trades.iter().map(|t| t.symbol.clone()).collect();
    let trade_ids: Vec<String> = trades.iter().map(|t| t.trade_id.clone()).collect();
    let prices: Vec<f64> = trades.iter().map(|t| t.price.to_f64().unwrap_or(0.0)).collect();
    let quantities: Vec<f64> = trades.iter().map(|t| t.quantity.to_f64().unwrap_or(0.0)).collect();
    let sides: Vec<String> = trades.iter().map(|t| format!("{:?}", t.side)).collect();
    let is_liquidations: Vec<bool> = trades.iter().map(|t| t.is_liquidation).collect();
    let timestamps: Vec<i64> = trades.iter().map(|t| t.timestamp.timestamp_micros()).collect();

    let schema = Schema::new(vec![
        Field::new("exchange", DataType::Utf8, false),
        Field::new("symbol", DataType::Utf8, false),
        Field::new("trade_id", DataType::Utf8, false),
        Field::new("price", DataType::Float64, false),
        Field::new("quantity", DataType::Float64, false),
        Field::new("side", DataType::Utf8, false),
        Field::new("is_liquidation", DataType::Boolean, false),
        Field::new("timestamp", DataType::Timestamp(TimeUnit::Microsecond, None), false),
    ]);

    let batch = RecordBatch::try_new(
        Arc::new(schema),
        vec![
            Arc::new(StringArray::from(exchanges)),
            Arc::new(StringArray::from(symbols)),
            Arc::new(StringArray::from(trade_ids)),
            Arc::new(Float64Array::from(prices)),
            Arc::new(Float64Array::from(quantities)),
            Arc::new(StringArray::from(sides)),
            Arc::new(BooleanArray::from(is_liquidations)),
            Arc::new(TimestampMicrosecondArray::from(timestamps)),
        ],
    )?;

    Ok(batch)
}

pub fn record_batch_to_trades(batch: &RecordBatch) -> anyhow::Result<Vec<Trade>> {
    let mut trades = Vec::with_capacity(batch.num_rows());
    
    let exchanges = batch.column(0).as_any().downcast_ref::<StringArray>().unwrap();
    let symbols = batch.column(1).as_any().downcast_ref::<StringArray>().unwrap();
    let trade_ids = batch.column(2).as_any().downcast_ref::<StringArray>().unwrap();
    let prices = batch.column(3).as_any().downcast_ref::<Float64Array>().unwrap();
    let quantities = batch.column(4).as_any().downcast_ref::<Float64Array>().unwrap();
    let sides = batch.column(5).as_any().downcast_ref::<StringArray>().unwrap();
    let is_liquidations = batch.column(6).as_any().downcast_ref::<BooleanArray>().unwrap();
    let timestamps = batch.column(7).as_any().downcast_ref::<TimestampMicrosecondArray>().unwrap();

    for i in 0..batch.num_rows() {
        let exchange_str = exchanges.value(i);
        let exchange = match exchange_str {
            "Binance" => Exchange::Binance,
            "Bybit" => Exchange::Bybit,
            "Okx" => Exchange::Okx,
            _ => Exchange::Backtest, 
        };
        
        let side_str = sides.value(i);
        let side = match side_str {
            "Buy" => Side::Buy,
            _ => Side::Sell,
        };

        // Handle timestamp
        let ts_micros = timestamps.value(i);
        let timestamp = Utc.timestamp_opt(ts_micros / 1_000_000, (ts_micros % 1_000_000) as u32 * 1000)
            .single()
            .unwrap_or(Utc::now());

        trades.push(Trade {
            exchange,
            symbol: symbols.value(i).to_string(),
            trade_id: trade_ids.value(i).to_string(),
            price: Decimal::from_f64(prices.value(i)).unwrap_or_default(),
            quantity: Decimal::from_f64(quantities.value(i)).unwrap_or_default(),
            side,
            is_liquidation: is_liquidations.value(i),
            timestamp,
        });
    }

    Ok(trades)
}
