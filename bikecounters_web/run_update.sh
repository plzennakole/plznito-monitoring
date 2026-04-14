#!/bin/bash
# run_update.sh — cron wrapper for ingest.py
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$SCRIPT_DIR/ingest.log"
PYTHON="${PYTHON:-python3}"

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || stat -f%z "$LOG")" -gt 1048576 ]; then
    mv "$LOG" "$LOG.1"
fi

echo "──────────────────────────────────────" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S')  START" >> "$LOG"
cd "$SCRIPT_DIR"

if "$PYTHON" ingest.py "$@" >> "$LOG" 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M:%S')  OK" >> "$LOG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S')  FAILED (exit $?)" >> "$LOG"
    exit 1
fi
