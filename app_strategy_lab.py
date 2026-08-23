from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(os.getenv("DAYTRADER_ROOT", "/home/ubuntu/day-trader-api"))
DB = Path(os.getenv("DAYTRADER_DB", str(ROOT / "daytrader.db")))

st.set_page_config(page_title="DAY TRADER · Strategy Lab", page_icon="🧪", layout="wide")

st.markdown(
    """
    <style>
    .block-container{padding-top:.55rem;padding-bottom:.7rem;max-width:1850px}
    h1{font-size:1.65rem!important;margin:.05rem 0 .1rem 0!important}
    h2,h3{margin:.35rem 0 .2rem 0!important}
    [data-testid="stMetric"]{background:#11151b;border:1px solid #2b313a;border-radius:10px;padding:.45rem .65rem}
    [data-testid="stMetricLabel"]{font-size:.72rem!important}
    [data-testid="stMetricValue"]{font-size:1.05rem!important;line-height:1.15!important}
    [data-testid="stDataFrame"]{font-size:.78rem}
    .stCaption{font-size:.72rem!important;margin-top:.1rem!important}
    .lab-card{border:1px solid #30363d;border-radius:10px;padding:8px 10px;margin:3px 0;background:#11151b;font-size:.78rem;line-height:1.35}
    .lab-kicker{opacity:.72;font-size:.72rem;margin-bottom:.2rem}
    .lab-title{font-size:.92rem;font-weight:700}
    .lab-status{font-size:.72rem;font-weight:700;margin-top:.15rem}
    .lab-next{font-size:.72rem;opacity:.8;margin-top:.1rem}
    div[data-testid="stVerticalBlock"]{gap:.35rem}
    div[data-testid="stHorizontalBlock"]{gap:.45rem}
    .stTabs [data-baseweb="tab-list"]{gap:.25rem}
    .stTabs [data-baseweb="tab"]{height:2rem;padding:0 .8rem;font-size:.78rem}
    </style>
    """,
    unsafe_allow_html=True,
)

STRATEGIES = [
    {"engine":"TREND_V1","family":"DAY TRADER Core","tf":"1m/5m","status":"STAGED_CAUSAL","source_fidelity":"INTERNAL","next":"Continue staged causal / temporal OOS"},
    {"engine":"MA20_SCALP","family":"Mean Reversion","tf":"1m","status":"REWORK_ENTRY_STOP","source_fidelity":"INTERNAL","next":"Rebuild extreme-gap entry + stop architecture"},
    {"engine":"FUJIMOTO","family":"Staged Swing","tf":"Daily / intraday study","status":"REIMPLEMENTATION_ONLY","source_fidelity":"PARTIAL","next":"Reproduce original Dynamic RSI / staged defense first"},
    {"engine":"TOM_DEMARK_HA","family":"Exhaustion / Reversal","tf":"5m","status":"REIMPLEMENTATION_ONLY","source_fidelity":"PARTIAL","next":"Preserve results; do not call original strategy rejected"},
    {"engine":"JARED_3BAR_4BAR","family":"Momentum Breakout","tf":"1m/5m","status":"PUBLIC_PATTERN_BASELINE","source_fidelity":"PARTIAL","next":"Run baseline then freeze survivor for temporal OOS"},
    {"engine":"JARED_CUSTOM","family":"Custom Long/Short","tf":"Unknown","status":"NOT_REPRODUCED","source_fidelity":"LOW","next":"Acquire original indicator rules before backtest"},
    {"engine":"PREDATOR_2_GTOP","family":"High Win-rate Scalping","tf":"Intraday","status":"NOT_REPRODUCED","source_fidelity":"LOW","next":"Reverse engineer G(bottom) / Top generation first"},
    {"engine":"ETHAN_NY_BREAKOUT","family":"Nasdaq Breakout + Retest","tf":"4H filter / 5m entry / 1m confirm","status":"SOURCE_LOCKED_REPLICATION","source_fidelity":"HIGH","next":"V-shape → space → 50SMA → break/close → retest"},
]


def query(sql: str, params=()):
    if not DB.exists():
        return pd.DataFrame()
    con = sqlite3.connect(str(DB), timeout=20)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def db_summary():
    z = query("""
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT symbol) AS symbols,
               COUNT(DISTINCT trade_date) AS dates,
               MIN(trade_date) AS first_date,
               MAX(trade_date) AS last_date
        FROM historical_minute_bars
        WHERE interval_min=1
    """)
    return z.iloc[0].to_dict() if not z.empty else {}


def coverage():
    return query("""
        SELECT symbol,
               COUNT(DISTINCT trade_date) AS days,
               COUNT(*) AS rows,
               MIN(trade_date) AS first_date,
               MAX(trade_date) AS last_date,
               SUM(CASE WHEN session='REGULAR' THEN 1 ELSE 0 END) AS regular_rows
        FROM historical_minute_bars
        WHERE interval_min=1
        GROUP BY symbol
        ORDER BY days DESC, symbol
    """)


