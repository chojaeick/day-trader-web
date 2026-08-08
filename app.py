import os
import time
import streamlit as st
from dotenv import load_dotenv
from trader.config import TradingConfig
from trader.demo import demo_candidates, demo_bars
from trader.screener import rank_candidates
from trader.signals import intraday_signal, position_signal
from trader.notifier import send_kakao_to_me

load_dotenv()
cfg = TradingConfig()

st.set_page_config(page_title='DAY TRADER WEB', page_icon='📈', layout='wide', initial_sidebar_state='collapsed')

st.markdown('''
<style>
html, body, [class*="css"] { font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.block-container { padding-top: 1.2rem; max-width: 1500px; }
.hero {display:flex; justify-content:space-between; align-items:center; padding:18px 22px; border:1px solid rgba(128,128,128,.2); border-radius:18px; margin-bottom:14px;}
.hero h1 {margin:0;font-size:1.9rem;}
.hero .sub {opacity:.68;font-size:.9rem;}
.badge {display:inline-block;padding:6px 10px;border-radius:999px;background:rgba(128,128,128,.13);font-weight:700;font-size:.8rem;margin-right:6px;}
.signal {padding:16px 18px;border-radius:16px;border:1px solid rgba(128,128,128,.2);font-size:1.05rem;margin:8px 0 14px 0;}
.small {opacity:.65;font-size:.85rem;}
div[data-testid="stMetric"] {border:1px solid rgba(128,128,128,.18);padding:12px 14px;border-radius:14px;}
[data-testid="stDataFrame"] {border-radius:14px; overflow:hidden;}
</style>
''', unsafe_allow_html=True)

if 'selected' not in st.session_state: st.session_state.selected = 'SOXL'
if 'entry' not in st.session_state: st.session_state.entry = 0.0
if 'qty_krw' not in st.session_state: st.session_state.qty_krw = 15_000_000

st.markdown('''<div class="hero"><div><h1>DAY TRADER WEB</h1><div class="sub">TOP10 → 1·5분봉 Signal → Position → Critical Alert</div></div><div><span class="badge">DEMO DATA</span><span class="badge">NO AUTO ORDER</span></div></div>''', unsafe_allow_html=True)

market = st.radio('시장', ['NASDAQ', '한국'], horizontal=True, label_visibility='collapsed')
if market == '한국':
    st.info('한국시장 실시간 데이터 어댑터는 다음 단계에서 KIS/키움 국내 API와 연결합니다. 현재 데모는 NASDAQ 종목으로 표시합니다.')

# Demo market regime
c1,c2,c3,c4 = st.columns(4)
c1.metric('NASDAQ Bias','BULL 74')
c2.metric('Semiconductor','STRONG')
c3.metric('Market Mode','TREND')
c4.metric('Data','DEMO')

st.subheader('오늘의 단타 후보 TOP 10')
ranked = rank_candidates(demo_candidates(), cfg)
ranked = ranked.copy()
ranked.insert(0, 'rank', range(1, len(ranked)+1))
show = ranked[['rank','symbol','score','bias','price','day_pct','premarket_pct','rvol','dollar_volume','atr_pct']].rename(columns={
    'rank':'순위','symbol':'종목','score':'점수','bias':'방향','price':'현재가','day_pct':'당일%','premarket_pct':'프리%','rvol':'RVOL','dollar_volume':'거래대금','atr_pct':'ATR%'
})
st.dataframe(show, use_container_width=True, hide_index=True, height=390)

# Three checkpoints for recommendation persistence, demo-derived
st.caption('추천 유지도 (V1 데모): 장 -10분 → -1분 → 개장 +7분')
checkpoint = ranked[['symbol','score']].head(5).copy()
checkpoint['-10분'] = (checkpoint['score'] - 3).clip(lower=0)
checkpoint['-1분'] = (checkpoint['score'] - 1).clip(lower=0)
checkpoint['+7분'] = checkpoint['score']
st.dataframe(checkpoint[['symbol','-10분','-1분','+7분']].rename(columns={'symbol':'종목'}), use_container_width=True, hide_index=True)

