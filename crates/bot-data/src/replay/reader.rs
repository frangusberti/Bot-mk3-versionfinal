use std::fs::File;
use std::path::PathBuf;
use parquet::file::reader::{FileReader, SerializedFileReader};
use parquet::record::{Row, Field};
use log::{info, error, warn};
use crate::replay::events::ReplayEvent;
use crate::replay::cursor::ReplayRow;
use crate::replay::types::ClockMode;

pub struct BatchedReplayReader {
    reader: SerializedFileReader<File>,
    current_row_group_idx: usize,
    buffer: std::collections::VecDeque<Row>,
    file_part_index: u32,
    clock_mode: ClockMode,
    global_row_idx: u32,
    debug_include_raw: bool,
}

impl BatchedReplayReader {
    pub fn new(path: PathBuf, part_index: u32, clock_mode: ClockMode, include_raw: bool) -> anyhow::Result<Self> {
        println!("READER_INIT: Opening file {:?}", path);
        let file = File::open(&path)?;
        let reader = SerializedFileReader::new(file)?;
        println!("READER_INIT: Row groups = {}", reader.metadata().num_row_groups());
        
        Ok(Self {
            reader,
            current_row_group_idx: 0,
            buffer: std::collections::VecDeque::new(),
            file_part_index: part_index,
            clock_mode,
            global_row_idx: 0,
            debug_include_raw: include_raw,
        })
    }
}

impl Iterator for BatchedReplayReader {
    type Item = ReplayRow;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            // 1. Serve from buffer
            if let Some(row) = self.buffer.pop_front() {
                 if let Some(mut event) = Self::row_to_event(row, self.debug_include_raw) {
                    event.file_part = self.file_part_index;
                    event.row_index = self.global_row_idx;
                    
                    let replay_row = ReplayRow::new(event, self.clock_mode);
                    self.global_row_idx += 1;
                    return Some(replay_row);
                }
                continue;
            }

            // 2. Refill buffer if empty
            if self.current_row_group_idx < self.reader.num_row_groups() {
                 match self.reader.get_row_group(self.current_row_group_idx) {
                    Ok(row_group_reader) => {
                         let num_rows = row_group_reader.metadata().num_rows();
                         info!("BatchedReplayReader: Loading Row Group {} ({} rows)", self.current_row_group_idx, num_rows);
                         
                        match row_group_reader.get_row_iter(None) {
                            Ok(rows) => {
                                match rows.collect::<Result<Vec<Row>, _>>() {
                                    Ok(row_vec) => {
                                        self.buffer.extend(row_vec);
                                        self.current_row_group_idx += 1;
                                        continue;
                                    },
                                    Err(e) => {
                                        error!("BatchedReplayReader: Failed to collect rows: {}", e);
                                        return None;
                                    }
                                }
                            },
                            Err(e) => {
                                error!("BatchedReplayReader: Failed to get row iter: {}", e);
                                return None;
                            }
                        }
                    },
                    Err(e) => {
                        error!("BatchedReplayReader: Failed to get row group {}: {}", self.current_row_group_idx, e);
                        return None;
                    }
                }
            } else {
                return None; // EOF
            }
        }
    }
}

impl BatchedReplayReader {
    fn row_to_event(row: Row, include_raw: bool) -> Option<ReplayEvent> {
        let mut time_exchange = 0;
        let mut time_local = 0;
        let mut time_canonical = 0;
        let mut symbol = String::new();
        let mut event_type = String::new();
        let mut stream_name = String::new();
        
        let mut price = None;
        let mut quantity = None;
        let mut side = None;
        let mut best_bid = None;
        let mut best_ask = None;
        
        let mut mark_price = None;
        let mut funding_rate = None;
        let mut liquidation_price = None;
        let mut liquidation_qty = None;
        let mut open_interest = None;
        let mut open_interest_value = None;
        
        let mut payload_json = None;
        let mut sequence_id = 0;

        for (name, field) in row.get_column_iter() {
             match name.as_str() {
                 "time_exchange" | "exchange_timestamp" => {
                    match field {
                        Field::Long(v) => time_exchange = *v,
                        Field::Double(v) => time_exchange = *v as i64,
                        _ => {}
                    }
                 },
                 "time_local" | "local_timestamp" => {
                    match field {
                        Field::Long(v) => time_local = *v,
                        Field::Double(v) => time_local = *v as i64,
                        _ => {}
                    }
                 },
                 "time_canonical" | "ts_canonical" => {
                    match field {
                        Field::Long(v) => time_canonical = *v,
                        Field::Double(v) => time_canonical = *v as i64,
                        _ => {}
                    }
                 },
                "symbol" => if let Field::Str(v) = field { symbol = v.clone(); },
                "event_type" => if let Field::Str(v) = field { event_type = v.clone(); },
                "stream_name" | "stream" => if let Field::Str(v) = field { stream_name = v.clone(); },
                
                "price" => if let Field::Double(v) = field { price = Some(*v); },
                "qty" | "quantity" => if let Field::Double(v) = field { quantity = Some(*v); },
                "side" | "is_buyer_maker" => {
                     match field {
                         Field::Str(v) => side = Some(v.clone()),
                         Field::Bool(v) => side = Some(if *v { "SELL".to_string() } else { "BUY".to_string() }), // if buyer_maker=true then taker=SELL
                         _ => {}
                     }
                },
                "best_bid" | "bid_price" => if let Field::Double(v) = field { best_bid = Some(*v); },
                "best_ask" | "ask_price" => if let Field::Double(v) = field { best_ask = Some(*v); },
                
                "mark_price" => if let Field::Double(v) = field { mark_price = Some(*v); },
                "funding_rate" => if let Field::Double(v) = field { funding_rate = Some(*v); },
                "liquidation_price" => if let Field::Double(v) = field { liquidation_price = Some(*v); },
                "liquidation_qty" => if let Field::Double(v) = field { liquidation_qty = Some(*v); },
                "open_interest" => if let Field::Double(v) = field { open_interest = Some(*v); },
                "open_interest_value" => if let Field::Double(v) = field { open_interest_value = Some(*v); },
                
                "sequence_id" | "last_update_id" => {
                    match field {
                        Field::Long(v) => sequence_id = *v,
                        Field::Double(v) => sequence_id = *v as i64,
                        _ => {}
                    }
                 },

                "payload_json" | "payload" => {
                    if include_raw {
                        if let Field::Str(v) = field { payload_json = Some(v.clone()); }
                    }
                },
                 _ => {}
            }
        }

        // --- CANONICAL TIME FALLBACK ---
        // If time_canonical is not provided in parquet, fallback to local_timestamp
        if time_canonical == 0 {
            time_canonical = time_local;
        }
        // If even local is 0, fallback to exchange
        if time_canonical == 0 {
            time_canonical = time_exchange;
        }

        Some(ReplayEvent {
            ts_exchange: time_exchange,
            ts_local: time_local,
            ts_canonical: time_canonical,
            symbol,
            event_type,
            stream_name,
            price,
            quantity,
            side,
            best_bid,
            best_ask,
            mark_price,
            funding_rate,
            liquidation_price,
            liquidation_qty,
            open_interest,
            open_interest_value,
            payload_json,
            sequence_id,
            file_part: 0, // set by reader
            row_index: 0, // set by reader
        })
    }
}
