import pandas as pd
import streamlit as st


def render_portfolio_page(market, *, get_market_status, tracker_rows, render_positions):
    st.markdown('### 💼 개인 보유종목 관리')
    st.caption('KR/US 공통 프레임 · 현재 선택한 시장의 보유종목만 표시합니다.')
    status = get_market_status(market) or {}
    tracker = tracker_rows(status) or []
    # IMPORTANT: Streamlit renders every tab in one script pass. Trading already
    # renders the same positions component with scope='trading', so Portfolio
    # must use its own widget-key namespace to avoid DuplicateElementKey.
    render_positions(market, tracker, scope='portfolio')


def _trade_event_type(e):
    return str(e.get('event_type') or e.get('type') or e.get('state') or '').upper().strip()


def _is_trade_event(e):
    t = _trade_event_type(e)
    if not t:
        return False
    keep = ('BUY', 'SELL', 'ORDER', 'FILL', 'FILLED', 'EXEC', 'ENTRY', 'EXIT', 'TP1', 'STOP', 'CANCEL', 'PARTIAL')
    return any(k in t for k in keep)


def render_daily_history_page(market, *, get_market_status):
    st.markdown('### 🧾 일별 매매내역')
    st.caption('진단 신호는 제외하고 주문·체결·진입·청산 계열 이벤트만 표시합니다. 실제 broker 체결원장은 별도 연결 전까지 확정 체결로 간주하지 않습니다.')
    status = get_market_status(market) or {}
    events = [e for e in (status.get('events') or []) if isinstance(e, dict)]
    trade_events = [e for e in events if _is_trade_event(e)]
    rows = []
    for e in trade_events:
        rows.append({
            '시간': e.get('at') or e.get('time') or e.get('timestamp') or e.get('created_at') or '-',
            '종목': e.get('symbol') or '-',
            '구분': _trade_event_type(e) or '-',
            '가격': e.get('fill_price') or e.get('price') or e.get('current_price') or '-',
            '수량': e.get('filled_qty') or e.get('qty') or e.get('quantity') or '-',
            '사유': e.get('reason') or e.get('detail') or e.get('message') or '-',
        })
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('시장', '국장' if market == 'KOREA' else '미장')
    c2.metric('매매 이벤트', len(rows))
    c3.metric('진단 이벤트 제외', max(0, len(events) - len(trade_events)))
    c4.metric('체결원장', '연결대기')
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=520)
    else:
        st.info('현재 표시할 주문/체결/진입/청산 이벤트가 없습니다.')
    with st.expander('런타임 진단 이벤트 보기', expanded=False):
        if events:
            diag = []
            for e in events[:100]:
                diag.append({
                    '시간': e.get('at') or e.get('time') or e.get('timestamp') or e.get('created_at') or '-',
                    '종목': e.get('symbol') or '-',
                    '이벤트': _trade_event_type(e) or '-',
                    '사유': e.get('reason') or e.get('detail') or e.get('message') or '-',
                })
            st.dataframe(pd.DataFrame(diag), use_container_width=True, hide_index=True, height=320)
        else:
            st.caption('진단 이벤트 없음')


def render_longterm_search_page(market, *, get_market_status, tracker_rows, finder_rows, search_symbol_ui, validate_symbol_ui, quote_snapshot, money):
    st.markdown('### 🔎 중장기 종목 탐색')
    st.caption('시장별 종목 검색과 현재 단타 후보를 참고용으로 분리 표시합니다. 중장기 전용 스코어는 별도 엔진 연결 후 이 페이지에 추가합니다.')

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
            st.info('중장기 평가엔진 연결 전입니다. 현재가는 조회용이며 단타 Power/상태를 중장기 추천으로 해석하지 않습니다.')
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
            '단타 Power': r.get('power') if r.get('power') is not None else '-',
            '단타 상태': r.get('state') or (r.get('entry_gate') or {}).get('signal_grade') or '-',
            '단타 위험': r.get('risk') or r.get('risk_level') or '-',
        })
    st.markdown('#### 현재 단타 후보 풀 · 참고용')
    if out:
        st.dataframe(pd.DataFrame(out), use_container_width=True, hide_index=True, height=420)
    else:
        st.info('현재 후보 풀이 없습니다.')
