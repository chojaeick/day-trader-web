from pathlib import Path
import re

APP=Path('app_v5.py')
API=Path('live_server/api.py')


def replace_once(s,pat,repl,label,flags=re.S):
    m=re.search(pat,s,flags)
    if not m:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return s[:m.start()]+repl+s[m.end():]


def patch_api():
    s=API.read_text()
    if "/api/v5/korea-symbol-search" not in s:
        anchor="@app.get('/api/v5/symbol-validate/{market}/{query}')"
        insert=r'''@app.get('/api/v5/korea-symbol-search')
async def v5_korea_symbol_search(q:str,limit:int=12):
    q=str(q or '').strip().upper()
    if not q:
        return {'ok':True,'rows':[]}
    rows=[]; seen=set()
    # Prefer in-memory Kiwoom metadata/discovery because it already carries names.
    pools=[]
    try:
        pools.append(list((getattr(korea,'stock_meta',{}) or {}).values()))
    except Exception:
        pass
    try:
        pools.append((getattr(korea,'discovery',{}) or {}).get('rows') or [])
    except Exception:
        pass
    try:
        st=v4.status('KOREA') if hasattr(v4,'status') else {}
        pools.append(((st.get('finder') or {}).get('rows') or [])+((st.get('tracker') or {}).get('rows') or []))
    except Exception:
        pass
    for pool in pools:
        for r in pool:
            if not isinstance(r,dict):
                continue
            sym=str(r.get('symbol') or r.get('stk_cd') or '').upper().strip()
            name=str(r.get('name') or r.get('stk_nm') or '').strip()
            if not sym or sym in seen:
                continue
            hay=(sym+' '+name.upper())
            if q not in hay:
                continue
            seen.add(sym)
            rows.append({'symbol':sym,'name':name or sym})
            if len(rows)>=max(1,min(int(limit),30)):
                break
        if len(rows)>=max(1,min(int(limit),30)):
            break
    # If user entered an exact code, always try live validation even if metadata cache is cold.
    if len(q)==6 and re.fullmatch(r'[0-9A-Z]{6}',q) and q not in seen:
        try:
            snap=await asyncio.to_thread(_v5_korea_quote_snapshot,q)
            if snap.get('valid'):
                rows.insert(0,{'symbol':q,'name':snap.get('name') or q})
        except Exception:
            pass
    return {'ok':True,'rows':rows[:max(1,min(int(limit),30))]}

'''
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: symbol search anchor')
        s=s.replace(anchor,insert+anchor,1)
    API.write_text(s)


