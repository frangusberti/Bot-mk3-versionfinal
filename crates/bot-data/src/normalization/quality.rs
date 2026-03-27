use super::manifest::{QualityReport, StreamQuality};
use super::schema::NormalizedMarketEvent;
use std::collections::HashMap;

pub struct QualityAnalyzer;

impl QualityAnalyzer {
    pub fn analyze(events: &[NormalizedMarketEvent]) -> QualityReport {
        let mut streams_map: HashMap<String, Vec<&NormalizedMarketEvent>> = HashMap::new();
        
        for event in events {
            streams_map.entry(event.stream_name.clone())
                .or_default()
                .push(event);
        }

        let mut stream_qualities = HashMap::new();
        let mut total_gaps = 0;
        let min_coverage = 100.0; // Todo: Calculate against time range
        
        // Expected streams (hardcoded for now, ideally config)
        let expected_streams = vec!["aggTrade", "depthUpdate", "bookTicker"];
        let mut missing_streams = Vec::new();

        for expected in &expected_streams {
            if !streams_map.contains_key(*expected) {
                missing_streams.push(expected.to_string());
            }
        }

        for (stream_name, stream_events) in &streams_map {
            // Calculate Lag & Drift
            let mut lags: Vec<f64> = Vec::new();
            let mut drifts: Vec<f64> = Vec::new();
            
            for e in stream_events {
                let lag = (e.time_local - e.time_exchange) as f64;
                lags.push(lag);
                drifts.push(lag); // Simplified drift
            }

            lags.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let p99_lag = if !lags.is_empty() {
                lags[(lags.len() as f64 * 0.99) as usize]
            } else { 0.0 };

            let drift_ms_avg = if !drifts.is_empty() {
                drifts.iter().sum::<f64>() / drifts.len() as f64
            } else { 0.0 };

            // Calculate Gaps (Simplified)
            let gap_count = 0; 
            total_gaps += gap_count;

            // Coverage
            let coverage = 100.0; 

            stream_qualities.insert(stream_name.to_string(), StreamQuality {
                coverage_pct: coverage,
                lag_p99_ms: p99_lag,
                events_per_sec: 0.0,
                gap_count,
                drift_ms_avg,
                status: "OK".to_string(),
            });
        }

        let mut overall_status = "OK".to_string();
        let mut usable_for_training = true;
        let mut usable_for_backtest = true;

        // Check missing streams
        let critical_streams = vec!["depthUpdate"];
        let required_streams = vec!["aggTrade", "bookTicker"];
        let mut missing_critical = false;
        let mut missing_required = false;

        // 1. Check Critical Streams (FAIL if missing)
        for expected in &critical_streams {
            if !streams_map.contains_key(*expected) {
                missing_streams.push(expected.to_string());
                missing_critical = true;
            }
        }

        // 2. Check Required Streams (WARN if missing)
        for expected in &required_streams {
            if !streams_map.contains_key(*expected) {
                missing_streams.push(expected.to_string());
                missing_required = true;
            }
        }
        
        // Check OpenInterest (INFO/WARN but not critical for integrity)
        if !streams_map.contains_key("openInterest") {
             // Just note it, maybe add to missing but don't fail
             missing_streams.push("openInterest".to_string());
        }

        if missing_critical {
            overall_status = "FAIL".to_string();
            usable_for_training = false;
            usable_for_backtest = false;
        } else if missing_required {
            overall_status = "WARN".to_string();
            // Still usable for simple backtests, maybe not training if feature engineering needs them
            // For now, let's say WARN means careful
        }

        for _events in streams_map.values() {
             // ... existing metrics calc ...
             // TODO: Real gap detection requires parsing payload for IDs. 
             // For now we trust the Recorder's "OrderBook" didn't crash.
             // In future, Parse payload JSON and check u_prev == u_curr-1
             let _ = _events; // Suppress unused warning for now
        }

        let symbol = if !events.is_empty() {
             events[0].symbol.clone()
        } else {
             "unknown".to_string()
        };
        
        let start_ts = if !events.is_empty() { events[0].time_exchange } else { 0 };
        let end_ts = if !events.is_empty() { events.last().unwrap().time_exchange } else { 0 };

        QualityReport {
            symbol,
            start_ts,
            end_ts,
            overall_status,
            coverage_pct: min_coverage,
            total_gaps,
            missing_streams,
            usable_for_training,
            usable_for_backtest,
            streams: stream_qualities,
        }
    }
}
