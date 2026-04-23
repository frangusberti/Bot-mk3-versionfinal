use bot_core::proto::{Action, ActionType};
use log::{error, warn};
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Serialize)]
pub struct RiskInfo {
    pub max_pos_frac: f64,
    pub effective_leverage: f64,
}

#[derive(Serialize)]
pub struct PortfolioInfo {
    pub is_long: f64,
    pub is_short: f64,
    pub is_flat: f64,
    pub position_frac: f64,
    pub upnl_frac: f64,
    pub leverage_used: f64,
    pub equity: f64,
    pub cash: f64,
}

#[derive(Serialize)]
pub struct HttpInferRequest {
    pub symbol: String,
    pub ts_ms: i64,
    pub mode: String,
    pub decision_interval_ms: i32,
    pub obs: Vec<f32>,
    pub risk: RiskInfo,
    pub portfolio: PortfolioInfo,
    pub meta: std::collections::HashMap<String, String>,
}

#[derive(Deserialize)]
pub struct HttpInferResponse {
    pub action: String,
    pub confidence: f64,
    pub log_prob: f32,
    pub value: f32,
}

#[derive(Deserialize, Debug)]
pub struct ProfileResponse {
    pub schema_version: u16,
    pub obs_dim: usize,
    #[serde(rename = "policy_type", default)]
    pub _policy_type: String,
    #[serde(rename = "model_path", default)]
    pub _model_path: Option<String>,
}

pub struct ActionInfo {
    pub action: Action,
    pub confidence: f64,
    pub log_prob: f32,
    pub value: f32,
}

pub struct PythonPolicyAdapter {
    client: reqwest::Client,
    url: String,
    _run_id: String,
    _policy_id: String,
}

impl PythonPolicyAdapter {
    pub async fn new(url: String, run_id: String, policy_id: String) -> Result<Self, String> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_millis(300))
            .build()
            .map_err(|e| e.to_string())?;

        // The URL passed might be localhost:50055, we need the full path
        let formatted_url = if url.starts_with("http") {
            if url.contains("/infer") {
                url
            } else {
                format!("{}/infer", url.trim_end_matches('/'))
            }
        } else {
            format!("http://{}/infer", url.trim_end_matches('/'))
        };

        Ok(Self {
            client,
            url: formatted_url,
            _run_id: run_id,
            _policy_id: policy_id,
        })
    }

    pub async fn get_profile(&self) -> Result<ProfileResponse, String> {
        let profile_url = self.url.replace("/infer", "/profile");
        let response = match self.client.get(&profile_url).send().await {
            Ok(resp) => resp,
            Err(e) => {
                return Err(format!(
                    "Policy Server unreachable at {}: {}",
                    profile_url, e
                ))
            }
        };

        if !response.status().is_success() {
            return Err(format!(
                "Policy Server profile error: {}",
                response.status()
            ));
        }

        let body: ProfileResponse = response.json().await.map_err(|e| e.to_string())?;
        Ok(body)
    }

    pub async fn infer_action(&mut self, req_data: HttpInferRequest) -> Result<ActionInfo, String> {
        let symbol = req_data.symbol.clone();

        let response = match self.client.post(&self.url).json(&req_data).send().await {
            Ok(resp) => resp,
            Err(e) => {
                warn!(
                    "Policy Server unreachable at {} for {}: {}. Falling back to HOLD.",
                    self.url, symbol, e
                );
                return Ok(ActionInfo {
                    action: Action {
                        r#type: ActionType::Hold as i32,
                    },
                    confidence: 0.0,
                    log_prob: 0.0,
                    value: 0.0,
                });
            }
        };

        if !response.status().is_success() {
            let status = response.status();
            error!(
                "Policy Server error for {}: {}. Falling back to HOLD.",
                symbol, status
            );
            return Ok(ActionInfo {
                action: Action {
                    r#type: ActionType::Hold as i32,
                },
                confidence: 0.0,
                log_prob: 0.0,
                value: 0.0,
            });
        }

        let body: HttpInferResponse = response.json().await.map_err(|e| e.to_string())?;

        let action_type = match body.action.as_str() {
            "HOLD" => ActionType::Hold,
            "OPEN_LONG" => ActionType::OpenLong,
            "ADD_LONG" => ActionType::AddLong,
            "REDUCE_LONG" => ActionType::ReduceLong,
            "CLOSE_LONG" => ActionType::CloseLong,
            "OPEN_SHORT" => ActionType::OpenShort,
            "ADD_SHORT" => ActionType::AddShort,
            "REDUCE_SHORT" => ActionType::ReduceShort,
            "CLOSE_SHORT" => ActionType::CloseShort,
            "REPRICE" => ActionType::Reprice,
            // Legacy fallbacks
            "POST_BID" => ActionType::OpenLong,
            "POST_ASK" => ActionType::OpenShort,
            "CLOSE_POSITION" => ActionType::CloseLong, // Ambiguous, but safe default
            _ => {
                warn!(
                    "Unknown action '{}' from policy server. Falling back to HOLD.",
                    body.action
                );
                ActionType::Hold
            }
        };

        Ok(ActionInfo {
            action: Action {
                r#type: action_type as i32,
            },
            confidence: body.confidence,
            log_prob: body.log_prob,
            value: body.value,
        })
    }

    #[allow(dead_code)]
    pub async fn infer_http(&mut self, req_data: HttpInferRequest) -> Result<Action, String> {
        self.infer_action(req_data).await.map(|info| info.action)
    }

    pub async fn reload(&self, model_path: String) -> Result<(), String> {
        // Construct URL for reload (swap /infer for /reload)
        let reload_url = self.url.replace("/infer", "/reload");

        let payload = serde_json::json!({
            "model_path": model_path
        });

        let resp = self
            .client
            .post(&reload_url)
            .json(&payload)
            .send()
            .await
            .map_err(|e| format!("Failed to call reload: {}", e))?;

        if !resp.status().is_success() {
            return Err(format!("Policy Server failed to reload: {}", resp.status()));
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn infer_action_falls_back_to_hold_on_unreachable_server() {
        let mut adapter = PythonPolicyAdapter::new(
            "127.0.0.1:9".to_string(),
            "test_run".to_string(),
            "test_policy".to_string(),
        )
        .await
        .expect("adapter init");

        let req = HttpInferRequest {
            symbol: "BTCUSDT".to_string(),
            ts_ms: 0,
            mode: "PAPER".to_string(),
            decision_interval_ms: 1000,
            obs: vec![0.0; 8],
            risk: RiskInfo {
                max_pos_frac: 0.5,
                effective_leverage: 5.0,
            },
            portfolio: PortfolioInfo {
                is_long: 0.0,
                is_short: 0.0,
                is_flat: 1.0,
                position_frac: 0.0,
                upnl_frac: 0.0,
                leverage_used: 0.0,
                equity: 1_000.0,
                cash: 1_000.0,
            },
            meta: std::collections::HashMap::new(),
        };

        let out = adapter.infer_action(req).await.expect("fallback response");
        assert_eq!(out.action.r#type, ActionType::Hold as i32);
        assert_eq!(out.confidence, 0.0);
    }
}
