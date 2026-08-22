#!/usr/bin/env bash
set -u

ROOT="/home/ubuntu/day-trader-api"
OUT="/tmp/kr_prototype_deploy_report.txt"
cd "$ROOT" || exit 1

exec > >(tee "$OUT") 2>&1

echo "===== KR PROTOTYPE DEPLOY V1 ====="
date
echo

echo "===== 1. SYNTAX ====="
./venv/bin/python -m py_compile live_server/v4_engine.py app.py
if [ $? -eq 0 ]; then
  echo "SYNTAX_OK True"
else
  echo "SYNTAX_OK False"
  exit 2
fi
echo

echo "===== 2. RESTART SERVICES ====="
SERVICES=(
  day-trader-api.service
  day-trader-kakao.service
  day-trader-live-alert.service
  day-trader-shadow-alert.service
  kr-orderflow-shadow.service
  kr-trend-shadow.service
)

for S in "${SERVICES[@]}"; do
  echo "--- $S ---"
  sudo systemctl restart "$S" || true
done

sleep 4
echo

echo "===== 3. SERVICE HEALTH ====="
ALL_OK=1
for S in "${SERVICES[@]}"; do
  A=$(systemctl is-active "$S" 2>/dev/null || true)
  E=$(systemctl is-enabled "$S" 2>/dev/null || true)
  echo "$S ACTIVE=$A ENABLED=$E"
  [ "$A" = "active" ] || ALL_OK=0
done
echo "SERVICES_ACTIVE_OK $([ "$ALL_OK" -eq 1 ] && echo True || echo False)"
echo

echo "===== 4. API PORT / PROCESS ====="
systemctl show day-trader-api.service -p ExecStart -p Environment --no-pager || true
ss -ltnp 2>/dev/null | grep -E ':(8000|8501|8080|80|443)\b' || true
echo

echo "===== 5. KR API PROTOTYPE SMOKE ====="
API="http://127.0.0.1:8000"
TMP="/tmp/kr_status.json"

curl -fsS "$API/api/v4/KOREA/status" -o "$TMP"
CURL_RC=$?
echo "KR_STATUS_HTTP_OK $([ "$CURL_RC" -eq 0 ] && echo True || echo False)"

if [ "$CURL_RC" -eq 0 ]; then
  ./venv/bin/python - <<'PY'
import json
p="/tmp/kr_status.json"
d=json.load(open(p))
tr=(d.get("tracker") or {})
rows=tr.get("rows") or []
print("KR_SESSION", d.get("session"))
print("TRACKED_COUNT", tr.get("tracked_count", len(rows)))
print("ROWS", len(rows))
print("PROTOTYPE_FIELD_ROWS", sum(1 for r in rows if "prototype_action" in r))
for r in rows[:5]:
    print(
        "ROW",
        r.get("symbol"),
        "state="+str(r.get("state")),
        "direction="+str(r.get("direction")),
        "prototype_action="+str(r.get("prototype_action")),
        "prototype_confidence="+str(r.get("prototype_confidence")),
        "prototype_reason="+str(r.get("prototype_reason")),
    )
print("PROTOTYPE_API_OK", bool(rows) and all("prototype_action" in r for r in rows))
PY
else
  echo "PROTOTYPE_API_OK False"
fi
echo

echo "===== 6. APP / UI PROTOTYPE MARKER ====="
grep -n "KR_PROTOTYPE_DECISION_V2" app.py || true
grep -n "KR_SHADOW_PROTO_V2" live_server/v4_engine.py || true
echo

echo "===== 7. KAKAO SERVICE EXECSTART ====="
for S in day-trader-kakao.service day-trader-live-alert.service day-trader-shadow-alert.service; do
  echo "--- $S ---"
  systemctl show "$S" -p ExecStart --no-pager || true
done
echo

echo "===== 8. KAKAO / ALERT SOURCE DISCOVERY ====="
grep -RniE \
  --exclude='*.bak' --exclude='*.pyc' --exclude='daytrader.db*' \
  --exclude-dir='venv' --exclude-dir='__pycache__' \
  'KAKAO|prototype_action|STATE_CHANGE|POWER_JUMP|shadow.?alert|live.?alert' \
  live_server tools *.py 2>/dev/null | head -160
echo

echo "===== 9. RECENT SERVICE ERRORS ====="
for S in day-trader-api.service day-trader-kakao.service day-trader-live-alert.service day-trader-shadow-alert.service; do
  echo "--- $S ---"
  journalctl -u "$S" --since "-5 min" --no-pager -p warning..alert | tail -25 || true
done
echo

echo "===== 10. DECISION ====="
API_OK=$(grep -q "PROTOTYPE_API_OK True" "$OUT" && echo 1 || echo 0)
if [ "$ALL_OK" -eq 1 ] && [ "$API_OK" -eq 1 ]; then
  echo "KR_PROTOTYPE_CORE_DEPLOYED True"
  echo "NEXT Kakao prototype-action alert patch only."
else
  echo "KR_PROTOTYPE_CORE_DEPLOYED False"
  echo "NEXT Fix service/API failure shown above before Kakao patch."
fi

echo "REPORT $OUT"
