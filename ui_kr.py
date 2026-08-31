import requests
import pandas as pd
import streamlit as st


def _get(api_url: str, path: str, timeout: int = 20):
    try:
        r = requests.get(str(api_url).rstrip('/') + path, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _money(v):
    try:
        return f"{float(v):,.0f}원"
    except Exception:
        return '-'


def render_kr_app(api_url: str):
    """KOREA-only UI entrypoint.

    This module must never import or mutate USA runtime/engine state.
    It reads only KOREA endpoints and is intentionally safe while the KR
    execution bridge remains signal-only/order_placement=False.
    """
    st.markdown('### 🇰🇷 국장 단타')
    gate = _get(api_url, '/api/v5/market-gate/KOREA', 15)
    entry = _get(api_url, '/api/v5/daytrade-entry/KOREA?limit=20&eval_limit=10', 60)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric('세션', 'OPEN' if gate.get('regular_open') else 'CLOSED')
    c2.metric('실시간', str(gate.get('pulse_status') or '-'))
    c3.metric('후보', entry.get('candidate_count', 0))
    c4.metric('진입 READY', entry.get('ready_count', 0))

    if not gate.get('ok'):
        st.error(f"KR Market Gate 오류: {gate.get('error') or gate}")
    elif gate.get('regular_open') and gate.get('pulse_status') != 'LIVE':
        st.warning(f"국장 정규장인데 실시간 pulse가 LIVE가 아닙니다: {gate.get('pulse_status')}")
    elif not gate.get('regular_open'):
        st.info('현재 장 시작 전/장 종료 상태입니다. 주문은 비활성 상태입니다.')

    rows = entry.get('rows') or []
    table = []
    for i, r in enumerate(rows, 1):
        d = r.get('v22_entry') or {}
        table.append({
            '#': i,
            '종목': r.get('name') or r.get('symbol') or '-',
            '코드': r.get('symbol') or '-',
            '현재가': _money(r.get('price') or r.get('current_price')),
            'V22E 점수': round(float(d.get('score')), 1) if d.get('score') is not None else '-',
            '판단': 'BUY' if d.get('enter') else '관찰',
            '사유': d.get('reason') or '-',
            '최종봉': d.get('bar_time') or '-',
        })

    left, right = st.columns([1.05, 1.35], gap='large')
    with left:
        st.markdown('#### 실시간 단타 후보 TOP 20')
        if table:
            st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True, height=520)
        else:
            st.info('현재 국장 후보 데이터가 없습니다.')

    with right:
        st.markdown('#### 국장 V22 상태')
        st.write({
            'gate_open': gate.get('gate_open'),
            'pulse_status': gate.get('pulse_status'),
            'regular_open': gate.get('regular_open'),
            'evaluated_count': entry.get('evaluated_count'),
            'ready_count': entry.get('ready_count'),
            'signal_only': entry.get('signal_only'),
            'order_placement': entry.get('order_placement'),
        })
        if entry.get('signal_only') is True and entry.get('order_placement') is False:
            st.success('KR V22는 현재 신호 전용 / 주문 OFF 상태입니다.')
        elif entry.get('order_placement'):
            st.warning('KR 주문 실행이 활성화되어 있습니다.')
        if rows:
            labels = [f"{r.get('symbol')} · {(r.get('v22_entry') or {}).get('score', '-')}" for r in rows]
            pick = st.selectbox('후보 선택', labels, key='kr_isolated_pick')
            r = rows[labels.index(pick)]
            st.json({
                'symbol': r.get('symbol'),
                'name': r.get('name'),
                'price': r.get('price') or r.get('current_price'),
                'v22_entry': r.get('v22_entry'),
                'entry_ready': r.get('entry_ready'),
                'session': r.get('session'),
            })

    st.caption('KR UI ISOLATED · 이 화면은 USA 엔진/상태/자동매매 스위치를 읽거나 변경하지 않습니다.')
