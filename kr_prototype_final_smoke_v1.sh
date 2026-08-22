#!/usr/bin/env bash
set -u
ROOT=/home/ubuntu/day-trader-api
OUT=/tmp/kr_prototype_final_smoke.txt
cd "$ROOT" || exit 1
exec > >(tee "$OUT") 2>&1

echo "===== KR PROTOTYPE FINAL SMOKE V1 ====="

echo "1) SYNTAX"
./venv/bin/python -m py_compile live_server/v4_engine.py app.py live_server/kakao_live_alert.py
echo "SYNTAX_OK=$?"

echo
echo "2) CORE MARKERS"
grep -q "KR_SHADOW_PROTO_V2" live_server/v4_engine.py && echo "ENGINE_PROTO=True" || echo "ENGINE_PROTO=False"
grep -q "prototype_action" live_server/v4_engine.py && echo "ENGINE_ACTION=True" || echo "ENGINE_ACTION=False"
grep -q "KR_PROTOTYPE_DECISION_V2" app.py && echo "APP_PANEL=True" || echo "APP_PANEL=False"
grep -q "'direction':'UNVERIFIED'" live_server/v4_engine.py && echo "PRODUCTION_GUARD=True" || echo "PRODUCTION_GUARD=False"

echo
echo "3) SERVICES"
for S in day-trader-api.service day-trader-live-alert.service kr-orderflow-shadow.service kr-trend-shadow.service; do
  printf "%s=" "$S"
  systemctl is-active "$S" 2>/dev/null || true
done

echo
echo "4) API"
if curl -fsS --max-time 5 http://127.0.0.1:8000/api/v4/KOREA/status >/tmp/kr_proto_status.json; then
  echo "KR_API=True"
  ./venv/bin/python - <<'PY'
import json
d=json.load(open('/tmp/kr_proto_status.json'))
tr=d.get('tracker') or {}
rows=tr.get('rows') or []
print("SESSION",d.get('session'))
print("TRACKER_ROWS",len(rows))
print("NOTE Sunday/closed market may legitimately have 0 rows")
PY
else
  echo "KR_API=False"
fi

echo
echo "5) OFFLINE PROTOTYPE DECISION LOGIC PRESENCE"
./venv/bin/python - <<'PY'
from pathlib import Path
s=Path('live_server/v4_engine.py').read_text()
required=[
"proto_action='BUY_REVIEW'",
"proto_action='ADD_REVIEW'",
"proto_action='EXIT_REVIEW'",
"proto_action='AVOID'",
"proto_action='DATA_WAIT'",
]
for x in required:
    print(x, x in s)
PY

echo
echo "6) CURRENT KAKAO RESTORE"
./venv/bin/python -m py_compile live_server/kakao_live_alert.py && echo "KAKAO_EXISTING_MODULE_OK=True" || echo "KAKAO_EXISTING_MODULE_OK=False"

echo
echo "===== DECISION ====="
PASS=1
grep -q "ENGINE_PROTO=True" "$OUT" || PASS=0
grep -q "ENGINE_ACTION=True" "$OUT" || PASS=0
grep -q "APP_PANEL=True" "$OUT" || PASS=0
grep -q "PRODUCTION_GUARD=True" "$OUT" || PASS=0
grep -q "KR_API=True" "$OUT" || PASS=0
grep -q "KAKAO_EXISTING_MODULE_OK=True" "$OUT" || PASS=0

if [ "$PASS" -eq 1 ]; then
  echo "KR_PROTOTYPE_CORE_READY=True"
  echo "MONDAY_MODE=SHADOW_MANUAL_ORDER"
  echo "NEXT=Observe live KR tracker rows and prototype_action during market"
else
  echo "KR_PROTOTYPE_CORE_READY=False"
fi
