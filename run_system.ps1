# run_system.ps1
# Unified startup script for Bot Mk3

Write-Host "Starting Bot Mk3 System..." -ForegroundColor Cyan

# Find and kill old python zombies holding ports
Write-Host "Cleaning up old zombie processes..." -ForegroundColor Yellow
$netstatOutput = netstat -ano | findstr :50051
if ($netstatOutput) {
    $netstatOutput | ForEach-Object {
        $parts = $_ -split '\s+'
        $pidToKill = $parts[-1]
        if ($pidToKill -ne "0" -and $pidToKill -ne "") {
            Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
        }
    }
}
$netstatOutputPolicy = netstat -ano | findstr :50055
if ($netstatOutputPolicy) {
    $netstatOutputPolicy | ForEach-Object {
        $parts = $_ -split '\s+'
        $pidToKill = $parts[-1]
        if ($pidToKill -ne "0" -and $pidToKill -ne "") {
            Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
        }
    }
}
$netstatOutputVite = netstat -ano | findstr :1420
if ($netstatOutputVite) {
    $netstatOutputVite | ForEach-Object {
        $parts = $_ -split '\s+'
        $pidToKill = $parts[-1]
        if ($pidToKill -ne "0" -and $pidToKill -ne "") {
            Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
        }
    }
}


# 1. Start Server in a dedicated Window
Write-Host "Launching Data Server (Rust)..." -ForegroundColor Green
$env:PATH = "C:\msys64\mingw64\bin;C:\msys64\usr\bin;" + $env:PATH
$cargoPath = "$env:USERPROFILE\.cargo\bin\cargo.exe"
Start-Process -FilePath $cargoPath -ArgumentList "run -p bot-server --target x86_64-pc-windows-gnu" -WorkingDirectory "."

# 2. Wait for server to initialize
Write-Host "Waiting 5 seconds for Data Server to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# 3. Start Policy Server in a dedicated Window
Write-Host "Launching Policy Server (Python RL)..." -ForegroundColor Green
$env:PYTHONPATH = ".\python"
Start-Process -FilePath "python" -ArgumentList "python/bot_policy/policy_server.py" -WorkingDirectory "."

# 4. Wait for Policy server
Write-Host "Waiting 3 seconds for Policy Server..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

# 5. Start GUI V3 Shell (Tauri) in a dedicated Window
Write-Host "Launching GUI V3 Shell (Tauri)..." -ForegroundColor Green
$env:PATH = "C:\Program Files\nodejs;C:\Program Files (x86)\nodejs;C:\Users\$env:USERNAME\AppData\Roaming\npm;" + $env:PATH
Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run tauri dev" -WorkingDirectory "ui"

# 6. Start Auto-Trainer in a dedicated Window
Write-Host "Launching Auto-Trainer..." -ForegroundColor Green
Start-Process -FilePath "python" -ArgumentList "python/bot_ml/auto_trainer.py" -WorkingDirectory "."

Write-Host "System Launched Successfully!" -ForegroundColor Cyan
Write-Host "Components: Data Server | Policy Server | GUI | Auto-Trainer"
Write-Host "You may close this launcher window now. The Server and GUI windows will remain open."
Start-Sleep -Seconds 5
