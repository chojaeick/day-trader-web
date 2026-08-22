#!/usr/bin/env bash
set -u
ROOT=/home/ubuntu/day-trader-api
OUT=/tmp/kr_prototype_api_fix.txt
cd "$ROOT" || exit 1
exec > >(tee "$OUT") 2>&1

echo "===== KR PROTOTYPE API DIAG/FIX ====="
echo "1) CURRENT SERVICE"
systemctl --no-pager --full status day-trader-api.service | tail -35 || true
echo
echo "2) CURRENT PROCESS/PORT"
ps -ef | grep '[u]vicorn.*live_server.api' || true
ss -ltnp | grep ':8000' || true
echo
echo "3) JOURNAL"
journalctl -u day-trader-api.service -n 100 --no-pager || true
echo
echo "4) DIRECT IMPORT"
./venv/bin/python - <<'PY'
import traceback
try:
    import live_server.api
    print("API_IMPORT_OK True")
except Exception:
    print("API_IMPORT_OK False")
    traceback.print_exc()
PY
echo
echo "5) WAIT/RETRY"
OK=0
for i in $(seq 1 20); do
  if curl -fsS --max-time 2 http://127.0.0.1:8000/api/v4/KOREA/status >/tmp/kr_status.json 2>/dev/null; then
    echo "API_READY attempt=$i"
    OK=1
    break
  fi
  sleep 1
done

if [ "$OK" -eq 1 ]; then
  ./venv/bin/python - <<'PY'
import json
d=json.load(open('/tmp/kr_status.json'))
tr=d.get('tracker') or {}
rows=tr.get('rows') or []
print("ROWS",len(rows))
print("PROTOTYPE_FIELD_ROWS",sum('prototype_action' in r for r in rows))
for r in rows[:5]:
    print(r.get('symbol'),r.get('prototype_action'),r.get('prototype_confidence'),r.get('prototype_reason'))
print("PROTOTYPE_API_OK", bool(rows) and all('prototype_action' in r for r in rows))
PY
else
  echo "API_READY False"
fi

echo
echo "6) DECISION"
if [ "$OK" -eq 1 ]; then
 echo "CORE_API_RECOVERED True"
else
 echo "CORE_API_RECOVERED False"
 echo "DO_NOT_PATCH_KAKAO_YET True"
fi
