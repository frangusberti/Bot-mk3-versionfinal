# run_e2e_paper_live.ps1
# 1. Start Policy Server in a new window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd python/bot_policy; python policy_server.py"

# 2. Start bot-server
# Wait a bit for policy server to warm up
Start-Sleep -Seconds 5
cargo run --bin bot-server
