#!/usr/bin/env bash
set -u

ROOT=/home/ubuntu/day-trader-api
REPO=/home/ubuntu/day-trader-api-repo
TS=$(date +%Y%m%d_%H%M%S)
OUT="$ROOT/engine_recovery_$TS"
mkdir -p "$OUT"

exec > >(tee -a "$OUT/run.log") 2>&1

echo "===== ENGINE RECOVERY INVENTORY V1 ====="
echo "START $(date -Is)"
echo "OUT=$OUT"
echo

echo "[1] SYSTEM"
uname -a > "$OUT/system.txt" 2>&1 || true
free -h >> "$OUT/system.txt" 2>&1 || true
df -h >> "$OUT/system.txt" 2>&1 || true
systemctl is-active day-trader-api.service day-trader-v5.service kr-orderflow-shadow.service kr-trend-shadow.service day-trader-live-alert.service > "$OUT/services.txt" 2>&1 || true

echo "[2] GIT STATUS / HISTORY"
(
  cd "$REPO" || exit 0
  git status --short
  echo "--- BRANCH ---"
  git branch --show-current
  echo "--- RECENT COMMITS ---"
  git log --date=iso --pretty=format:'%h %ad %s' -n 250
) > "$OUT/git_history.txt" 2>&1 || true

echo "[3] ENGINE / REPLAY / VALIDATION FILE INVENTORY"
for BASE in "$ROOT" "$REPO"; do
  find "$BASE" -maxdepth 4 -type f \
    \( -iname '*trend*' -o -iname '*replay*' -o -iname '*scalp*' -o -iname '*rebound*' -o -iname '*validation*' -o -iname '*oos*' -o -iname '*cost*' -o -iname '*stress*' -o -iname '*causal*' -o -iname '*engine*' -o -iname '*part18*' -o -iname '*dynamic*rsi*' -o -iname '*macd*' \) \
    -printf '%TY-%Tm-%Td %TH:%TM:%TS\t%10s\t%p\n' 2>/dev/null
 done | sort > "$OUT/engine_files.tsv"

echo "[4] RESULT-LIKE FILES"
for BASE in "$ROOT" /tmp; do
  find "$BASE" -maxdepth 4 -type f \
    \( -iname '*.csv' -o -iname '*.json' -o -iname '*.txt' -o -iname '*.log' -o -iname '*.md' \) \
    -mtime -30 -printf '%TY-%Tm-%Td %TH:%TM:%TS\t%10s\t%p\n' 2>/dev/null
 done | sort > "$OUT/recent_result_files.tsv"

echo "[5] METRIC / PASS-FAIL RECOVERY GREP"
PATTERN='PASS|FAIL|PROMOTE|REJECT|OOS|MDD|MAX_DRAWDOWN|DRAW(DOWN)?|WIN.?RATE|TRADES|NET|PNL|P&L|RETURN|PROFIT|LOSS|COST|STRESS|CAUSAL|FULL_FAIL_2BAR|STRUCT_2BAR|UNIQUE|PARSED|DB ROWS|REGULAR_OK'
for BASE in "$ROOT" "$REPO"; do
  find "$BASE" -maxdepth 4 -type f \
    \( -name '*.py' -o -name '*.md' -o -name '*.txt' -o -name '*.log' -o -name '*.csv' -o -name '*.json' \) \
    -not -path '*/venv/*' -not -path '*/venv-ui/*' -not -path '*/.git/*' -print0 2>/dev/null |
    xargs -0 grep -HnEi "$PATTERN" 2>/dev/null || true
 done > "$OUT/metric_hits.txt"

echo "[6] TARGETED VERSION SEARCH"
TARGETS='trend_v4|trend_v5|trend_v51|trend_v52|trend_v53|trend_v54|trend_v55|staged_causal|scalp|rebound|part18|dynamic.*rsi|macd|v1.*v7|V1.*V7'
for BASE in "$ROOT" "$REPO"; do
  find "$BASE" -maxdepth 4 -type f \
    \( -name '*.py' -o -name '*.md' -o -name '*.txt' -o -name '*.log' -o -name '*.csv' -o -name '*.json' \) \
    -not -path '*/venv/*' -not -path '*/venv-ui/*' -not -path '*/.git/*' -print0 2>/dev/null |
    xargs -0 grep -HnEi "$TARGETS" 2>/dev/null || true
 done > "$OUT/version_hits.txt"

echo "[7] SQLITE INVENTORY"
DB="$ROOT/daytrader.db"
if [ -f "$DB" ]; then
  sqlite3 "$DB" ".tables" > "$OUT/db_tables.txt" 2>&1 || true
  sqlite3 "$DB" "SELECT name, sql FROM sqlite_master WHERE type='table' AND (name LIKE '%trade%' OR name LIKE '%validation%' OR name LIKE '%history%' OR name LIKE '%metric%' OR name LIKE '%position%' OR name LIKE '%signal%');" > "$OUT/db_engine_schema.txt" 2>&1 || true
  for T in historical_minute_bars daily_history daily_metrics v4_positions v4_signal_events v4_trade_log v4_validation_marks; do
    sqlite3 "$DB" "SELECT '$T' AS table_name, COUNT(*) AS n FROM $T;" >> "$OUT/db_counts.txt" 2>/dev/null || true
  done
  sqlite3 "$DB" "SELECT symbol, MIN(trade_date), MAX(trade_date), COUNT(*) FROM historical_minute_bars GROUP BY symbol ORDER BY symbol;" > "$OUT/minute_coverage.tsv" 2>/dev/null || true
fi

echo "[8] IMPORTANT LOG TAILS"
for SVC in day-trader-api.service kr-orderflow-shadow.service kr-trend-shadow.service day-trader-live-alert.service; do
  echo "===== $SVC =====" >> "$OUT/service_logs.txt"
  journalctl -u "$SVC" --since '7 days ago' --no-pager 2>/dev/null | tail -1000 >> "$OUT/service_logs.txt" || true
 done

echo "[9] PACKAGE RECOVERY BUNDLE"
(
  cd "$ROOT" || exit 0
  tar -czf "engine_recovery_${TS}.tar.gz" "engine_recovery_$TS"
)

echo

echo "DONE $(date -Is)"
echo "REPORT_DIR=$OUT"
echo "BUNDLE=$ROOT/engine_recovery_${TS}.tar.gz"
echo "NO STRATEGY EXECUTION PERFORMED"
