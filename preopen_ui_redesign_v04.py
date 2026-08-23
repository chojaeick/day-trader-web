from pathlib import Path
import re

APP=Path('app_v5.py')
API=Path('live_server/api.py')

def replace_once(s,pat,repl,label,flags=re.S):
    m=re.search(pat,s,flags)
    if not m: raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return s[:m.start()]+repl+s[m.end():]

def patch_api():
    s=API.read_text()
    if '/api/v5/symbol-validate/' not in s:
        anchor="@app.get('/api/v5/holding-profile/{market}/{symbol}')"
        insert=r'''@app.get('/api/v5/symbol-validate/{market}/{query}')
async def validate_v5_symbol(market:str,query:str):
    market=str(market or '').upper().strip()
    q=str(query or '').strip().upper()
    if market not in ('USA','KOREA'):
        raise HTTPException(status_code=400,detail='market must be USA or KOREA')
    if not q:
        raise HTTPException(status_code=400,detail='symbol required')

    # Integrity first: do not allow arbitrary free text into the position ledger.
    if market=='USA':
        import re as _re
        if not _re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',q):
            return {'ok':False,'valid':False,'market':market,'query':q,'reason':'INVALID_US_SYMBOL_FORMAT'}
        row=db.quote(q) or {}
        if not row:
            try:
                ex=await asyncio.to_thread(k.active_exchange,q)
                await asyncio.to_thread(k.quote,q,ex)
                row=db.quote(q) or {}
            except Exception:
                row={}
        price=float(row.get('price') or row.get('last') or row.get('close') or 0)
        if price<=0:
            return {'ok':False,'valid':False,'market':market,'query':q,'reason':'SYMBOL_NOT_CONFIRMED'}
        return {'ok':True,'valid':True,'market':market,'symbol':q,'name':row.get('name') or q,'price':price}

    # Korea: strict 6-digit code until name-search resolver is explicitly verified.
    import re as _re
    if not _re.fullmatch(r'\d{6}',q):
        return {'ok':False,'valid':False,'market':market,'query':q,'reason':'KOREA_REQUIRES_6_DIGIT_CODE'}
    # Existing DB/tracker/position evidence is enough to confirm known codes without heavy discovery.
    evidence=False; name=q; price=0.0
    try:
        st=v4.status('KOREA') if hasattr(v4,'status') else {}
        pool=((st.get('finder') or {}).get('rows') or [])+((st.get('tracker') or {}).get('rows') or [])+(st.get('positions') or [])
        hit=next((r for r in pool if str(r.get('symbol') or '')==q),None)
        if hit:
            evidence=True; name=hit.get('name') or q; price=float(hit.get('price') or hit.get('current_price') or 0)
    except Exception:
        pass
    if not evidence:
        try:
            con=sqlite3.connect(s.db_path,timeout=5)
            row=con.execute("SELECT 1 FROM daily_history WHERE symbol=? LIMIT 1",(q,)).fetchone()
            con.close(); evidence=bool(row)
        except Exception:
            pass
    if not evidence:
        return {'ok':False,'valid':False,'market':market,'query':q,'reason':'SYMBOL_NOT_CONFIRMED'}
    return {'ok':True,'valid':True,'market':market,'symbol':q,'name':name,'price':price}

'''
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: validate endpoint anchor')
        s=s.replace(anchor,insert+anchor,1)
    API.write_text(s)

