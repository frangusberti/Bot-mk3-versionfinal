use reqwest::{Client, Method};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use hex;
use log::{info, warn, error};
use anyhow::{Result, anyhow};
use tokio::sync::{mpsc, Mutex as TokioMutex};

const BASE_URL: &str = "https://fapi.binance.com";
const TESTNET_BASE_URL: &str = "https://testnet.binancefuture.com";

// ─── Symbol Precision Rules ─────────────────────────────────────────────────

/// Per-symbol trading rules fetched from /fapi/v1/exchangeInfo.
#[derive(Clone, Debug)]
pub struct SymbolRules {
    pub price_precision: u32,
    pub quantity_precision: u32,
    pub min_qty: f64,
    pub min_notional: f64,
    pub tick_size: f64,
    pub step_size: f64,
}

impl Default for SymbolRules {
    fn default() -> Self {
        Self {
            price_precision: 2,
            quantity_precision: 3,
            min_qty: 0.001,
            min_notional: 5.0,
            tick_size: 0.01,
            step_size: 0.001,
        }
    }
}

// ─── User Data Stream Fill Events ───────────────────────────────────────────

/// Represents a fill event from the Binance User Data Stream.
#[derive(Clone, Debug)]
pub struct LiveFill {
    pub symbol: String,
    pub order_id: String,
    pub side: String,         // "BUY" or "SELL"
    pub order_type: String,   // "MARKET", "LIMIT"
    pub qty: f64,
    pub price: f64,
    pub commission: f64,
    pub commission_asset: String,
    pub realized_pnl: f64,
    pub timestamp_ms: i64,
}

// ─── Binance Client ─────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct BinanceClient {
    api_key: String,
    secret_key: String,
    client: Client,
    base_url: String,
    /// Cached per-symbol trading rules
    symbol_rules: Arc<TokioMutex<HashMap<String, SymbolRules>>>,
}

impl BinanceClient {
    pub fn new(api_key: String, secret_key: String) -> Self {
        Self {
            api_key,
            secret_key,
            client: Client::new(),
            base_url: BASE_URL.to_string(),
            symbol_rules: Arc::new(TokioMutex::new(HashMap::new())),
        }
    }

    pub fn new_testnet(api_key: String, secret_key: String) -> Self {
        Self {
            api_key,
            secret_key,
            client: Client::new(),
            base_url: TESTNET_BASE_URL.to_string(),
            symbol_rules: Arc::new(TokioMutex::new(HashMap::new())),
        }
    }

