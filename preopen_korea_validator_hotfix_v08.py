from pathlib import Path
import re

API=Path('live_server/api.py')


def replace_once(s,pat,repl,label,flags=re.S):
    m=re.search(pat,s,flags)
    if not m:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return s[:m.start()]+repl+s[m.end():]


def main():
    s=API.read_text()

    # Fix stale Korea validation branch that incorrectly rejects valid 6-digit codes.
    pat=r"    # Korea: strict 6-digit code until name-search resolver is explicitly verified\.\n.*?    return \{'ok':True,'valid':True,'market':market,'symbol':q,'name':name,'price':price\}\n"
    repl=r'''    # Korea: verify canonical 6-digit code directly against Kiwoom.
    import re as _re
    if not _re.fullmatch(r'\d{6}',q):
        return {'ok':False,'valid':False,'market':market,'query':q,'reason':'KOREA_REQUIRES_6_DIGIT_CODE'}

    name=q
    price=0.0
    confirmed=False

    # 1) Direct Kiwoom quote call is the primary existence check.
    try:
        kd=await asyncio.to_thread(korea.quote,q)
        raw=(kd or {}).get('raw') or {}
        if (kd or {}).get('ok'):
            confirmed=True
            name=str(raw.get('stk_nm') or raw.get('stk_name') or q).strip() or q
            for key in ('cur_prc','cur_pric','current_price','last','close'):
                try:
                    v=abs(float(str(raw.get(key) or '').replace(',','').replace('+','')))
                    if v>0:
                        price=v; break
                except Exception:
                    pass
    except Exception:
        pass

    # 2) If quote payload is sparse/closed-market, try one-page 1m chart.
    if confirmed and price<=0:
        try:
            md=await asyncio.to_thread(korea.minute_chart,q,1,1)
            bars=(md or {}).get('bars') or []
            if bars:
                price=float((bars[-1] or {}).get('close') or 0)
        except Exception:
            pass

    # 3) Fallback to already-known local evidence only if direct Kiwoom failed.
    if not confirmed:
        try:
            st=v4.status('KOREA') if hasattr(v4,'status') else {}
            pool=((st.get('finder') or {}).get('rows') or [])+((st.get('tracker') or {}).get('rows') or [])+(st.get('positions') or [])
            hit=next((r for r in pool if str(r.get('symbol') or '')==q),None)
            if hit:
                confirmed=True
                name=hit.get('name') or q
                price=float(hit.get('price') or hit.get('current_price') or 0)
        except Exception:
            pass

    if not confirmed:
        return {'ok':False,'valid':False,'market':market,'query':q,'reason':'SYMBOL_NOT_CONFIRMED'}

    return {'ok':True,'valid':True,'market':market,'symbol':q,'name':name,'price':price,'source':'KIWOOM_DIRECT'}
'''

    s=replace_once(s,pat,repl,'korea validation branch')
    API.write_text(s)
    print('PREOPEN_KOREA_VALIDATOR_HOTFIX_V08_OK')


if __name__=='__main__':
    main()
