use serde::{Serialize, Deserialize};
use arrow::array::{ArrayRef, Float32Array, Int32Array, Int64Array, StringArray, BooleanArray, Float64Array};
use arrow::record_batch::RecordBatch;
use std::sync::Arc;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExperienceRow {
    pub episode_id: String,
    pub symbol: String,
    pub decision_ts: i64,
    pub step_index: i32,
    pub obs: Vec<f32>, // Flat vector, will be expanded to cols or list
    pub action: i32,
    pub reward: f32,
    pub equity_before: f64,
    pub equity_after: f64,
    pub pos_qty_before: f64,
    pub pos_side_before: String, // "Long", "Short", "Flat"
    pub fees_step: f64,
    pub done: bool,
    pub done_reason: String,
    pub info_json: String,
    pub log_prob: f32,
    pub value_estimate: f32,
}

impl ExperienceRow {
    pub fn to_record_batch(rows: &[ExperienceRow]) -> Result<RecordBatch, arrow::error::ArrowError> {
        let n = rows.len();
        
        let episode_ids: Vec<String> = rows.iter().map(|r| r.episode_id.clone()).collect();
        let symbols: Vec<String> = rows.iter().map(|r| r.symbol.clone()).collect();
        let decision_tss: Vec<i64> = rows.iter().map(|r| r.decision_ts).collect();
        let step_indices: Vec<i32> = rows.iter().map(|r| r.step_index).collect();
        let actions: Vec<i32> = rows.iter().map(|r| r.action).collect();
        let rewards: Vec<f32> = rows.iter().map(|r| r.reward).collect();
        let equity_befores: Vec<f64> = rows.iter().map(|r| r.equity_before).collect();
        let equity_afters: Vec<f64> = rows.iter().map(|r| r.equity_after).collect();
        let pos_qtys: Vec<f64> = rows.iter().map(|r| r.pos_qty_before).collect();
        let pos_sides: Vec<String> = rows.iter().map(|r| r.pos_side_before.clone()).collect();
        let fees: Vec<f64> = rows.iter().map(|r| r.fees_step).collect();
        let dones: Vec<bool> = rows.iter().map(|r| r.done).collect();
        let reasons: Vec<String> = rows.iter().map(|r| r.done_reason.clone()).collect();
        let infos: Vec<String> = rows.iter().map(|r| r.info_json.clone()).collect();
        let log_probs: Vec<f32> = rows.iter().map(|r| r.log_prob).collect();
        let value_estimates: Vec<f32> = rows.iter().map(|r| r.value_estimate).collect();

        // Observation columns (obs_0 .. obs_11)
        // Assuming fixed dimension 12. If dynamic, we need ListArray, but user preferred columns.
        let obs_dim = 12; 
        let mut obs_cols: Vec<Vec<f32>> = (0..obs_dim).map(|_| Vec::with_capacity(n)).collect::<Vec<_>>();
        
        for row in rows {
            for (i, col) in obs_cols.iter_mut().enumerate().take(obs_dim) {
                let val = if i < row.obs.len() { row.obs[i] } else { 0.0 };
                col.push(val);
            }
        }

        let mut columns: Vec<ArrayRef> = vec![
            Arc::new(StringArray::from(episode_ids)),
            Arc::new(StringArray::from(symbols)),
            Arc::new(Int64Array::from(decision_tss)),
            Arc::new(Int32Array::from(step_indices)),
        ];
        
        // Add obs columns
        for col in obs_cols.iter().take(obs_dim) {
            columns.push(Arc::new(Float32Array::from(col.clone())));
        }
        
        let extra_cols: Vec<ArrayRef> = vec![
            Arc::new(Int32Array::from(actions)),
            Arc::new(Float32Array::from(rewards)),
            Arc::new(Float64Array::from(equity_befores)),
            Arc::new(Float64Array::from(equity_afters)),
            Arc::new(Float64Array::from(pos_qtys)),
            Arc::new(StringArray::from(pos_sides)),
            Arc::new(Float64Array::from(fees)),
            Arc::new(BooleanArray::from(dones)),
            Arc::new(StringArray::from(reasons)),
            Arc::new(StringArray::from(infos)),
            Arc::new(Float32Array::from(log_probs)),
            Arc::new(Float32Array::from(value_estimates)),
        ];
        columns.extend(extra_cols);

        // Define Schema
        let mut fields = vec![
            arrow::datatypes::Field::new("episode_id", arrow::datatypes::DataType::Utf8, false),
            arrow::datatypes::Field::new("symbol", arrow::datatypes::DataType::Utf8, false),
            arrow::datatypes::Field::new("decision_ts", arrow::datatypes::DataType::Int64, false),
            arrow::datatypes::Field::new("step_index", arrow::datatypes::DataType::Int32, false),
        ];
        
        for i in 0..obs_dim {
            fields.push(arrow::datatypes::Field::new(format!("obs_{}", i), arrow::datatypes::DataType::Float32, false));
        }
        
        fields.extend(vec![
            arrow::datatypes::Field::new("action", arrow::datatypes::DataType::Int32, false),
            arrow::datatypes::Field::new("reward", arrow::datatypes::DataType::Float32, false),
            arrow::datatypes::Field::new("equity_before", arrow::datatypes::DataType::Float64, false),
            arrow::datatypes::Field::new("equity_after", arrow::datatypes::DataType::Float64, false),
            arrow::datatypes::Field::new("pos_qty_before", arrow::datatypes::DataType::Float64, false),
            arrow::datatypes::Field::new("pos_side_before", arrow::datatypes::DataType::Utf8, false),
            arrow::datatypes::Field::new("fees_step", arrow::datatypes::DataType::Float64, false),
            arrow::datatypes::Field::new("done", arrow::datatypes::DataType::Boolean, false),
            arrow::datatypes::Field::new("done_reason", arrow::datatypes::DataType::Utf8, false),
            arrow::datatypes::Field::new("info_json", arrow::datatypes::DataType::Utf8, false),
            arrow::datatypes::Field::new("log_prob", arrow::datatypes::DataType::Float32, false),
            arrow::datatypes::Field::new("value_estimate", arrow::datatypes::DataType::Float32, false),
        ]);

        let schema = Arc::new(arrow::datatypes::Schema::new(fields));
        
        RecordBatch::try_new(schema, columns)
    }
}
