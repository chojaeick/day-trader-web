from pathlib import Path
import re

API = Path('live_server/api.py')
APP = Path('app_v5.py')


def must_replace(text, old, new, label):
    if old not in text:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return text.replace(old, new, 1)


def patch_api():
    s = API.read_text()

    if 'import sqlite3' not in s:
        s = s.replace('import os\n', 'import os\nimport sqlite3\n', 1)

    # NORMAL must be a true standby mode. Streaming/websocket tasks remain alive,
    # but the expensive V4 finder/tracker loop is skipped entirely.
    anchor = "            profile=_runtime_profile()\n"
    standby = anchor + """            if profile['mode']=='NORMAL':
                await asyncio.sleep(profile['loop_seconds'])
                continue
"""
    if "if profile['mode']=='NORMAL':" not in s:
        s = must_replace(s, anchor, standby, 'normal standby gate')

    # Persistent holding classification is deliberately kept separate from the
    # legacy v4_positions table so old live/shadow ledger semantics are untouched.
    if '/api/v5/holding-profile/' not in s:
        endpoint_anchor = """@app.post('/api/v4/runtime-mode/{mode}')
async def set_runtime_mode(mode:str):
    m=str(mode or '').upper()
    if m not in ('NORMAL','DAYTRADE'):
        raise HTTPException(status_code=400,detail='mode must be NORMAL or DAYTRADE')
    runtime_mode['mode']=m
    runtime_mode['updated_at']=datetime.now(timezone.utc).isoformat()
    logging.warning('V4 runtime mode changed to %s',m)
    return {'ok':True,**_runtime_profile(),'updated_at':runtime_mode['updated_at']}
"""
        profile_api = endpoint_anchor + """

def _ensure_v5_holding_profile_table():
    con=sqlite3.connect(s.db_path,timeout=15)
    try:
        con.execute('''CREATE TABLE IF NOT EXISTS v5_holding_profiles(
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            holding_type TEXT NOT NULL DEFAULT 'SHORT_TERM',
            source TEXT NOT NULL DEFAULT 'MANUAL',
            updated_at TEXT,
            PRIMARY KEY(market,symbol)
        )''')
        con.commit()
    finally:
        con.close()


def _get_holding_profile(market:str,symbol:str):
    _ensure_v5_holding_profile_table()
    con=sqlite3.connect(s.db_path,timeout=15)
    con.row_factory=sqlite3.Row
    try:
        row=con.execute('SELECT * FROM v5_holding_profiles WHERE market=? AND symbol=?',
                        (market.upper(),symbol.upper())).fetchone()
        return dict(row) if row else {
            'market':market.upper(),'symbol':symbol.upper(),
            'holding_type':'SHORT_TERM','source':'LEGACY','updated_at':None
        }
    finally:
        con.close()


@app.get('/api/v5/holding-profile/{market}/{symbol}')
async def get_holding_profile(market:str,symbol:str):
    return {'ok':True,**_get_holding_profile(market,symbol)}


@app.post('/api/v5/holding-profile')
async def set_holding_profile(payload:dict):
    market=str(payload.get('market') or '').upper().strip()
    symbol=str(payload.get('symbol') or '').upper().strip()
    holding_type=str(payload.get('holding_type') or 'SHORT_TERM').upper().strip()
    source=str(payload.get('source') or 'MANUAL').upper().strip()
    if not market or not symbol:
        raise HTTPException(status_code=400,detail='market and symbol required')
    if holding_type not in ('SHORT_TERM','LONG_TERM'):
        raise HTTPException(status_code=400,detail='holding_type must be SHORT_TERM or LONG_TERM')
    _ensure_v5_holding_profile_table()
    now=datetime.now(timezone.utc).isoformat()
    con=sqlite3.connect(s.db_path,timeout=15)
    try:
        con.execute('''INSERT INTO v5_holding_profiles(market,symbol,holding_type,source,updated_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(market,symbol) DO UPDATE SET
                         holding_type=excluded.holding_type,
                         source=excluded.source,
                         updated_at=excluded.updated_at''',
                    (market,symbol,holding_type,source,now))
        con.commit()
    finally:
        con.close()
    return {'ok':True,**_get_holding_profile(market,symbol)}
"""
        s = must_replace(s, endpoint_anchor, profile_api, 'holding profile endpoints')

    API.write_text(s)


