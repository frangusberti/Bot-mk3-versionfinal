# Running Bot Mk3

## Quick Start (Recommended)
We provide a unified startup script tto launch the entire system (Server + GUI).

```powershell
.\run_system.ps1
```

## Manual Startup

### 1. Start the Rust Server (Control Plane)
Open a terminal and run:
```powershell
cargo run -p bot-server
```
*Output should say: "Bot Mk3 Server listening on 0.0.0.0:50051"*

### 2. Start the Python GUI (Presentation Plane)
Open a **new** terminal and run:
```powershell
# Install dependencies if not already done
pip install -r python/requirements.txt

# Run the GUI
python python/bot_gui/main.py
```

## 3. Verify Connection
- The GUI window should appear with a "System: Healthy" status.
- The "Status" tab should show a live-updating tree of components.
