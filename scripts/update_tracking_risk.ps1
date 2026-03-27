# update_tracking_risk.ps1
# Appends an operational entry to TRACKING_CAMBIOS_RISK_ORCHESTRATOR.txt

$ErrorActionPreference = "Stop"

$TrackFile = "TRACKING_CAMBIOS_RISK_ORCHESTRATOR.txt"
if (-Not (Test-Path $TrackFile)) {
    Write-Error "No existe $TrackFile"
    exit 1
}

$TsUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

try {
    $CommitSha = git rev-parse --short HEAD 2>$null
}
catch {
    $CommitSha = "N/A"
}

try {
    $Branch = git rev-parse --abbrev-ref HEAD 2>$null
}
catch {
    $Branch = "N/A"
}

$Entry = @"

Auto-update tracking ($TsUtc)
--------------------------------
- Commit base: $CommitSha
- Branch: $Branch
- Nota: actualizacion automatica de tracking para mantener historial operativo.
"@

Add-Content -Path $TrackFile -Value $Entry

Write-Host "Tracking actualizado: $TrackFile"
