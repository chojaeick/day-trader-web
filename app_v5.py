import os
import requests
import pandas as pd
import streamlit as st

try:
    API_URL = st.secrets.get('DAYTRADER_API_URL', '')
except Exception:
    API_URL = os.getenv('DAYTRADER_API_URL', '')
API_URL = str(API_URL).rstrip('/')

st.set_page_config(page_title='DAY TRADER V5', page_icon='📈', layout='wide', initial_sidebar_state='collapsed')
st.markdown('''
<style>
.block-container{padding-top:1.1rem;max-width:1500px}
[data-testid="stMetricValue"]{font-size:1.55rem}
.v5-card{border:1px solid #30363d;border-radius:14px;padding:16px 18px;margin:8px 0 14px 0;background:#11151b}
.v5-action{font-size:1.35rem;font-weight:800}
.v5-muted{opacity:.72;font-size:.92rem}
.v5-kicker{font-size:.78rem;letter-spacing:.08em;opacity:.65}
</style>
''', unsafe_allow_html=True)


def api(path, timeout=10):
    try:
        r = requests.get(API_URL + path, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def money(v, market):
    x = f(v)
    if market == 'KOREA':
        return f'{x:,.0f}원'
    return f'${x:,.2f}'


def action_of(row):
    proto = str(row.get('prototype_action') or row.get('proto_action') or '').upper()
    state = str(row.get('state') or '').upper()
    grade = str((row.get('entry_gate') or {}).get('signal_grade') or '').upper()
    if proto:
        return proto
    if state in {'HARD_EXIT','EXIT_READY'}:
        return 'EXIT_REVIEW'
    if state == 'PARTIAL_EXIT':
        return 'REDUCE_REVIEW'
    if grade in {'ENTRY','ENTRY_CANDIDATE'} or state == 'ENTRY':
        return 'BUY_REVIEW'
    if grade in {'READY','READY_STRONG'} or state in {'READY','SETUP'}:
        return 'WAIT'
    if state == 'HOLD':
        return 'HOLD'
    return 'WATCH'


def action_ko(a):
    return {
        'BUY_REVIEW':'매수 검토', 'ADD_REVIEW':'추가매수 검토', 'HOLD':'보유',
        'WAIT':'대기', 'WATCH':'관찰', 'REDUCE_REVIEW':'비중축소 검토',
        'EXIT_REVIEW':'매도 검토', 'AVOID':'회피', 'DATA_WAIT':'데이터 대기'
    }.get(str(a), str(a))


def get_market_status(market):
    return api(f'/api/v4/{market}/status', timeout=15)


def tracker_rows(status):
    return (status.get('tracker') or {}).get('rows') or []


def finder_rows(status):
    finder = status.get('finder') or {}
    return finder.get('rows') or status.get('finder_rows') or []


def recommendation_table(rows, market, limit=5):
    out = []
    for r in rows[:limit]:
        gate = r.get('entry_gate') or {}
        out.append({
            '종목': r.get('symbol') or '-',
            '종목명': r.get('name') or r.get('symbol') or '-',
            '판단': action_ko(action_of(r)),
            '현재가': money(r.get('price') or r.get('current_price'), market),
            'Power': round(f(r.get('power')), 1),
            '상태': r.get('state') or gate.get('signal_grade') or '-',
            '위험': r.get('risk') or r.get('risk_level') or '-',
        })
    return pd.DataFrame(out)


def render_trading(market):
    status = get_market_status(market)
    rows = tracker_rows(status)
    finders = finder_rows(status)
    session = status.get('session') or status.get('market_session') or '-'

    c1,c2,c3,c4 = st.columns(4)
    c1.metric('시장', market)
    c2.metric('세션', session)
    c3.metric('추천 후보', len(finders))
    c4.metric('실시간 관리', len(rows))

    st.subheader('⚡ 지금 단타 후보')
    st.caption('엔진 내부 디버그 값보다 최종 행동 판단을 먼저 보여줍니다. 실제 주문은 수동입니다.')
    source = rows if rows else finders
    if source:
        st.dataframe(recommendation_table(source, market), use_container_width=True, hide_index=True)
        for r in source[:3]:
            symbol = r.get('symbol') or '-'
            name = r.get('name') or symbol
            act = action_of(r)
            price = r.get('price') or r.get('current_price')
            power = f(r.get('power'))
            reason = r.get('prototype_reason') or r.get('reason') or r.get('core_reason') or '세부 판단 근거는 엔진 데이터 연결 후 표시'
            st.markdown(f'''<div class="v5-card"><div class="v5-kicker">SHORT TERM · {market}</div><div class="v5-action">{action_ko(act)} · {name} ({symbol})</div><div>현재가 <b>{money(price, market)}</b> · Power <b>{power:+.1f}</b></div><div class="v5-muted">{reason}</div></div>''', unsafe_allow_html=True)
            with st.expander(f'매수 등록 / 상세 · {symbol}'):
                x1,x2 = st.columns(2)
                x1.number_input('실제 매수가', min_value=0.0, value=max(f(price),0.0), key=f'px_{market}_{symbol}')
                x2.number_input('투입 금액', min_value=0.0, value=0.0, step=100000.0 if market=='KOREA' else 100.0, key=f'amt_{market}_{symbol}')
                st.info('V5 Phase 1에서는 입력 UI만 제공합니다. 기존 포지션 API와 안전하게 연결한 뒤 저장 버튼을 활성화합니다.')
    else:
        st.info('현재 추천/Tracker 데이터가 없습니다.')

    st.subheader('📈 중장기 후보')
    st.info('Phase 1 골격: 단타와 완전히 분리합니다. 장기 엔진/월봉·기본정보 연결 후 적립 구간과 목표 비중을 표시합니다.')

    st.subheader('🛡 투자중 단타 관리')
    positions = api('/api/v4/positions', timeout=10)
    pos_rows = positions.get('rows') if isinstance(positions, dict) else None
    if pos_rows:
        for p in pos_rows:
            if str(p.get('market') or '').upper() not in {'', market}:
                continue
            sym = p.get('symbol') or '-'
            avg = p.get('avg_price') or p.get('entry_price')
            qty = p.get('qty') or p.get('quantity')
            st.markdown(f'''<div class="v5-card"><div class="v5-kicker">ACTIVE POSITION</div><div class="v5-action">{sym} · {qty or '-'}주</div><div>평단 {money(avg, market)} · Floor / Ceiling / T1 / T2는 기존 Position Intelligence 연결 예정</div></div>''', unsafe_allow_html=True)
    else:
        st.caption('등록된 포지션이 없거나 포지션 API 연결을 확인해야 합니다.')


def render_portfolio():
    st.header('💼 Portfolio')
    st.caption('실제 자산을 기록하고 일별 Snapshot을 DB에 누적하는 화면입니다.')
    c1,c2,c3,c4 = st.columns(4)
    c1.metric('총 자산', 'DB 연결 예정')
    c2.metric('현금', '-')
    c3.metric('주식', '-')
    c4.metric('오늘 손익', '-')
    st.info('Phase 2에서 holdings / portfolio_daily_snapshots 테이블과 API를 추가합니다.')


def render_briefing():
    st.header('📰 Market Briefing')
    st.caption('매일 07:00 생성한 하나의 브리핑을 앱과 카카오가 함께 사용합니다.')
    st.subheader('오늘 시장 한눈에 보기')
    st.info('KOSPI · KOSDAQ · S&P500 · Nasdaq · Dow · USD/KRW · 핵심 뉴스 · 인기 테마 · 보유종목 뉴스를 연결할 예정입니다.')


def render_settings():
    st.header('⚙️ Settings')
    st.subheader('카카오 알림')
    c1,c2 = st.columns(2)
    with c1:
        st.toggle('📰 07:00 Morning Brief', value=True, disabled=True)
        st.toggle('⚡ 단타 BUY / ADD', value=True, disabled=True)
        st.toggle('🚨 긴급 EXIT / 손절', value=True, disabled=True)
    with c2:
        st.toggle('📈 중장기 추천', value=True, disabled=True)
        st.toggle('🛡 보유종목 중요 변화', value=True, disabled=True)
        st.toggle('📊 일일 자산 결산', value=False, disabled=True)
    st.caption('Phase 3에서 DB 저장 및 기존 Kakao send_text 모듈과 연결합니다.')


st.title('📈 DAY TRADER V5')
st.caption('DECISION TERMINAL · 무엇을 살지 → 얼마를 살지 → 어떻게 관리할지 · MANUAL ORDER')

market = st.radio('시장', ['USA','KOREA'], horizontal=True, key='v5_market')
tab_trade, tab_port, tab_news, tab_settings, tab_debug = st.tabs(['⚡ Trading','💼 Portfolio','📰 Market Briefing','⚙️ Settings','🧪 Legacy / Debug'])

with tab_trade:
    render_trading(market)
with tab_port:
    render_portfolio()
with tab_news:
    render_briefing()
with tab_settings:
    render_settings()
with tab_debug:
    st.warning('기존 V4 Validation / Archive / Shadow 진단 기능은 삭제하지 않습니다. 정상 V5 흐름과 분리해 유지합니다.')
    st.code('streamlit run app.py  # existing V4 UI')

st.divider()
st.caption('DAY TRADER V5 PROTOTYPE · EXISTING ENGINE REUSE · NO AUTO ORDER')
