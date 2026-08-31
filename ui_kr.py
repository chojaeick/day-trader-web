import os
import requests
import pandas as pd
import streamlit as st


def _api_base(api_url: str) -> str:
    base = str(api_url or '').strip().rstrip('/')
    if not base:
        base = str(os.getenv('DAYTRADER_API_URL') or '').strip().rstrip('/')
    if not base:
        base = 'http://127.0.0.1:8000'
    return base


def _get(api_url: str, path: str, timeout: int = 20):
    try:
        r = requests.get(_api_base(api_url) + path, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _money(v):
    try:
        return f"{float(v):,.0f}원"
    except Exception:
        return '-'


def _score(v):
    try:
        return f"{float(v):.1f}"
    except Exception:
        return '-'


def render_kr_trading(api_url: str, render_positions_fn=None):
    """KOREA-only Trading body inside the shared V5 shell.

    This module reads only KOREA V5 endpoints.  USA runtime/engine/order state
    is never imported or mutated here.  The shell/header/mode controls remain
    owned by app_v5.py.
    """
    base = _api_base(api_url)
    gate = _get(base, '/api/v5/market-gate/KOREA', 15)
    entry = _get(base, '/api/v5/daytrade-entry/KOREA?limit=20&eval_limit=10', 60)

    rows = entry.get('rows') or []
    pulse = str(gate.get('pulse_status') or '-')
    regular = bool(gate.get('regular_open'))
    signal_only = bool(entry.get('signal_only'))
    order_on = bool(entry.get('order_placement'))

    if not gate.get('ok'):
        st.error(f"KR Market Gate 오류: {gate.get('error') or gate}")
    elif regular and pulse != 'LIVE':
        st.warning(f'국장 정규장 · 실시간 pulse {pulse} · 주문 OFF 유지')
    elif not regular:
        st.caption(f'● CLOSED · {pulse} · 신호전용 {"ON" if signal_only else "OFF"} · 주문 {"ON" if order_on else "OFF"}')

    if not entry.get('ok'):
        st.error(f"KR Entry 오류: {entry.get('error') or entry}")

    table = []
    for i, r in enumerate(rows, 1):
        d = r.get('v22_entry') or {}
        table.append({
            '#': i,
            '종목': r.get('name') or r.get('symbol') or '-',
            '현재가': _money(r.get('price') or r.get('current_price')),
            'V22E 점수': _score(d.get('score')),
            '상태': 'BUY' if d.get('enter') else '관찰',
        })

    left, right = st.columns([1.05, 1.35], gap='large')
    selected = None

    with left:
        live_badge = '● LIVE' if regular and pulse == 'LIVE' else '● STANDBY'
        st.markdown('### ⚡ 실시간 단타 후보 TOP 20')
        st.caption(f'{live_badge} · 후보를 선택하면 오른쪽에 V22E 상세 평가가 표시됩니다.')
        if table:
            st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True, height=520)
            labels, lookup = [], {}
            for r in rows:
                d = r.get('v22_entry') or {}
                label = f"{r.get('symbol') or '-'} · {r.get('name') or '-'} · V22E {_score(d.get('score'))}"
                labels.append(label)
                lookup[label] = r
            pick = st.selectbox('후보 선택', labels, key='kr_shared_pick', label_visibility='collapsed')
            selected = lookup.get(pick)
        else:
            st.info('현재 국장 후보 데이터가 없습니다.')

    with right:
        st.markdown('### 🎯 선택 종목 상세')
        if selected:
            d = selected.get('v22_entry') or {}
            symbol = selected.get('symbol') or '-'
            name = selected.get('name') or symbol
            price = selected.get('price') or selected.get('current_price')
            score = d.get('score')
            action = 'BUY' if d.get('enter') else '관찰'

            h1, h2, h3 = st.columns([1.2, 1.0, .8])
            h1.markdown(f'## {name}')
            h1.caption(symbol)
            h2.markdown(f'## {_money(price)}')
            h2.caption('현재가')
            h3.metric('V22E', _score(score), action)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric('현재가', _money(price))
            m2.metric('V22E', _score(score))
            m3.metric('Gate', 'OPEN' if gate.get('gate_open') else 'CLOSED')
            m4.metric('주문', 'ON' if order_on else 'OFF')

            c1, c2 = st.columns(2)
            with c1:
                st.markdown('#### 진입 조건 요약')
                st.write(f'• V22E 점수: {_score(score)}')
                st.write(f'• Gate: {"OPEN" if gate.get("gate_open") else "CLOSED"}')
                st.write(f'• Pulse: {pulse}')
                st.write(f'• 신호 전용: {"ON" if signal_only else "OFF"}')
            with c2:
                st.markdown('#### 엔진 근거 (요약)')
                st.write(f'• {d.get("reason") or "평가 근거 대기"}')
                st.write(f'• 엔진: {d.get("engine") or "ENGINE5_V22_KR_LIVE"}')
                st.write(f'• 최종봉: {d.get("bar_time") or "-"}')
                st.write(f'• 평가상태: {"READY" if selected.get("entry_ready") else "WATCH"}')

            with st.expander('상세 엔진 평가 보기', expanded=False):
                st.json({
                    'symbol': symbol,
                    'score': score,
                    'enter': d.get('enter'),
                    'reason': d.get('reason'),
                    'engine': d.get('engine'),
                    'bar_time': d.get('bar_time'),
                    'entry_ready': selected.get('entry_ready'),
                    'gate_open': gate.get('gate_open'),
                    'pulse_status': pulse,
                    'signal_only': signal_only,
                    'order_placement': order_on,
                })
        else:
            st.info('왼쪽 후보에서 종목을 선택하세요.')

    st.divider()
    if callable(render_positions_fn):
        render_positions_fn('KOREA', rows)
    else:
        st.markdown('### 🛡 보유주식 관리')
        st.caption('공통 보유주식 관리 모듈 연결 대기')
