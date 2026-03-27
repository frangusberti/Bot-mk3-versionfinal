pub mod schema;
pub mod health;
pub mod manifest;
pub mod proto {
    tonic::include_proto!("bot");
}
