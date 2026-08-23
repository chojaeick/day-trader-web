from pathlib import Path

APP=Path('app_v5.py')

OLD="""        quote=quote_snapshot(sym,market) if not live else {}\n        p=normalize_position(raw,live or quote);shown+=1\n"""
NEW="""        quote=quote_snapshot(sym,market) if not live else {}\n        # V31: if the domestic spot quote is temporarily empty, reuse the\n        # latest broker-backed daily/monthly close as a display/evaluation fallback.\n        # This avoids losing current price just because ka10004/1m snapshot is sparse.\n        if market=='KOREA' and not live:\n            qpx=first_value(quote,'price','current_price','last_price') if isinstance(quote,dict) else None\n            if qpx in (None,'',0,0.0):\n                try:\n                    mh=api(f'/api/v5/monthly-history/KOREA/{sym}',25)\n                    months=list(mh.get('months') or []) if isinstance(mh,dict) else []\n                    if months:\n                        last=months[-1] or {}\n                        px=last.get('close')\n                        if px not in (None,'',0,0.0):\n                            quote={'ok':True,'market':'KOREA','symbol':sym,'price':px,'current_price':px,'source':'KIWOOM_KA10081_LAST_CLOSE'}\n                except Exception:\n                    pass\n        p=normalize_position(raw,live or quote);shown+=1\n"""

def main():
    s=APP.read_text()
    if NEW in s:
        print('LONGTERM_CURRENT_PRICE_FALLBACK_V31_OK')
        return
    if OLD not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: render_positions quote block')
    s=s.replace(OLD,NEW,1)
    s=s.replace('class="v24-ver">v28</span>','class="v24-ver">v31</span>',1)
    APP.write_text(s)
    print('LONGTERM_CURRENT_PRICE_FALLBACK_V31_OK')

if __name__=='__main__':
    main()
