fn main() -> Result<(), Box<dyn std::error::Error>> {
    unsafe { std::env::set_var("PROTOC", protoc_bin_vendored::protoc_bin_path().unwrap()); }
    let proto_file = "../../proto/bot.proto";
    
    // Check if proto file exists
    if std::path::Path::new(proto_file).exists() {
        tonic_build::configure()
            .build_server(true)
            .build_client(true)
            .compile(&[proto_file], &["../../proto"])?;
    } else {
        println!("cargo:warning=Proto file not found at {}", proto_file);
    }
    
    println!("cargo:rerun-if-changed=../../proto/bot.proto");
    Ok(())
}
