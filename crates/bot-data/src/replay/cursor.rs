use std::cmp::Ordering;
use crate::replay::events::{ReplayEvent, StreamPriority};
use crate::replay::types::ClockMode;

/// Represents a single row from a Parquet file, wrapped with metadata for strict ordering.
#[derive(Debug, Clone)]
pub struct ReplayRow {
    pub event: ReplayEvent,
    pub clock_mode: ClockMode,
    pub stream_priority: StreamPriority,
}

impl ReplayRow {
    pub fn new(event: ReplayEvent, clock_mode: ClockMode) -> Self {
        let stream_priority = StreamPriority::from_stream_name(&event.stream_name);
        Self {
            event,
            clock_mode,
            stream_priority,
        }
    }

    /// Primary timestamp based on ClockMode
    fn primary_ts(&self) -> i64 {
        match self.clock_mode {
            ClockMode::Exchange => self.event.ts_exchange,
            ClockMode::Local => self.event.ts_local,
            ClockMode::Canonical => self.event.ts_canonical,
        }
    }

    /// Secondary timestamp logic handling 0/null values
    /// "secondary_ts (si clock != exchange, usar exchange; si clock != local, usar local)"
    fn secondary_ts(&self) -> i64 {
        match self.clock_mode {
            ClockMode::Exchange => {
                // If primary is exchange, secondary is local
                // If local is 0, fall back to something else? No, use local as is.
                self.event.ts_local
            },
            ClockMode::Local => {
                // If primary is local, secondary is exchange
                self.event.ts_exchange
            },
            ClockMode::Canonical => {
                // Logic not explicitly defined in prompt for Canonical secondary.
                // Assuming exchange logic as default fallback for canonical.
                // Or maybe prompt says: "si clock != exchange, usar exchange" -> implies canonical != exchange, so use exchange.
                self.event.ts_exchange
            }
        }
    }
}

impl PartialEq for ReplayRow {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}

impl Eq for ReplayRow {}

impl PartialOrd for ReplayRow {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for ReplayRow {
    /// Strict Total Ordering:
    /// a) clock_ts (según ClockMode)
    /// b) secondary_ts (si clock != exchange, usar exchange; si clock != local, usar local)
    /// c) stream_priority fijo:
    ///    depth > bookTicker > aggTrade > trade > markPrice > funding > liquidation > openInterest
    /// d) sequence_id si existe (si no, 0)
    /// e) file_part_index
    /// f) row_index
    fn cmp(&self, other: &Self) -> Ordering {
        // a) clock_ts
        let ts_a = self.primary_ts();
        let ts_b = other.primary_ts();
        match ts_a.cmp(&ts_b) {
            Ordering::Equal => {},
            ord => return ord,
        }

        // b) secondary_ts
        let sec_ts_a = self.secondary_ts();
        let sec_ts_b = other.secondary_ts();
        match sec_ts_a.cmp(&sec_ts_b) {
             Ordering::Equal => {},
             ord => return ord,
        }

        // c) stream_priority
        match self.stream_priority.cmp(&other.stream_priority) {
            Ordering::Equal => {},
            ord => return ord,
        }

        // d) sequence_id
        match self.event.sequence_id.cmp(&other.event.sequence_id) {
            Ordering::Equal => {},
            ord => return ord,
        }

        // e) file_part_index
        match self.event.file_part.cmp(&other.event.file_part) {
            Ordering::Equal => {},
            ord => return ord,
        }

        // f) row_index
        self.event.row_index.cmp(&other.event.row_index)
    }
}