symbols = ranked['symbol'].tolist()
def_idx = symbols.index(st.session_state.selected) if st.session_state.selected in symbols else 0
selected = st.selectbox('오늘 집중 감시 종목', symbols, index=def_idx)
st.session_state.selected = selected

st.divider()
st.subheader(f'{selected} 집중 감시')
bars = demo_bars(selected)
sig = intraday_signal(selected, bars, market_bias=.7, cfg=cfg)

m1,m2,m3,m4,m5 = st.columns(5)
m1.metric('상태', sig.state)
m2.metric('Signal', f'{sig.score}/100')
m3.metric('현재가', f'${sig.price:,.2f}')
m4.metric('Bias', sig.bias)
m5.metric('Hard Stop', f'{cfg.hard_stop_pct:.1f}%')

status_icon = {'TRIGGER':'🚨','SETUP':'🟡','WATCH':'👀','WAIT':'⏳'}.get(sig.state,'•')
st.markdown(f'<div class="signal"><b>{status_icon} {selected} · {sig.state}</b><br><span class="small">{sig.reason}</span><br><br>기술적 무효화 <b>${sig.invalidation:,.2f}</b> &nbsp; · &nbsp; 1차 참고 <b>${sig.target1:,.2f}</b> &nbsp; · &nbsp; 2차 참고 <b>${sig.target2:,.2f}</b></div>', unsafe_allow_html=True)

left,right = st.columns([2,1])
with left:
    st.caption('1분봉 가격 (Demo)')
    st.line_chart(bars.set_index('time')['close'], height=300)
with right:
    st.markdown('**V1 확인 항목**')
    for item in ['VWAP','EMA 9 / 20 / 50','RVOL','RSI','20봉 Breakout','시장/섹터 동조']:
        st.write('✓', item)
    st.caption('5분봉 Confirmation은 실시간 데이터 어댑터 연결 시 1분봉 집계로 동시 계산합니다.')

st.subheader('포지션 등록')
p1,p2,p3 = st.columns(3)
entry = p1.number_input('실제 체결가', min_value=0.0, value=float(st.session_state.entry), step=0.01)
amount = p2.number_input('진입금액 (원)', min_value=0, value=int(st.session_state.qty_krw), step=100000)
p3.metric('기본 분할', '50% → 30% → 20%')
st.session_state.entry = entry
st.session_state.qty_krw = amount

if entry > 0:
    ps = position_signal(selected, bars, entry, cfg)
    pnl = (ps.price/entry - 1)*100
    st.success(f'{ps.state} | 현재 ${ps.price:.2f} | 수익률 {pnl:+.2f}% | {ps.reason}')
    if ps.critical:
        st.warning('Critical Alert 조건: 카카오톡/푸시 알림 대상으로 분류됩니다.')
else:
    st.caption('카카오페이에서 주문한 뒤 체결가만 입력하면 Position Mode로 전환됩니다.')

with st.expander('알림 테스트 / 설정'):
    st.write('웹 화면에는 WATCH/SETUP/TRIGGER/ADD/HOLD/TRIM/EXIT를 모두 표시하고, 카카오톡은 TRIGGER·STOP 위험·강한 익절·급등락·중요뉴스만 보내는 정책입니다.')
    if st.button('현재 신호 카카오톡 테스트'):
        ok,msg = send_kakao_to_me(f'[{selected}] {sig.state} / Score {sig.score} / ${sig.price:.2f}\n{sig.reason}')
        (st.success if ok else st.error)(msg)

st.divider()
st.caption('V1 Web Prototype · 현재 Demo 데이터. 다음 단계: Kiwoom/KIS 실시간 WebSocket + 1분/5분 Signal + News + 서버 알림. 매매 자동주문은 포함하지 않습니다.')
