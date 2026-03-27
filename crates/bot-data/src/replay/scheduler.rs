use tokio::time::{sleep, Duration, Instant};
use crate::replay::types::{ReplayConfig, ReplayEvent};
use log::debug;

pub struct ReplayScheduler {
    config: ReplayConfig,
    last_emit_ts: Option<i64>,  // Timestamp of last emitted event (in replay time)
    last_emit_realtime: Option<Instant>, // Realtime when last event was emitted
    is_paused: bool,
}

impl ReplayScheduler {
    pub fn new(config: ReplayConfig) -> Self {
        Self {
            config,
            last_emit_ts: None,
            last_emit_realtime: None,
            is_paused: false,
        }
    }

    pub fn update_config(&mut self, config: ReplayConfig) {
        self.config = config;
    }

    pub fn set_paused(&mut self, paused: bool) {
        self.is_paused = paused;
        if !paused {
            // Reset realtime anchor on resume to avoid jumping
            self.last_emit_realtime = Some(Instant::now());
        }
    }

    pub async fn wait_for_event(&mut self, event: &ReplayEvent) {
        if self.is_paused {
            // Simple spin-wait or better: return early and let caller handle pause loop?
            // For now, let's assume the caller (ReplayEngine) checks pause state before calling this.
            // If we are here, we are running.
            // But if we pause *during* a sleep, we should handle it.
            // The engine loop is better suited to handle pause.
            return;
        }

        // 1. Get current event timestamp based on clock mode
        let current_ts = event.sort_key.0; // Primary timestamp

        // 2. Logic for FAST mode (Speed <= 0 or very high?)
        // Requirement says: "as_fast_as_possible (without sleeps)"
        // Typically handled by a specific speed flag or speed >= 1000.0
        if self.config.speed >= 100.0 {
            self.last_emit_ts = Some(current_ts);
            return;
        }

        if let Some(last_ts) = self.last_emit_ts {
            if current_ts > last_ts {
                let delta_ms = (current_ts - last_ts) as f64;
                
                // Calculate sleep duration
                // delay = delta / speed
                let delay_ms = delta_ms / self.config.speed;
                
                if delay_ms > 1.0 { // Sleep only if significant
                    sleep(Duration::from_millis(delay_ms as u64)).await;
                }
            }
        }
        
        self.last_emit_ts = Some(current_ts);
        self.last_emit_realtime = Some(Instant::now());
    }
}
