use bot_data::simulation::portfolio::PortfolioManager;
use bot_data::simulation::structs::Side;

fn assert_approx(left: f64, right: f64) {
    let diff = (left - right).abs();
    assert!(diff < 1e-9, "left: {}, right: {}, diff: {}", left, right, diff);
}

#[test]
fn test_accounting_parity_basic_flow() {
    let initial_cash = 1000.0;
    let mut pm = PortfolioManager::new(initial_cash);

    let symbol = "BTCUSDT";
    let ts = 1700000000000;

    // 1. Entry: Buy 0.1 BTC at 40,000. Fee 0.8 USDT
    pm.on_fill(symbol, Side::Buy, 0.1, 40000.0, 0.8, ts);

    assert_approx(pm.state.cash_usdt, initial_cash - 0.8);
    assert_approx(pm.state.trading_fees_entry, 0.8);
    assert_approx(pm.state.trading_fees_exit, 0.0);
    
    let pos = pm.state.positions.get(symbol).unwrap();
    assert_approx(pos.qty, 0.1);
    assert_approx(pos.entry_vwap, 40000.0);
    assert_approx(pos.realized_fees, 0.8);

    // 2. Partial Exit: Sell 0.05 BTC at 42,000. 
    // Gross PnL = (42000-40000)*0.05 = 100.
    // Exit Fee = 0.42.
    pm.on_fill(symbol, Side::Sell, 0.05, 42000.0, 0.42, ts + 1000);

    assert_approx(pm.state.trading_fees_exit, 0.42);
    // Realized PnL is tracked in cumulative_pnl map
    assert_approx(*pm.state.cumulative_pnl.get(symbol).unwrap(), 100.0);
    // Cash: 999.2 (after entry) + 100.0 (pnl) - 0.42 (exit fee) = 1098.78
    assert_approx(pm.state.cash_usdt, 1098.78);

    // 3. Apply Funding: Loss of 1.0 USDT
    // Notional is 0.05 * 40000 = 2000.
    // To lose 1.0 USDT on Long: actual_pnl = -1.0
    // actual_pnl = - (qty * price * rate) => -1.0 = - (2000 * rate) => rate = 0.0005
    pm.apply_funding(symbol, 0.0005);
    assert_approx(pm.state.funding_pnl, -1.0);
    assert_approx(pm.state.cash_usdt, 1097.78);
    
    let pos = pm.state.positions.get(symbol).unwrap();
    assert_approx(pos.qty, 0.05);
    assert_approx(pos.realized_funding, -1.0);
}

#[test]
fn test_accounting_parity_position_flip() {
    let initial_cash = 1000.0;
    let mut pm = PortfolioManager::new(initial_cash);

    let symbol = "BTCUSDT";
    let ts = 1700000000000;

    // 1. Long 0.1 at 40k. Entry Fee 1.0
    pm.on_fill(symbol, Side::Buy, 0.1, 40000.0, 1.0, ts);

    // 2. Flip to Short 0.1. Must sell 0.2 total.
    // Sell 0.2 at 41k. Fee 2.0.
    pm.on_fill(symbol, Side::Sell, 0.2, 41000.0, 2.0, ts + 1000);

    assert_approx(pm.state.trading_fees_entry, 1.0 + 1.0); 
    assert_approx(pm.state.trading_fees_exit, 1.0); 
    assert_approx(*pm.state.cumulative_pnl.get(symbol).unwrap(), 100.0);
    
    let pos = pm.state.positions.get(symbol).unwrap();
    assert_eq!(pos.side, Side::Sell);
    assert_approx(pos.qty, 0.1);
    assert_approx(pos.entry_vwap, 41000.0);
}
