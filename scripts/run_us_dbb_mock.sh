#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${DAY_TRADER_APP_DIR:-/home/ubuntu/day-trader-api}"
VENV_PY="${DAY_TRADER_PYTHON:-$APP_DIR/venv/bin/python}"
ENV_FILE="${DAY_TRADER_ENV_FILE:-/etc/day-trader/kiwoom-mock.env}"

cd "$APP_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

[[ -x "$VENV_PY" ]] || { echo "FAIL python=$VENV_PY"; exit 2; }
[[ -f "$APP_DIR/daytrader.db" ]] || { echo "FAIL db=$APP_DIR/daytrader.db"; exit 2; }
[[ -f "$APP_DIR/tools/dbb_pair_mock_live.py" ]] || { echo "FAIL runner=$APP_DIR/tools/dbb_pair_mock_live.py"; exit 2; }

export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" "$APP_DIR/tools/dbb_pair_mock_live.py" "$@"
