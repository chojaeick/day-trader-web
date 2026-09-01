#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${DAY_TRADER_APP_DIR:-/home/ubuntu/day-trader-api}"
PY="${DAY_TRADER_PYTHON:-$APP_DIR/venv/bin/python}"
cd "$APP_DIR"
[[ -x "$PY" ]] || { echo "FAIL python=$PY"; exit 2; }
[[ -f "$APP_DIR/daytrader.db" ]] || { echo "FAIL db=$APP_DIR/daytrader.db"; exit 2; }
export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" tools/v22_kr_backdata_validate.py --db "$APP_DIR/daytrader.db" "$@"
