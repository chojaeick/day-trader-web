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


def render_kr_trading(api_url: str):
    """KOREA-only Trading body for the shared V5 shell.

    No USA runtime, engine, broker, state, or order module is imported here.
    The common header / KR-US switch / mode frame remains owned by app_v5.py.
    """
    base = _api_base(api_url)
    gate = _get(base, '/api/v5/market-gate/KOREA', 15)
    entry = _get(base, '/api/v5/daytrade-entry/KOREA?limit=20&eval_limit=10', 60)

    rows = entry.get('rows') or []
    evaluated = int(entry.get('evaluated_count') or 0)
    ready = int(entry.get('ready_count') or 0)
    pulse = str(gate.get('pulse_status') or '-')
    session = 'REGULAR' if gate.get('regular_open') else 'CLOSED'

    k1, k2, k3, k4 = st.columns(4)
    k1.metric('세션', session)
    k2.metric('실시간', pulse)
    k3.metric('후보', int(entry.get('candidate_count') or len(rows)))
    k4.metric('진입 READY', ready)

    if not gate.get('ok'):
        st.error(f"KR Market Gate 오류: {gate.get('error') or gate}")
    elif gate.get('regular_open') and pulse != 'LIVE':
        st.warning(f'국장 정규장인데 실시간 pulse가 LIVE가 아닙니다: {pulse}')
    elif not gate.get('regular_open'):
        st.info('현재 장 시작 전/장 종료 상태입니다. 주문은 비활성 상태입니다.')

    if not entry.get('ok'):
        st.error(f"KR Entry 오류: {entry.get('error') or entry}")

    table = []
    for i, r in enumerate(rows, 1):
        d = r.get('v22_entry') or {}
        table.append({
            '#': i,
            '종목': r.get('name') or r.get('symbol') or '-',
            '코드': r.get('symbol') or '-',
            '현재가': _money(r.get('price') or r.get('current_price')),
            'V22E 점수': round(float(d.get('score')), 1) if d.get('score') is not None else '-',
            '상태': 'BUY' if d.get('enter') else '관찰',
        })

    left, right = st.columns([1.0, 1.3], gap='large')
    selected = None
    with left:
        st.markdown('### ⚡ 실시간 단타 후보 TOP 20')
        st.caption('후보를 선택하면 오른쪽에 V22E 상세 판단이 표시됩니다.')
        if table:
            st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True, height=520)
            labels = []
            lookup = {}
            for r in rows:
                d = r.get('v22_entry') or {}
                label = f"{r.get('symbol') or '-'} · {r.get('name') or '-'} · V22E {d.get('score', '-')}"
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

            c1, c2, c3 = st.columns([1.2, 1.0, .8])
            c1.markdown(f'## {name}')
            c1.caption(symbol)
            c2.markdown(f'## {_money(price)}')
            c2.caption('현재가')
            c3.metric('V22E', f'{float(score):.1f}' if score is not None else '-', action)

            st.divider()
            s1, s2, s3, s4 = st.columns(4)
            s1.metric('엔진', str(d.get('engine') or 'ENGINE5_V22_KR_LIVE').replace('ENGINE5_', ''))
            s2.metric('판단', action)
            s3.metric('최종봉', str(d.get('bar_time') or '-'))
            s4.metric('평가상태', 'READY' if selected.get('entry_ready') else 'WATCH')

            st.markdown('#### 진입 조건 요약')
            st.write(f"• V22E 점수: {float(score):.1f}" if score is not None else '• V22E 점수: -')
            st.write(f"• 엔진 판단: {d.get('reason') or '-'}")
            st.write(f"• Gate: {'OPEN' if gate.get('gate_open') else 'CLOSED'} / Pulse: {pulse}")
            st.write(f"• 신호 전용: {entry.get('signal_only')} / 주문 실행: {entry.get('order_placement')}")
        else:
            st.info('왼쪽 후보에서 종목을 선택하세요.')

    st.divider()
    st.markdown('### 🛡 국장 단타 운영 상태')
    o1, o2, o3, o4 = st.columns(4)
    o1.metric('평가 종목', evaluated)
    o2.metric('READY', ready)
    o3.metric('신호 전용', 'ON' if entry.get('signal_only') else 'OFF')
    o4.metric('주문', 'ON' if entry.get('order_placement') else 'OFF')
    st.caption(f'KR API {base} · KR UI MODULE · USA runtime/engine/order state untouched')