def patch_app():
    s = APP.read_text()

    # Helper for persistent SHORT_TERM/LONG_TERM classification.
    helper_anchor = "def recommendation_table(rows,market,limit=5):\n"
    if 'def holding_profile(' not in s:
        helper = """def holding_profile(market,symbol):
    x=api(f'/api/v5/holding-profile/{market}/{symbol}',5)
    return str(x.get('holding_type') or 'SHORT_TERM').upper()

def set_holding_profile(market,symbol,holding_type):
    return post('/api/v5/holding-profile',{
        'market':market,'symbol':symbol,
        'holding_type':holding_type,'source':'MANUAL'
    },5)

"""
        s = must_replace(s, helper_anchor, helper + helper_anchor, 'holding profile ui helper')

    manual_new = r'''def render_manual_holding(market,scope='trading'):
    with st.expander('➕ 보유주식 등록',expanded=False):
        c1,c2,c3,c4,c5=st.columns([1.25,.7,1.0,1.05,.72])
        symbol=c1.text_input('종목',placeholder='SOXL / 005930',key=f'msym_{market}_{scope}').strip().upper()
        qty=c2.number_input('수량',min_value=0,value=0,step=1,key=f'mqty_{market}_{scope}')
        avg=c3.number_input('평단',min_value=0.0,value=0.0,step=100.0 if market=='KOREA' else 0.01,key=f'mavg_{market}_{scope}')
        kind_label=c4.selectbox('구분',['단타','중장기'],key=f'mkind_{market}_{scope}')
        holding_type='SHORT_TERM' if kind_label=='단타' else 'LONG_TERM'
        if c5.button('등록',disabled=(not symbol or qty<=0 or avg<=0),key=f'mreg_{market}_{scope}',use_container_width=True):
            result=post('/api/v4/position/buy',{'market':market,'symbol':symbol,'qty':int(qty),'price':float(avg),'note':'V5 manual holding registration'})
            if result.get('ok'):
                p=set_holding_profile(market,symbol,holding_type)
                if p.get('ok'):
                    st.success(f'{symbol} · {kind_label} 등록 완료')
                    st.rerun()
                else:
                    st.warning(f'보유등록은 완료, 구분 저장 실패: {p}')
            else:
                st.error(f"등록 실패: {result.get('error') or result}")

'''
    pattern = re.compile(r"def render_manual_holding\(.*?\n(?=def render_buy_box)", re.S)
    if not pattern.search(s):
        raise SystemExit('PATCH_TARGET_NOT_FOUND: render_manual_holding')
    s = pattern.sub(manual_new, s, count=1)

    positions_new = r'''def render_positions(market,tracker):
    head,add=st.columns([3.0,1.0])
    head.markdown('### 🛡 실제 보유 종목')
    head.caption('한 줄 요약 · 단타/중장기 즉시 변경 · 상세 엔진 평가는 펼쳐보기')
    with add:
        render_manual_holding(market,'trading')

    pos_rows,_=position_rows()
    shown=0
    for raw in pos_rows:
        if str(raw.get('market') or '').upper() not in {'',market}:
            continue
        sym=raw.get('symbol') or (raw.get('position') or {}).get('symbol') or '-'
        live=next((r for r in tracker if str(r.get('symbol')).upper()==str(sym).upper()),None)
        p=normalize_position(raw,live)
        shown+=1
        current_type=holding_profile(market,sym)
        current_label='단타' if current_type=='SHORT_TERM' else '중장기'
        pct_text=f'{p["pct"]:+.2f}%' if p['pct'] is not None else '-'
        judgement=action_ko(action_of(live)) if live else ('중장기 평가대기' if current_type=='LONG_TERM' else '미커버')

        c0,c1,c2,c3,c4,c5,c6,c7=st.columns([1.15,.9,1.0,1.0,.9,1.05,1.15,.55])
        c0.markdown(f'**{sym}**  \\n{p["qty"]:,.0f}주')
        c1.markdown(f'현재  \\n**{money(p["cur"],market,"-")}**')
        c2.markdown(f'평단  \\n**{money(p["avg"],market,"-")}**')
        c3.markdown(f'손익  \\n**{money(p["pnl"],market,"-")}**')
        c4.markdown(f'수익률  \\n**{pct_text}**')
        c5.markdown(f'판단  \\n**{judgement}**')
        selected=c6.selectbox('투자구분',['단타','중장기'],index=0 if current_type=='SHORT_TERM' else 1,key=f'kind_{market}_{sym}',label_visibility='collapsed')
        if selected!=current_label:
            if c6.button('변경 적용',key=f'kind_apply_{market}_{sym}',use_container_width=True):
                new_type='SHORT_TERM' if selected=='단타' else 'LONG_TERM'
                rr=set_holding_profile(market,sym,new_type)
                if rr.get('ok'):
                    st.rerun()
                else:
                    st.error(f'구분 변경 실패: {rr}')
        remove=c7.button('삭제',key=f'del_{market}_{sym}',use_container_width=True)
        if remove:
            close_px=p['avg'] or p['cur'] or 1.0
            result=post('/api/v4/position/sell',{'market':market,'symbol':sym,'qty':p['qty'],'price':close_px,'note':'V5 MANUAL LEDGER REMOVE - ZERO PNL CORRECTION'})
            if result.get('ok'):
                st.success(f'{sym} 장부 제거 완료')
                st.rerun()
            else:
                st.error(f"삭제 실패: {result.get('error') or result}")

        meta=f'Floor {money(p["floor"],market)} · Warning {money(p["warning_floor"],market)} · Ceiling {money(p["ceiling"],market)} · T1 {money(p["t1"],market)} · T2 {money(p["t2"],market)}'
        st.caption(meta)
        with st.expander(f'{sym} 엔진별 상세 평가',expanded=False):
            if current_type=='SHORT_TERM':
                st.dataframe(engine_matrix(live),hide_index=True,use_container_width=True)
            else:
                st.info('중장기 보유: 단타 엔진 점수를 최종 판단에 사용하지 않습니다. 중장기 전용 일봉/주봉·추세·펀더멘털 평가 엔진 연결 예정입니다.')
                st.dataframe(engine_matrix(live),hide_index=True,use_container_width=True)
        st.divider()

    if shown==0:
        st.info('등록된 실제 보유종목이 없습니다.')

'''
    pattern2 = re.compile(r"def render_positions\(.*?\n(?=def )", re.S)
    m=pattern2.search(s)
    if not m:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: render_positions')
    s = s[:m.start()] + positions_new + s[m.end():]

    APP.write_text(s)


if __name__=='__main__':
    patch_api()
    patch_app()
    print('PREOPEN_STABILIZE_PATCH_V02_OK')
