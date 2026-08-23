#!/usr/bin/env bash
set -u

API="http://127.0.0.1:8000"

echo "===== DAY TRADER V5 PRE-OPEN GO-LIVE CHECK v26 ====="
date

echo
echo "===== SERVICES ====="
for svc in day-trader-api.service day-trader-v5.service; do
  printf "%-28s " "$svc"
  systemctl is-active "$svc" 2>/dev/null || true
done

echo
echo "===== PORTS ====="
ss -ltnp 2>/dev/null | grep -E ':8000|:8503' || true

echo
echo "===== RUNTIME MODE ====="
curl -fsS "$API/api/v4/runtime-mode" || echo "RUNTIME_MODE_FAIL"
echo

echo
echo "===== KOREA STATUS ====="
curl -fsS "$API/api/v4/KOREA/status" | python3 - <<'PY' 2>/dev/null || echo "KOREA_STATUS_FAIL"
import json,sys
try:
    x=json.load(sys.stdin)
    print(json.dumps({
        'market':x.get('market'),
        'session':x.get('session'),
        'finder_rows':len(((x.get('finder') or {}).get('rows') or [])),
        'tracker_rows':len(((x.get('tracker') or {}).get('rows') or [])),
        'positions':len(x.get('positions') or []),
        'events':len(x.get('events') or []),
    },ensure_ascii=False))
except Exception as e:
    print('PARSE_FAIL',repr(e))
PY

echo
echo "===== LOAD ====="
uptime
free -h | sed -n '1,3p'
ps -eo pid,%cpu,%mem,etime,cmd --sort=-%cpu | head -10

echo
echo "===== QUICK VERDICT ====="
api_ok=0; ui_ok=0
curl -fsS "$API/api/v4/runtime-mode" >/dev/null 2>&1 && api_ok=1
curl -fsS "http://127.0.0.1:8503" >/dev/null 2>&1 && ui_ok=1
if [ "$api_ok" -eq 1 ] && [ "$ui_ok" -eq 1 ]; then
  echo "GO-LIVE_BASE_OK: API/UI reachable"
else
  echo "GO-LIVE_BASE_FAIL: api_ok=$api_ok ui_ok=$ui_ok"
fi

echo "NOTE: 장중 실제 단타 분석이 필요할 때만 UI에서 DAYTRADE로 전환. 평소에는 NORMAL 유지."