def patch_app():
    s=APP.read_text()

    if 'def search_symbol_ui(' not in s:
        anchor='def validate_symbol_ui(market,query):\n'
        helper=r'''def search_symbol_ui(market,query):
    q=str(query or '').strip()
    if not q:return []
    if market=='KOREA':
        x=api(f'/api/v5/korea-symbol-search?q={requests.utils.quote(q)}&limit=12',8)
        return x.get('rows') or [] if isinstance(x,dict) else []
    # USA: keep symbol-first until a dedicated company-name directory is wired.
    return []

'''
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: search helper anchor')
        s=s.replace(anchor,helper+anchor,1)

    # Replace manual registration with search-first UX for Korea.
    manual=r'''def render_manual_holding(market,scope='holdings'):
    with st.expander('＋ 보유주식 등록',expanded=False):
        st.caption('국장은 종목명 또는 종목코드로 검색할 수 있습니다. 실제 상장 종목만 등록됩니다.')
        a,b,c,d=st.columns([1.35,.65,.9,.8])
        raw=a.text_input('종목 검색',placeholder='삼성전자 / KODEX 미국S&P500 / 005930 / 0193T0',key=f'msym_{market}_{scope}').strip()
        qty=b.number_input('수량',min_value=0,value=0,step=1,key=f'mqty_{market}_{scope}')
        avg=c.number_input('평균매수가',min_value=0.0,value=0.0,step=100.0 if market=='KOREA' else 0.01,key=f'mavg_{market}_{scope}')
        kind=d.selectbox('투자유형',['단타','중장기'],key=f'mkind_{market}_{scope}')

        selected_symbol=''; selected_name=''
        if market=='KOREA' and raw:
            rows=search_symbol_ui(market,raw)
            if rows:
                labels=[f"{r.get('name') or r.get('symbol')}  ·  {r.get('symbol')}" for r in rows]
                chosen=st.selectbox('검색 결과',labels,key=f'mpick_{market}_{scope}')
                hit=rows[labels.index(chosen)]
                selected_symbol=str(hit.get('symbol') or '').upper()
                selected_name=str(hit.get('name') or selected_symbol)
        elif raw:
            selected_symbol=raw.upper()

        check=validate_symbol_ui(market,selected_symbol) if selected_symbol else {'valid':False}
        if selected_symbol:
            if check.get('valid'):
                resolved_name=(check.get('name') or selected_name or selected_symbol)
                st.success(f"확인됨 · {resolved_name} · {selected_symbol}")
            else:
                reason=check.get('reason') or check.get('error') or '미확인 종목'
                st.error(f'등록 불가 · {reason}')
        enabled=bool(check.get('valid') and qty>0 and avg>0)
        if st.button('확인된 종목을 보유주식으로 등록',type='primary',disabled=not enabled,key=f'mreg_{market}_{scope}',use_container_width=True):
            symbol=str(check.get('symbol') or selected_symbol).upper()
            result=post('/api/v4/position/buy',{'market':market,'symbol':symbol,'qty':int(qty),'price':float(avg),'note':'V5 verified manual holding registration'})
            if result.get('ok'):
                p=set_holding_profile(market,symbol,'SHORT_TERM' if kind=='단타' else 'LONG_TERM')
                if p.get('ok'): st.success(f'{symbol} 등록 완료'); st.rerun()
                else: st.warning(f'종목 등록 완료, 투자유형 저장 실패: {p}')
            else: st.error(f"등록 실패: {result.get('error') or result}")

'''
    s=replace_once(s,r'def render_manual_holding\(.*?\n(?=def render_buy_box)',manual,'manual holding search')

    # Add a tiny display-name resolver and use it in holdings rows.
    if 'def holding_display_name(' not in s:
        anchor='def render_positions(market,tracker):\n'
        helper=r'''@st.cache_data(ttl=300,show_spinner=False)
def holding_display_name(market,symbol):
    sym=str(symbol or '').upper().strip()
    if market=='KOREA':
        rows=search_symbol_ui('KOREA',sym)
        for r in rows:
            if str(r.get('symbol') or '').upper()==sym:
                return r.get('name') or sym
        q=quote_snapshot(sym,'KOREA')
        nm=q.get('name') if isinstance(q,dict) else None
        return nm or sym
    q=quote_snapshot(sym,'USA')
    return (q.get('name') if isinstance(q,dict) else None) or sym

'''
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: holding display anchor')
        s=s.replace(anchor,helper+anchor,1)

    # Replace the first holdings symbol-only markdown with name-first rendering.
    s=s.replace("c0.markdown(f'**{sym}**  \\n<span style=\"color:#8190a7;font-size:.7rem\">{p[\"qty\"]:,.0f}주</span>',unsafe_allow_html=True)",
                "display_name=holding_display_name(market,sym)\n        c0.markdown(f'**{display_name}**  \\n<span style=\"color:#8190a7;font-size:.68rem\">{sym} · {p[\"qty\"]:,.0f}주</span>',unsafe_allow_html=True)",1)

    APP.write_text(s)


def main():
    patch_api()
    patch_app()
    print('PREOPEN_UI_SEARCH_AND_NAMES_V13_OK')

if __name__=='__main__':
    main()
