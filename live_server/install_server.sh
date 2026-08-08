#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/day-trader-api
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r live_server/requirements.txt
sudo cp live_server/day-trader-api.service /etc/systemd/system/day-trader-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now day-trader-api
sleep 2
sudo systemctl --no-pager --full status day-trader-api | sed -n '1,18p'
echo
echo 'Health test:'
curl -s http://127.0.0.1:8000/health || true
echo
