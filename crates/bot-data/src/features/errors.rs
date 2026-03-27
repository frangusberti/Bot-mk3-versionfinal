use thiserror::Error;

#[derive(Error, Debug)]
pub enum FeatureError {
    #[error("Missing required stream: {0}")]
    MissingStream(String),

    #[error("Invalid configuration: {0}")]
    InvalidConfig(String),

    #[error("Calculation error: {0}")]
    CalculationError(String),
    
    #[error("Engine not initialized")]
    NotInitialized,
    
    #[error("Empty dataset")]
    EmptyDataset,

    #[error("Profile mismatch: {0}")]
    ProfileMismatch(String),

    #[error("Version mismatch: {0}")]
    VersionMismatch(String),
}