def patch_app():
    s=APP.read_text()
    # richer dashboard CSS, compact and consistent
    css_add=r'''
<style>
:root{--v5-bg:#08111f;--v5-panel:#0d1726;--v5-line:#20324a;--v5-text:#eaf2ff;--v5-muted:#8292aa;--v5-blue:#2788ff;--v5-green:#20d87a;--v5-red:#ff4d5e;--v5-amber:#ffb020}
.block-container{max-width:1680px!important;padding:1.0rem 1.35rem 1.2rem!important}
.v5-title{font-size:2.35rem!important;font-weight:900!important;letter-spacing:-.04em;margin:0!important}
.v5-sub{color:#8492a8!important;font-size:.78rem!important}
[data-testid="stHorizontalBlock"]{gap:.7rem!important}
[data-testid="stDataFrame"]{border:1px solid #20324a;border-radius:12px;overflow:hidden}
div[data-testid="stExpander"]{border:1px solid #20324a!important;border-radius:12px!important;background:#0b1422!important}
.v5-section{border:1px solid #20324a;border-radius:14px;background:linear-gradient(180deg,#0d1828 0%,#0a1320 100%);padding:14px 16px;margin:5px 0 10px}
.v5-section-title{font-size:1.22rem;font-weight:850;margin-bottom:4px}.v5-section-sub{color:#8190a7;font-size:.72rem}
.v5-kpi{border:1px solid #20324a;border-radius:10px;padding:10px 12px;background:#0b1524;min-height:72px}.v5-kpi-label{color:#8190a7;font-size:.68rem}.v5-kpi-value{font-size:1.1rem;font-weight:800;margin-top:3px}
.v5-good{color:#20d87a}.v5-bad{color:#ff5a69}.v5-warn-t{color:#ffb020}.v5-blue{color:#3d95ff}
.stButton>button{border-radius:9px!important;font-weight:700!important}
[data-baseweb="select"]>div,[data-testid="stNumberInput"] input,[data-testid="stTextInput"] input{border-radius:8px!important}
</style>
'''
    if '--v5-panel:#0d1726' not in s:
        s=s.replace("</style>\n''', unsafe_allow_html=True)","</style>\n''', unsafe_allow_html=True)\nst.markdown('''"+css_add+"''',unsafe_allow_html=True)",1)

    if 'def validate_symbol_ui(' not in s:
        anchor='def recommendation_table(rows,market,limit=5):\n'
        helper=r'''def validate_symbol_ui(market,query):
    q=str(query or '').strip()
    if not q:return {'ok':False,'valid':False,'reason':'EMPTY'}
    return api(f'/api/v5/symbol-validate/{market}/{q}',8)

'''
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: app validate helper')
        s=s.replace(anchor,helper+anchor,1)

    manual=r'''def render_manual_holding(market,scope='holdings'):
    with st.expander('＋ 보유주식 등록',expanded=False):
        st.caption('실제 존재가 확인된 종목만 등록됩니다. 국장은 현재 6자리 종목코드 기준으로 검증합니다.')
        a,b,c,d=st.columns([1.35,.65,.9,.8])
        raw=a.text_input('종목코드',placeholder='SOXL / NVDA / 005930',key=f'msym_{market}_{scope}').strip().upper()
        qty=b.number_input('수량',min_value=0,value=0,step=1,key=f'mqty_{market}_{scope}')
        avg=c.number_input('평균매수가',min_value=0.0,value=0.0,step=100.0 if market=='KOREA' else 0.01,key=f'mavg_{market}_{scope}')
        kind=d.selectbox('투자유형',['단타','중장기'],key=f'mkind_{market}_{scope}')
        check=validate_symbol_ui(market,raw) if raw else {'valid':False}
        if raw:
            if check.get('valid'):
                st.success(f"확인됨 · {check.get('symbol')} · {check.get('name') or check.get('symbol')}")
            else:
                reason=check.get('reason') or check.get('error') or '미확인 종목'
                st.error(f'등록 불가 · {reason}')
        enabled=bool(check.get('valid') and qty>0 and avg>0)
        if st.button('확인된 종목을 보유주식으로 등록',type='primary',disabled=not enabled,key=f'mreg_{market}_{scope}',use_container_width=True):
            symbol=str(check.get('symbol') or raw).upper()
            result=post('/api/v4/position/buy',{'market':market,'symbol':symbol,'qty':int(qty),'price':float(avg),'note':'V5 verified manual holding registration'})
            if result.get('ok'):
                p=set_holding_profile(market,symbol,'SHORT_TERM' if kind=='단타' else 'LONG_TERM')
                if p.get('ok'): st.success(f'{symbol} 등록 완료'); st.rerun()
                else: st.warning(f'종목 등록 완료, 투자유형 저장 실패: {p}')
            else: st.error(f"등록 실패: {result.get('error') or result}")

'''
    s=replace_once(s,r'def render_manual_holding\(.*?\n(?=def render_buy_box)',manual,'manual holding')

    selected=r'''def render_selected_detail(r,market):
    symbol=r.get('symbol') or '-';name=r.get('name') or symbol;reason=r.get('prototype_reason') or r.get('reason') or r.get('core_reason') or '엔진 판단 근거 대기'
    st.markdown('<div class="v5-section-title">🎯 선택 종목 상세</div>',unsafe_allow_html=True)
    a,b,c,d=st.columns(4)
    a.metric('종목',symbol);b.metric('현재가',money(r.get('price') or r.get('current_price'),market));c.metric('Power',f"{f(r.get('power')):+.1f}");d.metric('판단',action_ko(action_of(r)))
    st.markdown(f'<div class="v5-section"><b>{name}</b><div class="v5-section-sub">{reason}</div></div>',unsafe_allow_html=True)
    with st.expander('엔진 평가 요약',expanded=True):
        st.dataframe(engine_matrix(r),use_container_width=True,hide_index=True,height=245)
    with st.expander('매수 계산 / 보유등록',expanded=False): render_buy_box(r,market)

'''
    s=replace_once(s,r'def render_selected_detail\(.*?\n(?=def render_positions)',selected,'selected detail')

    positions=r'''def render_positions(market,tracker):
    st.markdown('<div class="v5-section-title">🛡 보유주식 관리</div><div class="v5-section-sub">전체 폭 관리 · 단타/중장기 즉시 전환 · 검증된 종목만 등록</div>',unsafe_allow_html=True)
    render_manual_holding(market,'holdings')
    pos_rows,_=position_rows(); shown=0
    for raw in pos_rows:
        if str(raw.get('market') or '').upper() not in {'',market}: continue
        sym=raw.get('symbol') or (raw.get('position') or {}).get('symbol') or '-'
        live=next((r for r in tracker if str(r.get('symbol')).upper()==str(sym).upper()),None)
        p=normalize_position(raw,live);shown+=1
        current_type=holding_profile(market,sym); pct='-' if p['pct'] is None else f"{p['pct']:+.2f}%"
        pnl_cls='v5-good' if (p['pnl'] or 0)>=0 else 'v5-bad'
        c0,c1,c2,c3,c4,c5,c6,c7=st.columns([1.1,.9,.9,.75,.82,.9,1.05,.58])
        c0.markdown(f'**{sym}**  \n<span style="color:#8190a7;font-size:.7rem">{p["qty"]:,.0f}주</span>',unsafe_allow_html=True)
        c1.markdown(f'현재가  \n**{money(p["cur"],market,"-")}**')
        c2.markdown(f'평균가  \n**{money(p["avg"],market,"-")}**')
        c3.markdown(f'손익  \n<span class="{pnl_cls}"><b>{money(p["pnl"],market,"-")}</b></span>',unsafe_allow_html=True)
        c4.markdown(f'수익률  \n<span class="{pnl_cls}"><b>{pct}</b></span>',unsafe_allow_html=True)
        c5.markdown(f'판단  \n**{action_ko(action_of(live)) if live else "미커버"}**')
        new_label=c6.selectbox('투자유형',['단타','중장기'],index=0 if current_type=='SHORT_TERM' else 1,key=f'kind_{market}_{sym}',label_visibility='collapsed')
        new_type='SHORT_TERM' if new_label=='단타' else 'LONG_TERM'
        if new_type!=current_type and c6.button('변경',key=f'kind_apply_{market}_{sym}',use_container_width=True):
            rr=set_holding_profile(market,sym,new_type)
            if rr.get('ok'): st.rerun()
            else: st.error(f'구분 변경 실패: {rr}')
        if c7.button('삭제',key=f'del_{market}_{sym}',use_container_width=True):
            close_px=p['avg'] or p['cur'] or 1.0
            rr=post('/api/v4/position/sell',{'market':market,'symbol':sym,'qty':p['qty'],'price':close_px,'note':'V5 manual ledger remove'})
            if rr.get('ok'): st.rerun()
            else: st.error(f"삭제 실패: {rr.get('error') or rr}")
        with st.expander(f'{sym} 상세 엔진 평가',expanded=False):
            st.dataframe(engine_matrix(live),hide_index=True,use_container_width=True,height=220)
        st.divider()
    if shown==0: st.info('등록된 실제 보유종목이 없습니다.')

'''
    s=replace_once(s,r'def render_positions\(.*?\n(?=def render_trading)',positions,'positions')

    trading=r'''def render_trading(market):
    status=get_market_status(market);rows=tracker_rows(status);finders=finder_rows(status);source=rows if rows else finders;session=status.get('session') or status.get('market_session') or '-'
    a,b,c,d=st.columns(4);a.metric('시장','미국장' if market=='USA' else '국장');b.metric('세션',session);c.metric('후보',len(finders));d.metric('관리',len(rows))
    left,right=st.columns([1.05,1.35],gap='large')
    with left:
        st.markdown('<div class="v5-section-title">⚡ 지금 단타 후보 TOP 5</div><div class="v5-section-sub">후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.</div>',unsafe_allow_html=True)
        if source:
            st.dataframe(recommendation_table(source,market),use_container_width=True,hide_index=True,height=225)
            labels=[];lookup={}
            for r in source[:5]:
                label=f"{r.get('symbol') or '-'} · {action_ko(action_of(r))} · Power {f(r.get('power')):+.1f}";labels.append(label);lookup[label]=r
            sel=st.selectbox('후보 선택',labels,key=f'sel_{market}',label_visibility='collapsed')
            selected=lookup[sel]
        else:
            st.info('현재 추천/Tracker 데이터가 없습니다.'); selected=None
    with right:
        if selected: render_selected_detail(selected,market)
        else: st.markdown('<div class="v5-section"><b>선택 종목 없음</b><div class="v5-section-sub">후보가 생성되면 상세 평가가 이 영역에 표시됩니다.</div></div>',unsafe_allow_html=True)
    st.divider()
    render_positions(market,rows)

'''
    s=replace_once(s,r'def render_trading\(.*?\n(?=def render_portfolio)',trading,'trading layout')
    APP.write_text(s)

if __name__=='__main__':
    patch_api();patch_app();print('PREOPEN_UI_REDESIGN_V04_OK')
