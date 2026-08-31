import pandas as pd
import streamlit as st


def render_portfolio_page(market, *, get_market_status, tracker_rows, render_positions):
    st.markdown('### 💼 개인 보유종목 관리')
    st.caption('KR/US 공통 프레임 · 현재 선택한 시장의 보유종목만 표시합니다.')
    status = get_market_status(market) or {}
    tracker = tracker_rows(status) or []
    render_positions(market, tracker)


def render_daily_history_page(market, *, get_market_status):
    st.markdown('### 🧾 일별 매매내역')
    st.caption('현재 런타임이 보유한 당일 이벤트를 시장별로 분리해 표시합니다. 실제 체결 원장은 별도 broker ledger 연결 전까지 체결내역으로 간주하지 않습니다.')
    status = get_market_status(market) or {}
    events = status.get('events') or []
    rows = []
    for e in events:
        if not isinstance(e, dict):
            continue
        rows.append({
            '시간': e.get('at') or e.get('time') or e.get('timestamp') or e.get('created_at') or '-',
            '종목': e.get('symbol') or '-',
            '이벤트': e.get('event_type') or e.get('type') or e.get('state') or '-',
            '가격': e.get('price') or e.get('current_price') or '-',
            '수량': e.get('qty') or e.get('quantity') or '-',
            '사유': e.get('reason') or e.get('detail') or e.get('message') or '-',
        })
    c1, c2, c3 = st.columns(3)
    c1.metric('시장', '국장' if market == 'KOREA' else '미장')
    c2.metric('당일 이벤트', len(rows))
    c3.metric('체결원장', '연결대기')
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=560)
    else:
        st.info('현재 저장된 당일 런타임 이벤트가 없습니다.')


def render_longterm_search_page(market, *, get_market_status, tracker_rows, finder_rows, search_symbol_ui, validate_symbol_ui, quote_snapshot, money):
    st.markdown('### 🔎 중장기 종목 탐색')
    st.caption('시장별 종목 검색 + 현재 후보 풀 확인. 중장기 전용 스코어 엔진은 다음 단계에서 이 페이지에 연결합니다.')

    q = st.text_input('종목 검색', placeholder='삼성전자 / 005930' if market == 'KOREA' else 'AAPL / MSFT', key=f'lt_search_{market}').strip()
    if q:
        hits = search_symbol_ui(market, q) if market == 'KOREA' else []
        if market == 'KOREA' and hits:
            labels = [f"{x.get('name') or x.get('symbol')} · {x.get('symbol')}" for x in hits]
            chosen = st.selectbox('검색 결과', labels, key=f'lt_pick_{market}')
            hit = hits[labels.index(chosen)]
            symbol = str(hit.get('symbol') or '').upper()
        else:
            symbol = q.upper()
        check = validate_symbol_ui(market, symbol)
        if check.get('valid'):
            symbol = str(check.get('symbol') or symbol).upper()
            quote = quote_snapshot(symbol, market) or {}
            a, b, c = st.columns(3)
            a.metric('종목', check.get('name') or symbol)
            b.metric('코드', symbol)
            c.metric('현재가', money(quote.get('price') or quote.get('last') or quote.get('close'), market))
        else:
            st.warning(f"종목 확인 실패: {check.get('reason') or check.get('error') or '미확인'}")

    status = get_market_status(market) or {}
    pool = tracker_rows(status) or finder_rows(status) or []
    out = []
    for r in pool[:20]:
        if not isinstance(r, dict):
            continue
        out.append({
            '종목': r.get('name') or r.get('symbol') or '-',
            '코드': r.get('symbol') or '-',
            '현재가': money(r.get('price') or r.get('current_price'), market),
            'Power': r.get('power') if r.get('power') is not None else '-',
            '상태': r.get('state') or (r.get('entry_gate') or {}).get('signal_grade') or '-',
            '위험': r.get('risk') or r.get('risk_level') or '-',
        })
    st.markdown('#### 현재 시장 후보 풀')
    if out:
        st.dataframe(pd.DataFrame(out), use_container_width=True, hide_index=True, height=420)
    else:
        st.info('현재 후보 풀이 없습니다.')
