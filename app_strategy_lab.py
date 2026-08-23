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
    .block-container{padding-top:1.2rem;max-width:1500px}
    [data-testid="stMetricValue"]{font-size:1.45rem}
    .lab-card{border:1px solid #30363d;border-radius:14px;padding:14px 16px;margin:8px 0;background:#11151b}
    .lab-muted{opacity:.72;font-size:.9rem}
    </style>
    """,
    unsafe_allow_html=True,
)

STRATEGIES = [
    {
        "engine": "TREND_V1",
        "family": "DAY TRADER Core",
        "tf": "1m/5m",
        "status": "STAGED_CAUSAL",
        "source_fidelity": "INTERNAL",
        "next": "Continue staged causal / temporal OOS",
    },
    {
        "engine": "MA20_SCALP",
        "family": "Mean Reversion",
        "tf": "1m",
        "status": "REWORK_ENTRY_STOP",
        "source_fidelity": "INTERNAL",
        "next": "Rebuild extreme-gap entry + stop architecture",
    },
    {
        "engine": "FUJIMOTO",
        "family": "Staged Swing",
        "tf": "Daily / intraday study",
        "status": "REIMPLEMENTATION_ONLY",
        "source_fidelity": "PARTIAL",
        "next": "Reproduce original Dynamic RSI / staged defense first",
    },
    {
        "engine": "TOM_DEMARK_HA",
        "family": "Exhaustion / Reversal",
        "tf": "5m",
        "status": "REIMPLEMENTATION_ONLY",
        "source_fidelity": "PARTIAL",
        "next": "Preserve results; do not call original strategy rejected",
    },
    {
        "engine": "JARED_3BAR_4BAR",
        "family": "Momentum Breakout",
        "tf": "1m/5m",
        "status": "PUBLIC_PATTERN_BASELINE",
        "source_fidelity": "PARTIAL",
        "next": "Run baseline then freeze survivor for temporal OOS",
    },
    {
        "engine": "JARED_CUSTOM",
        "family": "Custom Long/Short",
        "tf": "Unknown",
        "status": "NOT_REPRODUCED",
        "source_fidelity": "LOW",
        "next": "Acquire original indicator rules before backtest",
    },
    {
        "engine": "PREDATOR_2_GTOP",
        "family": "High Win-rate Scalping",
        "tf": "Intraday",
        "status": "NOT_REPRODUCED",
        "source_fidelity": "LOW",
        "next": "Reverse engineer G(bottom) / Top generation first",
    },
    {
        "engine": "ETHAN_NY_BREAKOUT",
        "family": "Nasdaq Breakout + Retest",
        "tf": "4H context / 5m entry / 1m confirm",
        "status": "SOURCE_LOCKED_REPLICATION",
        "source_fidelity": "HIGH",
        "next": "V-shape level replication → open-space → break/close → retest",
    },
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
    z = query(
        """
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT symbol) AS symbols,
               COUNT(DISTINCT trade_date) AS dates,
               MIN(trade_date) AS first_date,
               MAX(trade_date) AS last_date
        FROM historical_minute_bars
        WHERE interval_min=1
        """
    )
    return z.iloc[0].to_dict() if not z.empty else {}


def coverage():
    return query(
        """
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
        """
    )


def expected_coverage(start="20260202", end="20260814"):
    # This table intentionally reports raw weekday gaps. Known US holidays can appear as gaps.
    syms = ["AMD","AMZN","AVGO","GOOGL","INTC","NFLX","NVDA","ORCL","PLTR","SMCI","SPY","QQQ","SMH","SOXL","SOXS","TQQQ","SQQQ"]
    existing = query(
        """
        SELECT symbol, trade_date
        FROM historical_minute_bars
        WHERE interval_min=1 AND trade_date BETWEEN ? AND ?
        GROUP BY symbol, trade_date
        """,
        (start, end),
    )
    if existing.empty:
        return pd.DataFrame()
    weekdays = pd.date_range(pd.to_datetime(start), pd.to_datetime(end), freq="B").strftime("%Y%m%d").tolist()
    have = set(zip(existing.symbol.astype(str), existing.trade_date.astype(str)))
    rows=[]
    for s in syms:
        present=sum((s,d) in have for d in weekdays)
        rows.append({"symbol":s,"weekday_pairs":len(weekdays),"present_pairs":present,"raw_gaps":len(weekdays)-present,"coverage_pct":present/len(weekdays)*100})
    return pd.DataFrame(rows)


st.title("🧪 DAY TRADER · Strategy Lab")
st.caption("전략을 없애는 화면이 아니라, 원본 재현도와 성과를 같은 기준으로 비교하기 위한 개발 대시보드")

summary = db_summary()
c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("DB", f"{DB.stat().st_size/1024**3:.2f} GB" if DB.exists() else "missing")
c2.metric("1m Rows", f"{int(summary.get('rows',0)):,}")
c3.metric("Symbols", f"{int(summary.get('symbols',0)):,}")
c4.metric("Dates", f"{int(summary.get('dates',0)):,}")
c5.metric("Range", f"{summary.get('first_date','-')} → {summary.get('last_date','-')}")

st.subheader("전략 레지스트리")
reg = pd.DataFrame(STRATEGIES)
st.dataframe(reg, width="stretch", hide_index=True)

st.markdown(
    """
    <div class="lab-card">
    <b>판정 원칙</b><br>
    NOT_REPRODUCED → 원본 규칙 미확보. 전략 성과 판정 금지.<br>
    REIMPLEMENTATION_ONLY → 우리가 만든 근사 구현의 결과만 의미함.<br>
    SOURCE_LOCKED_REPLICATION → 원본 규칙을 고정하고 예제 신호 재현부터 수행.<br>
    VALIDATED_ORIGINAL → 원본 재현 + 동일 자금관리 + temporal OOS까지 통과한 경우에만 사용.
    </div>
    """,
    unsafe_allow_html=True,
)

left,right = st.columns([1.15,0.85])
with left:
    st.subheader("DB 종목별 Coverage")
    cov=coverage()
    if cov.empty:
        st.warning("historical_minute_bars를 읽지 못했습니다.")
    else:
        st.dataframe(cov, width="stretch", hide_index=True, height=470)
with right:
    st.subheader("핵심 17종목 Raw Gap")
    gaps=expected_coverage()
    if gaps.empty:
        st.warning("coverage 계산 데이터가 없습니다.")
    else:
        st.dataframe(gaps.sort_values(["raw_gaps","symbol"],ascending=[False,True]), width="stretch", hide_index=True, height=470)
        st.caption("raw_gaps에는 미국 휴장일이 포함될 수 있습니다. Backfill 완료 후 거래일 캘린더 기준으로 정제 예정.")

st.subheader("최종 비교 Scorecard — 아직 빈 칸이 정상")
score = pd.DataFrame([
    {"engine":x["engine"],"trades":None,"win_rate":None,"expectancy_R":None,"PF":None,"MDD":None,"cost_net":None,"OOS":None,"rank":None}
    for x in STRATEGIES
])
st.dataframe(score, width="stretch", hide_index=True)
st.caption("각 엔진이 원본 재현 gate를 통과할 때만 이 표에 실측값을 채웁니다. 실패한 근사 구현값을 원전략 점수로 기록하지 않습니다.")
