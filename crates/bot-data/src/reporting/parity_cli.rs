use bot_data::reporting::parity::ReplayRecompute;
use std::env;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("Usage: parity_tool <run_id> <symbol>");
        std::process::exit(1);
    }

    let run_id = &args[1];
    let symbol = &args[2];

    println!("Starting offline parity generation for Run: {}, Symbol: {}", run_id, symbol);

    match ReplayRecompute::run_recompute(run_id, symbol) {
        Ok(_) => {
            println!("Successfully generated replay offline obs vectors.");
        }
        Err(e) => {
            eprintln!("Error generating parity vectors: {}", e);
            std::process::exit(1);
        }
    }
}