def expected_coverage(start="20260202", end="20260814"):
    syms = ["AMD","AMZN","AVGO","GOOGL","INTC","NFLX","NVDA","ORCL","PLTR","SMCI","SPY","QQQ","SMH","SOXL","SOXS","TQQQ","SQQQ"]
    existing = query("""
        SELECT symbol, trade_date
        FROM historical_minute_bars
        WHERE interval_min=1 AND trade_date BETWEEN ? AND ?
        GROUP BY symbol, trade_date
    """, (start, end))
    if existing.empty:
        return pd.DataFrame()
    weekdays = pd.date_range(pd.to_datetime(start), pd.to_datetime(end), freq="B").strftime("%Y%m%d").tolist()
    have = set(zip(existing.symbol.astype(str), existing.trade_date.astype(str)))
    rows=[]
    for s in syms:
        present=sum((s,d) in have for d in weekdays)
        rows.append({"symbol":s,"weekday_pairs":len(weekdays),"present_pairs":present,"raw_gaps":len(weekdays)-present,"coverage_pct":round(present/len(weekdays)*100,1)})
    return pd.DataFrame(rows)


st.title("🧪 DAY TRADER · Strategy Lab")
st.caption("원본 재현도 · 전략 상태 · DB 커버리지 · 비교 Scorecard를 한 화면에서 보는 개발 대시보드")

summary = db_summary()
c1,c2,c3,c4,c5 = st.columns([1,1,1,1,1.45])
c1.metric("DB", f"{DB.stat().st_size/1024**3:.2f} GB" if DB.exists() else "missing")
c2.metric("1m Rows", f"{int(summary.get('rows',0)):,}")
c3.metric("Symbols", f"{int(summary.get('symbols',0)):,}")
c4.metric("Dates", f"{int(summary.get('dates',0)):,}")
c5.metric("Range", f"{summary.get('first_date','-')} → {summary.get('last_date','-')}")

# Strategy cards: 4 columns x 2 rows, much denser than a tall registry table.
st.markdown("### 전략 현황")
for row_start in (0,4):
    cols = st.columns(4)
    for col, s in zip(cols, STRATEGIES[row_start:row_start+4]):
        with col:
            st.markdown(
                f"""
                <div class="lab-card">
                  <div class="lab-kicker">{s['family']} · {s['tf']}</div>
                  <div class="lab-title">{s['engine']}</div>
                  <div class="lab-status">{s['status']} · Fidelity {s['source_fidelity']}</div>
                  <div class="lab-next">→ {s['next']}</div>
                </div>
                """, unsafe_allow_html=True)

cov = coverage()
gaps = expected_coverage()
score = pd.DataFrame([
    {"engine":x["engine"],"trades":None,"win_rate":None,"expectancy_R":None,"PF":None,"MDD":None,"cost_net":None,"OOS":None,"rank":None}
    for x in STRATEGIES
])

# Main single-screen work area: scorecard gets the largest space; DB tables stay visible to the right.
left, mid, right = st.columns([1.55,1.0,1.0])
with left:
    st.markdown("### 비교 Scorecard")
    st.dataframe(score, width="stretch", hide_index=True, height=315)
    st.caption("원본 재현 gate 통과 후에만 실측값 기록")
with mid:
    st.markdown("### DB Coverage")
    if cov.empty:
        st.warning("historical_minute_bars를 읽지 못했습니다.")
    else:
        show_cov = cov[["symbol","days","rows","regular_rows"]].copy()
        st.dataframe(show_cov, width="stretch", hide_index=True, height=315)
with right:
    st.markdown("### 17종목 Raw Gap")
    if gaps.empty:
        st.warning("coverage 계산 데이터가 없습니다.")
    else:
        show_gaps = gaps[["symbol","present_pairs","raw_gaps","coverage_pct"]].sort_values(["raw_gaps","symbol"],ascending=[False,True])
        st.dataframe(show_gaps, width="stretch", hide_index=True, height=315)

with st.expander("판정 원칙 / 개발 메모", expanded=False):
    st.markdown(
        """
        **NOT_REPRODUCED** → 원본 규칙 미확보. 전략 성과 판정 금지.  
        **REIMPLEMENTATION_ONLY** → 우리가 만든 근사 구현의 결과만 의미.  
        **SOURCE_LOCKED_REPLICATION** → 원본 규칙 고정 후 예제 신호 재현부터 수행.  
        **VALIDATED_ORIGINAL** → 원본 재현 + 동일 자금관리 + temporal OOS까지 통과한 경우에만 사용.  
        Raw gap에는 미국 휴장일이 포함될 수 있으며, backfill 완료 후 거래일 캘린더 기준으로 정제합니다.
        """
    )
