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

    block=r'''@app.get('/api/v5/symbol-validate/{market}/{query}')
async def validate_v5_symbol(market:str,query:str):
    market=str(market or '').upper().strip()
    q=str(query or '').strip().upper()
    if market not in ('USA','KOREA'):
        raise HTTPException(status_code=400,detail='market must be USA or KOREA')
    if not q:
        raise HTTPException(status_code=400,detail='symbol required')

    if market=='USA':
        import re as _re
        if not _re.fullmatch(r'[A-Z][A-Z0-9.\\-]{0,9}',q):
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

    import re as _re
    if not _re.fullmatch(r'\\d{6}',q):
        return {'ok':False,'valid':False,'market':market,'query':q,'reason':'KOREA_REQUIRES_6_DIGIT_CODE'}

    # Real Kiwoom validation: a valid listed code must be accepted even when it is
    # absent from the local tracker/history cache or the market is closed.
    try:
        snap=await asyncio.to_thread(_v5_korea_quote_snapshot,q)
    except Exception as e:
        return {'ok':False,'valid':False,'market':market,'query':q,
                'reason':'SYMBOL_NOT_CONFIRMED','detail':str(e)[:180]}
    if not snap.get('valid'):
        return {'ok':False,'valid':False,'market':market,'query':q,
                'reason':'SYMBOL_NOT_CONFIRMED','detail':snap.get('error')}
    return {'ok':True,'valid':True,'market':market,'symbol':q,
            'name':snap.get('name') or q,'price':snap.get('price') or 0,
            'source':snap.get('source') or 'KIWOOM'}

@app.get('/api/v5/korea-quote/{symbol}')
async def v5_korea_quote(symbol:str):
    q=str(symbol or '').strip().upper()
    import re as _re
    if not _re.fullmatch(r'\\d{6}',q):
        return {'ok':False,'valid':False,'symbol':q,'reason':'KOREA_REQUIRES_6_DIGIT_CODE'}
    try:
        return await asyncio.to_thread(_v5_korea_quote_snapshot,q)
    except Exception as e:
        return {'ok':False,'valid':False,'symbol':q,'error':str(e)}

'''

    helper=r'''def _v5_pick_scalar(obj, keys):
    if isinstance(obj,dict):
        for k in keys:
            if k in obj and obj.get(k) not in (None,''):
                return obj.get(k)
        for v in obj.values():
            x=_v5_pick_scalar(v,keys)
            if x not in (None,''):
                return x
    elif isinstance(obj,list):
        for v in obj:
            x=_v5_pick_scalar(v,keys)
            if x not in (None,''):
                return x
    return None

def _v5_num(v):
    try:
        return abs(float(str(v).replace(',','').replace('+','').strip()))
    except Exception:
        return 0.0

def _v5_korea_quote_snapshot(code):
    code=str(code or '').strip().upper()
    q=korea.quote(code)
    raw=(q or {}).get('raw') or {}
    name=_v5_pick_scalar(raw,('stk_nm','stock_name','name')) or code
    price=_v5_num(_v5_pick_scalar(raw,('cur_prc','cur_price','current_price','last','close')))
    source='KIWOOM_KA10004'
    # Some ka10004 responses are order-book centric and may omit cur_prc.
    # In that case use the latest actual 1-minute close. This is also useful
    # after market close because it returns the last recorded trade price.
    if price<=0:
        try:
            bars=korea.minute_chart(code,1,1)
            latest=(bars or {}).get('latest') or {}
            price=_v5_num(latest.get('close'))
            if price>0:
                source='KIWOOM_KA10080_LAST_CLOSE'
        except Exception:
            pass
    return {'ok':True,'valid':True,'market':'KOREA','symbol':code,
            'name':str(name).strip() or code,'price':price,'source':source,
            'checked_at':(q or {}).get('checked_at')}

'''

    anchor="@app.get('/api/v5/symbol-validate/{market}/{query}')"
    if '_v5_korea_quote_snapshot' not in s:
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: symbol validate anchor')
        s=s.replace(anchor,helper+anchor,1)

    pat=r"@app.get\('/api/v5/symbol-validate/\{market\}/\{query\}'\).*?(?=@app.get\('/api/v5/holding-profile/\{market\}/\{symbol\}'\))"
    s=replace_once(s,pat,block,'replace symbol validation')
    API.write_text(s)


def patch_app():
    s=APP.read_text()

    old=r'''def quote_snapshot(symbol):
    x=api(f'/api/quote/{str(symbol).upper()}',5)
    return x if isinstance(x,dict) and not x.get('error') else {}
'''
    new=r'''@st.cache_data(ttl=20,show_spinner=False)
def quote_snapshot(symbol,market='USA'):
    sym=str(symbol or '').upper().strip()
    path=f'/api/v5/korea-quote/{sym}' if market=='KOREA' else f'/api/quote/{sym}'
    x=api(path,8)
    return x if isinstance(x,dict) and not x.get('error') and x.get('ok',True) else {}
'''
    if old in s:
        s=s.replace(old,new,1)
    elif "def quote_snapshot(symbol):" in s:
        s=replace_once(s,r'def quote_snapshot\(symbol\):.*?\n(?=def standby_candidates)',new,'quote snapshot')

    # Standby candidates are USA today, but make the helper market-aware so
    # KOREA can reuse it later without calling the wrong endpoint.
    s=s.replace('q=quote_snapshot(sym)\n',"q=quote_snapshot(sym,market)\n")

    # Holdings price fallback must use the Korea resolver for Korean codes.
    s=s.replace('quote=quote_snapshot(sym) if not live else {}',
                'quote=quote_snapshot(sym,market) if not live else {}')

    # Improve registration guidance now that real Kiwoom validation is used.
    s=s.replace('실제 존재가 확인된 종목만 등록됩니다. 국장은 현재 6자리 종목코드 기준으로 검증합니다.',
                '실제 상장 여부를 확인한 종목만 등록됩니다. 국장은 6자리 코드를 Kiwoom으로 직접 검증합니다.')

    APP.write_text(s)


def main():
    patch_api()
    patch_app()
    print('PREOPEN_KOREA_HOLDINGS_RESOLVER_V07_OK')

if __name__=='__main__':
    main()
