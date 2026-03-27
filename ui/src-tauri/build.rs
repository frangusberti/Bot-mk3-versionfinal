fn main() {
    std::env::set_var("PROTOC", protoc_bin_vendored::protoc_bin_path().unwrap());
    tonic_build::configure()
        .build_server(false) // Solo necesitamos el cliente
        .compile(
            &["../../proto/bot.proto"],
            &["../../proto"],
        )
        .expect("Failed to compile protos");

    tauri_build::build();
}
