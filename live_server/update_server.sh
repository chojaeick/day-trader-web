#!/usr/bin/env bash
set -euo pipefail
BASE=/home/ubuntu/day-trader-api
REPO=/home/ubuntu/day-trader-api-repo
cd "$REPO"
git pull --ff-only
cp -r live_server trader "$BASE"/
cp requirements.txt "$BASE"/requirements-web.txt || true
cd "$BASE"
source venv/bin/activate
pip install -r live_server/requirements.txt
sudo systemctl restart day-trader-api
sleep 2
sudo systemctl --no-pager --full status day-trader-api | head -30
curl -s http://127.0.0.1:8000/health; echo
