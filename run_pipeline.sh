#!/bin/bash
# Weather Trader — pipeline runner
# Run after each GFS model drop: 03:30, 09:30, 15:30, 21:30 UTC

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/pipeline_$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S') UTC — Pipeline start ===" | tee -a "$LOG_FILE"

cd "$SCRIPT_DIR"
source venv/bin/activate

echo "[1/2] Fetching forecasts..." | tee -a "$LOG_FILE"
python scripts/fetch_forecasts.py 2>&1 | tee -a "$LOG_FILE"

sleep 30

echo "[2/2] Running signal engine..." | tee -a "$LOG_FILE"
python scripts/signal_engine.py 2>&1 | tee -a "$LOG_FILE"

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S') UTC — Pipeline complete ===" | tee -a "$LOG_FILE"

# ──────────────────────────────────────────────
# To automate with cron (Mac/Linux):
# Run: crontab -e
# Add these 4 lines (replace /path/to with your actual path):
#
# 30 3  * * * /path/to/weather-trader/run_pipeline.sh
# 30 9  * * * /path/to/weather-trader/run_pipeline.sh
# 30 15 * * * /path/to/weather-trader/run_pipeline.sh
# 30 21 * * * /path/to/weather-trader/run_pipeline.sh
# ──────────────────────────────────────────────
