use parquet::file::reader::{FileReader, SerializedFileReader};
use std::fs::File;
use std::path::Path;

fn main() {
    let path = Path::new(r"C:\Bot mk3\runs\20260317_2352_BTCUSDT\datasets\golden_l2_v1_train\normalized_events.parquet");
    if !path.exists() {
        println!("File not found: {:?}", path);
        return;
    }
    
    let file = File::open(path).unwrap();
    let reader = SerializedFileReader::new(file).unwrap();
    let schema = reader.metadata().file_metadata().schema();
    
    println!("--- PARQUET SCHEMA ---");
    for field in schema.get_fields() {
        println!("COLUMN: {}", field.name());
    }
    println!("--- END SCHEMA ---");
}
