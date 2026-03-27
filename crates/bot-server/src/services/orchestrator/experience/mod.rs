use tokio::sync::mpsc;
use std::collections::HashMap;
use std::path::PathBuf;
use log::info;
use bot_data::experience::schema::ExperienceRow;
use bot_data::experience::writer::ExperienceWriter;

pub mod builder;

pub enum ExperienceCommand {
    Record(Box<ExperienceRow>),
    #[allow(dead_code)]
    Flush,
    #[allow(dead_code)]
    Rotate,
}

pub struct ExperienceService {
    rx: mpsc::Receiver<ExperienceCommand>,
    // Map<Symbol, Writer>
    writers: HashMap<String, ExperienceWriter>,
    base_dir: PathBuf,
}

impl ExperienceService {
    pub fn new(rx: mpsc::Receiver<ExperienceCommand>, base_dir: PathBuf) -> Self {
        Self {
            rx,
            writers: HashMap::new(),
            base_dir,
        }
    }

    pub fn start(mut self) {
        std::thread::spawn(move || {
            info!("ExperienceService started.");
            
            // Blocking loop since Parquet writes are blocking
            while let Some(cmd) = self.rx.blocking_recv() {
                match cmd {
                    ExperienceCommand::Record(row) => {
                        let symbol = row.symbol.clone();
                        let writer = self.writers.entry(symbol.clone()).or_insert_with(|| {
                            let path = self.base_dir.join(&symbol);
                            ExperienceWriter::new(path)
                        });
                        writer.write(*row);
                    }
                    ExperienceCommand::Flush => {
                        for writer in self.writers.values_mut() {
                            writer.flush_buffer();
                        }
                    }
                    ExperienceCommand::Rotate => {
                        // Force rotation if needed
                        for writer in self.writers.values_mut() {
                            // writer.rotate_file(); // Make public?
                            // For now, flush is enough, wrapper handles rotation logic on write
                            writer.flush_buffer(); 
                        }
                    }
                }
            }
            // Cleanup
            for (_, mut writer) in self.writers.drain() {
                writer.close();
            }
            info!("ExperienceService stopped.");
        });
    }
}
