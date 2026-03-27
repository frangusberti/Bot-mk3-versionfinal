#!/usr/bin/env bash
set -euo pipefail

TRACK_FILE="TRACKING_CAMBIOS_RISK_ORCHESTRATOR.txt"
if [[ ! -f "$TRACK_FILE" ]]; then
  echo "No existe $TRACK_FILE" >&2
  exit 1
fi

TS_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'N/A')"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'N/A')"

cat >> "$TRACK_FILE" <<EOT

Auto-update tracking (${TS_UTC})
--------------------------------
- Commit base: ${COMMIT_SHA}
- Branch: ${BRANCH}
- Nota: actualización automática de tracking para mantener historial operativo.
EOT

echo "Tracking actualizado: $TRACK_FILE"