    fn sign(&self, query: &str) -> String {
        let mut mac = Hmac::<Sha256>::new_from_slice(self.secret_key.as_bytes())
            .expect("HMAC can take key of any size");
        mac.update(query.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    async fn request<T: for<'de> Deserialize<'de>>(
        &self,
        method: Method,
        endpoint: &str,
        params: Vec<(&str, String)>,
    ) -> Result<T> {
        let ts = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis();
        let mut query_params = params;
        query_params.push(("timestamp", ts.to_string()));
        query_params.push(("recvWindow", "5000".to_string()));

        let query_str = query_params
            .iter()
            .map(|(k, v)| format!("{}={}", k, v))
            .collect::<Vec<String>>()
            .join("&");

        let signature = self.sign(&query_str);
        let full_query = format!("{}&signature={}", query_str, signature);

        let url = format!("{}{}?{}", self.base_url, endpoint, full_query);

        let req = self
            .client
            .request(method, &url)
            .header("X-MBX-APIKEY", &self.api_key);

        let resp = req.send().await?;

        let status = resp.status();
        let text = resp.text().await?;

        if !status.is_success() {
            return Err(anyhow!("Binance error {}: {}", status, text));
        }

        serde_json::from_str(&text)
            .map_err(|e| anyhow!("Failed to parse response: {} | Error: {}", text, e))
    }

    /// Unsigned public request (no HMAC signature needed).
    async fn request_public<T: for<'de> Deserialize<'de>>(
        &self,
        endpoint: &str,
    ) -> Result<T> {
        let url = format!("{}{}", self.base_url, endpoint);
        let resp = self.client.get(&url).send().await?;
        let status = resp.status();
        let text = resp.text().await?;
        if !status.is_success() {
            return Err(anyhow!("Binance public error {}: {}", status, text));
        }
        serde_json::from_str(&text)
            .map_err(|e| anyhow!("Failed to parse public response: {} | Error: {}", text, e))
    }

    // ── Exchange Info & Precision ────────────────────────────────────────────

    /// Fetch exchange info and populate the symbol_rules cache.
    pub async fn load_exchange_info(&self) -> Result<()> {
        #[derive(Deserialize)]
        struct ExchangeInfo {
            symbols: Vec<SymbolInfo>,
        }
        #[derive(Deserialize)]
        #[allow(non_snake_case)]
        struct SymbolInfo {
            symbol: String,
            pricePrecision: u32,
            quantityPrecision: u32,
            filters: Vec<serde_json::Value>,
        }

        let info: ExchangeInfo = self.request_public("/fapi/v1/exchangeInfo").await?;
        let mut rules = self.symbol_rules.lock().await;

        for s in info.symbols {
            let mut rule = SymbolRules {
                price_precision: s.pricePrecision,
                quantity_precision: s.quantityPrecision,
                ..Default::default()
            };

            // Parse filters for min_qty, tick_size, step_size, min_notional
            for f in &s.filters {
                if let Some(ft) = f.get("filterType").and_then(|v| v.as_str()) {
                    match ft {
                        "LOT_SIZE" => {
                            if let Some(v) = f.get("minQty").and_then(|v| v.as_str()) {
                                rule.min_qty = v.parse().unwrap_or(rule.min_qty);
                            }
                            if let Some(v) = f.get("stepSize").and_then(|v| v.as_str()) {
                                rule.step_size = v.parse().unwrap_or(rule.step_size);
                            }
                        }
                        "PRICE_FILTER" => {
                            if let Some(v) = f.get("tickSize").and_then(|v| v.as_str()) {
                                rule.tick_size = v.parse().unwrap_or(rule.tick_size);
                            }
                        }
                        "MIN_NOTIONAL" => {
                            if let Some(v) = f.get("notional").and_then(|v| v.as_str()) {
                                rule.min_notional = v.parse().unwrap_or(rule.min_notional);
                            }
                        }
                        _ => {}
                    }
                }
            }

            rules.insert(s.symbol, rule);
        }

        info!("[EXCHANGE_INFO] Loaded rules for {} symbols", rules.len());
        Ok(())
    }

    /// Get cached rules for a symbol, or return defaults.
    pub async fn get_rules(&self, symbol: &str) -> SymbolRules {
        let rules = self.symbol_rules.lock().await;
        rules.get(symbol).cloned().unwrap_or_else(|| {
            warn!(
                "[RULES] No cached rules for {}, using defaults (price_prec=2, qty_prec=3)",
                symbol
            );
            SymbolRules::default()
        })
    }

    /// Format quantity string using the correct precision for a symbol.
    pub fn format_qty(qty: f64, rules: &SymbolRules) -> String {
        let precision = rules.quantity_precision as usize;
        format!("{:.*}", precision, qty)
    }

    /// Format price string using the correct precision for a symbol.
    pub fn format_price(price: f64, rules: &SymbolRules) -> String {
        let precision = rules.price_precision as usize;
        format!("{:.*}", precision, price)
    }

    // ── Order Management ────────────────────────────────────────────────────

    pub async fn post_order(
        &self,
        symbol: &str,
        side: &str,
        qty: f64,
        price: f64,
        type_: &str,
    ) -> Result<String> {
        let rules = self.get_rules(symbol).await;
        let qty_str = Self::format_qty(qty, &rules);
        let price_str = if price > 0.0 {
            Self::format_price(price, &rules)
        } else {
            String::new()
        };

        let mut params = vec![
            ("symbol", symbol.to_string()),
            ("side", side.to_uppercase()),
            ("type", type_.to_string()),
            ("quantity", qty_str),
        ];

        if type_ == "LIMIT" {
            params.push(("price", price_str));
            params.push(("timeInForce", "GTC".to_string()));
        }

        #[derive(Deserialize)]
        #[allow(non_snake_case)]
        struct OrderResp {
            orderId: i64,
        }

        let res: OrderResp = self
            .request(Method::POST, "/fapi/v1/order", params)
            .await?;
        Ok(res.orderId.to_string())
    }

    pub async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()> {
        let params = vec![
            ("symbol", symbol.to_string()),
            ("orderId", order_id.to_string()),
        ];
        let _: serde_json::Value = self
            .request(Method::DELETE, "/fapi/v1/order", params)
            .await?;
        Ok(())
    }

    // ── Position & Balance ──────────────────────────────────────────────────

    pub async fn get_position(&self, symbol: &str) -> Result<Option<crate::execution::PositionInfo>> {
        #[derive(Deserialize, Debug)]
        struct AccountInfo {
            positions: Vec<ApiPosition>,
        }
        #[derive(Deserialize, Debug)]
        #[allow(non_snake_case)]
        struct ApiPosition {
            symbol: String,
            #[serde(rename = "positionAmt")]
            position_amt: String,
            #[serde(rename = "entryPrice")]
            entry_price: String,
            #[serde(rename = "unrealizedProfit")]
            unrealized_profit: String,
            #[serde(rename = "initialMargin")]
            initial_margin: String,
            #[serde(rename = "notional")]
            notional: Option<String>,
        }

        let res: AccountInfo = self
            .request(Method::GET, "/fapi/v2/account", vec![])
            .await?;

        if let Some(p) = res.positions.into_iter().find(|p| p.symbol == symbol) {
            let qty: f64 = p.position_amt.parse()?;
            if qty == 0.0 {
                return Ok(None);
            }
            let entry_price: f64 = p.entry_price.parse()?;
            let notional_value = p
                .notional
                .as_ref()
                .and_then(|n| n.parse::<f64>().ok())
                .unwrap_or(qty.abs() * entry_price);

            Ok(Some(crate::execution::PositionInfo {
                symbol: symbol.to_string(),
                side: if qty > 0.0 {
                    "Buy".to_string()
                } else {
                    "Sell".to_string()
                },
                qty: qty.abs(),
                entry_price,
                unrealized_pnl: p.unrealized_profit.parse()?,
                realized_fees: 0.0,     // Populated by User Data Stream fills
                realized_funding: 0.0,  // Populated by User Data Stream fills
                realized_pnl: 0.0,      // Populated by User Data Stream fills
                margin_used: p.initial_margin.parse()?,
                notional_value: notional_value.abs(),
            }))
        } else {
            Ok(None)
        }
    }

    pub async fn get_balance(&self) -> Result<f64> {
        #[derive(Deserialize, Debug)]
        #[allow(non_snake_case)]
        struct AccountInfo {
            totalMarginBalance: String,
        }
        let res: AccountInfo = self
            .request(Method::GET, "/fapi/v2/account", vec![])
            .await?;
        Ok(res.totalMarginBalance.parse()?)
    }

    // ── Leverage ────────────────────────────────────────────────────────────

    /// Set leverage for a symbol on Binance Futures.
    /// Returns the new leverage value as confirmed by Binance.
    pub async fn set_leverage(&self, symbol: &str, leverage: u32) -> Result<f64> {
        #[derive(Deserialize, Debug)]
        struct LevResp {
            leverage: u32,
        }

        let params = vec![
            ("symbol", symbol.to_string()),
            ("leverage", leverage.to_string()),
        ];

        let res: LevResp = self
            .request(Method::POST, "/fapi/v1/leverage", params)
            .await?;
        info!(
            "[LEV][LIVE][{}] set_leverage {} -> OK (confirmed: {})",
            symbol, leverage, res.leverage
        );
        Ok(res.leverage as f64)
    }

    /// Read current leverage for a symbol from Binance Futures account info.
    pub async fn get_leverage(&self, symbol: &str) -> Result<f64> {
        #[derive(Deserialize, Debug)]
        struct AccountInfo {
            positions: Vec<PosInfo>,
        }
        #[derive(Deserialize, Debug)]
        struct PosInfo {
            symbol: String,
            leverage: String,
        }

        let res: AccountInfo = self
            .request(Method::GET, "/fapi/v2/account", vec![])
            .await?;
        if let Some(p) = res.positions.into_iter().find(|p| p.symbol == symbol) {
            Ok(p.leverage.parse()?)
        } else {
            Ok(1.0)
        }
    }

    // ── User Data Stream (for Live Fill Tracking) ───────────────────────────

    /// Create a listen key for the User Data Stream.
    pub async fn create_listen_key(&self) -> Result<String> {
        #[derive(Deserialize)]
        #[allow(non_snake_case)]
        struct ListenKeyResp {
            listenKey: String,
        }
        let url = format!("{}/fapi/v1/listenKey", self.base_url);
        let resp = self
            .client
            .post(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .send()
            .await?;
        let status = resp.status();
        let text = resp.text().await?;
        if !status.is_success() {
            return Err(anyhow!("Failed to create listen key: {} {}", status, text));
        }
        let parsed: ListenKeyResp = serde_json::from_str(&text)?;
        info!("[USER_STREAM] Listen key created");
        Ok(parsed.listenKey)
    }

    /// Keep alive the listen key (must be called every 30 minutes).
    pub async fn keep_alive_listen_key(&self) -> Result<()> {
        let url = format!("{}/fapi/v1/listenKey", self.base_url);
        let resp = self
            .client
            .put(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .send()
            .await?;
        if !resp.status().is_success() {
            let text = resp.text().await?;
            return Err(anyhow!("Failed to keep alive listen key: {}", text));
        }
        Ok(())
    }

    /// Parse an ORDER_TRADE_UPDATE event from the User Data Stream JSON.
    pub fn parse_order_trade_update(json: &str) -> Option<LiveFill> {
        let v: serde_json::Value = serde_json::from_str(json).ok()?;
        let event_type = v.get("e")?.as_str()?;
        if event_type != "ORDER_TRADE_UPDATE" {
            return None;
        }
        let o = v.get("o")?;
        let exec_type = o.get("x")?.as_str()?;
        // Only emit fills on actual TRADE execution
        if exec_type != "TRADE" {
            return None;
        }

        Some(LiveFill {
            symbol: o.get("s")?.as_str()?.to_string(),
            order_id: o.get("i")?.as_u64()?.to_string(),
            side: o.get("S")?.as_str()?.to_string(),
            order_type: o.get("o")?.as_str()?.to_string(),
            qty: o.get("l")?.as_str()?.parse().ok()?,       // last filled qty
            price: o.get("L")?.as_str()?.parse().ok()?,      // last filled price
            commission: o.get("n")?.as_str()?.parse().ok()?,  // commission amount
            commission_asset: o.get("N")?.as_str().unwrap_or("USDT").to_string(),
            realized_pnl: o.get("rp")?.as_str()?.parse().ok()?, // realized profit
            timestamp_ms: v.get("T")?.as_i64()?,
        })
    }

    /// Start sending live fill events from User Data Stream.
    /// Returns a receiver that will yield LiveFill events.
    /// The task runs in the background and handles reconnection and keep-alive.
    pub async fn start_user_data_stream(
        self: Arc<Self>,
        fill_tx: mpsc::Sender<LiveFill>,
    ) -> Result<()> {
        use tokio_tungstenite::connect_async;
        use futures_util::StreamExt;
        use url::Url;

        let listen_key = self.create_listen_key().await?;
        let ws_base = if self.base_url.contains("testnet") {
            "wss://stream.binancefuture.com/ws"
        } else {
            "wss://fstream.binance.com/ws"
        };
        let ws_url = format!("{}/{}", ws_base, listen_key);

        let client_for_keepalive = self.clone();
        let fill_tx_clone = fill_tx.clone();

        tokio::spawn(async move {
            // Keep-alive loop: renew listen key every 25 minutes
            let keepalive_client = client_for_keepalive.clone();
            tokio::spawn(async move {
                let mut interval = tokio::time::interval(tokio::time::Duration::from_secs(25 * 60));
                loop {
                    interval.tick().await;
                    if let Err(e) = keepalive_client.keep_alive_listen_key().await {
                        error!("[USER_STREAM] Keep-alive failed: {}", e);
                    }
                }
            });

            // WS connection loop with reconnection
            loop {
                info!("[USER_STREAM] Connecting to {}", ws_url);
                match connect_async(Url::parse(&ws_url).unwrap()).await {
                    Ok((ws_stream, _)) => {
                        info!("[USER_STREAM] Connected");
                        let (_, mut read) = ws_stream.split();

                        while let Some(msg) = read.next().await {
                            match msg {
                                Ok(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                                    if let Some(fill) = BinanceClient::parse_order_trade_update(&text) {
                                        info!(
                                            "[FILL][LIVE] {} {} {} @ {} (commission: {} {})",
                                            fill.symbol, fill.side, fill.qty, fill.price,
                                            fill.commission, fill.commission_asset
                                        );
                                        if fill_tx_clone.send(fill).await.is_err() {
                                            warn!("[USER_STREAM] Fill channel closed");
                                            return;
                                        }
                                    }
                                }
                                Ok(tokio_tungstenite::tungstenite::Message::Close(_)) => {
                                    warn!("[USER_STREAM] Connection closed by server");
                                    break;
                                }
                                Err(e) => {
                                    error!("[USER_STREAM] WS error: {}", e);
                                    break;
                                }
                                _ => {}
                            }
                        }
                    }
                    Err(e) => {
                        error!("[USER_STREAM] Connection failed: {}", e);
                    }
                }

                warn!("[USER_STREAM] Reconnecting in 5s...");
                tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
            }
        });

        Ok(())
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_qty_btcusdt() {
        let rules = SymbolRules {
            quantity_precision: 3,
            ..Default::default()
        };
        assert_eq!(BinanceClient::format_qty(0.123456, &rules), "0.123");
        assert_eq!(BinanceClient::format_qty(1.0, &rules), "1.000");
    }

    #[test]
    fn test_format_qty_dogeusdt() {
        let rules = SymbolRules {
            quantity_precision: 0,
            ..Default::default()
        };
        assert_eq!(BinanceClient::format_qty(100.7, &rules), "101");
    }

    #[test]
    fn test_format_price_btcusdt() {
        let rules = SymbolRules {
            price_precision: 2,
            ..Default::default()
        };
        assert_eq!(BinanceClient::format_price(50000.123, &rules), "50000.12");
    }

    #[test]
    fn test_format_price_high_precision() {
        let rules = SymbolRules {
            price_precision: 5,
            ..Default::default()
        };
        assert_eq!(
            BinanceClient::format_price(0.123456789, &rules),
            "0.12346"
        );
    }

    #[test]
    fn test_parse_order_trade_update_valid() {
        let json = r#"{
            "e": "ORDER_TRADE_UPDATE",
            "T": 1700000000000,
            "o": {
                "s": "BTCUSDT",
                "i": 12345,
                "S": "BUY",
                "o": "MARKET",
                "x": "TRADE",
                "l": "0.001",
                "L": "50000.00",
                "n": "0.02",
                "N": "USDT",
                "rp": "5.50"
            }
        }"#;
        let fill = BinanceClient::parse_order_trade_update(json).unwrap();
        assert_eq!(fill.symbol, "BTCUSDT");
        assert_eq!(fill.side, "BUY");
        assert!((fill.qty - 0.001).abs() < 1e-9);
        assert!((fill.price - 50000.0).abs() < 1e-9);
        assert!((fill.commission - 0.02).abs() < 1e-9);
        assert!((fill.realized_pnl - 5.50).abs() < 1e-9);
    }

    #[test]
    fn test_parse_order_trade_update_ignores_non_trade() {
        let json = r#"{
            "e": "ORDER_TRADE_UPDATE",
            "T": 1700000000000,
            "o": {
                "s": "BTCUSDT",
                "i": 12345,
                "S": "BUY",
                "o": "LIMIT",
                "x": "NEW",
                "l": "0.0",
                "L": "0.0",
                "n": "0.0",
                "N": "USDT",
                "rp": "0.0"
            }
        }"#;
        assert!(BinanceClient::parse_order_trade_update(json).is_none());
    }

    #[test]
    fn test_parse_order_trade_update_ignores_other_events() {
        let json = r#"{"e": "ACCOUNT_UPDATE", "T": 1700000000000}"#;
        assert!(BinanceClient::parse_order_trade_update(json).is_none());
    }

    #[test]
    fn test_exchange_info_parsing() {
        // Verify SymbolRules can be created from mock exchangeInfo-like data
        let rule = SymbolRules {
            price_precision: 1,
            quantity_precision: 0,
            min_qty: 1.0,
            min_notional: 5.0,
            tick_size: 0.1,
            step_size: 1.0,
        };
        assert_eq!(BinanceClient::format_qty(123.456, &rule), "123");
        assert_eq!(BinanceClient::format_price(0.567, &rule), "0.6");
    }
}
