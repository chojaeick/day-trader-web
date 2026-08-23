from pathlib import Path

API=Path('live_server/api.py')

HELPER=r'''
# ===== V34 USA DAILY HISTORY PAGINATION =====
def _v34_us_daily_rows(symbol,days=900,max_pages=12):
    from datetime import timedelta as _td
    symbol=str(symbol or '').upper().strip()
    ex=k.active_exchange(symbol)
    start=(datetime.now(timezone.utc)-_td(days=int(days))).strftime('%Y%m%d')
    rows=[]; next_key=''; pages=0
    while pages<max_pages:
        hdr=k.headers('usa06012')
        if next_key:
            hdr['cont-yn']='Y'
            hdr['next-key']=next_key
        r=requests.post(
            s.rest_base+'/api/us/chart',
            headers=hdr,
            json={'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'},
            timeout=25,
        )
        d=r.json()
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"usa06012 {symbol}: {d.get('return_code')} {d.get('return_msg')}")
        raw=d.get('result_list') or d.get('data') or []
        if isinstance(raw,dict):
            raw=list(raw.values())
        rows.extend(x for x in raw if isinstance(x,dict))
        pages+=1
        cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
        next_key=r.headers.get('next-key') or r.headers.get('Next-Key') or ''
        if cont!='Y' or not next_key:
            break
    return rows,pages
'''


def main():
    s=API.read_text()
    if 'V34 USA DAILY HISTORY PAGINATION' not in s:
        anchor='# ===== V33 LONG-TERM WEEKLY HISTORY FALLBACK =====\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: v33 anchor')
        pos=s.index(anchor)
        s=s[:pos]+HELPER+'\n'+s[pos:]

    old_month="""        if market=='USA':\n            ex=k.active_exchange(symbol)\n            from datetime import timedelta as _td\n            start=(datetime.now(timezone.utc)-_td(days=500)).strftime('%Y%m%d')\n            r=requests.post(s.rest_base+'/api/us/chart',headers=k.headers('usa06012'),json={\n                'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'\n            },timeout=25)\n            d=r.json()\n            if d.get('return_code') not in (None,0):\n                raise RuntimeError(f\"usa06012 {symbol}: {d.get('return_code')} {d.get('return_msg')}\")\n            mon=_v28_monthly_from_daily(d.get('result_list') or [])\n            return {'ok':len(mon)>=10,'market':market,'symbol':symbol,'source':'KIWOOM_USA06012','months':mon[-24:],'count':len(mon)}\n"""
    new_month="""        if market=='USA':\n            rows,pages=_v34_us_daily_rows(symbol,days=1200,max_pages=16)\n            mon=_v28_monthly_from_daily(rows)\n            return {'ok':len(mon)>=10,'market':market,'symbol':symbol,'source':'KIWOOM_USA06012_PAGED','months':mon[-36:],'count':len(mon),'pages':pages}\n"""
    if old_month in s:
        s=s.replace(old_month,new_month,1)
    elif "'source':'KIWOOM_USA06012_PAGED'" not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: monthly USA branch')

    old_week="""        if market=='USA':\n            ex=k.active_exchange(symbol)\n            from datetime import timedelta as _td\n            start=(datetime.now(timezone.utc)-_td(days=550)).strftime('%Y%m%d')\n            r=requests.post(s.rest_base+'/api/us/chart',headers=k.headers('usa06012'),json={\n                'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'\n            },timeout=25)\n            d=r.json()\n            if d.get('return_code') not in (None,0):\n                raise RuntimeError(f\"usa06012 {symbol}: {d.get('return_code')} {d.get('return_msg')}\")\n            weeks=_v33_weekly_from_daily(d.get('result_list') or [])\n            return {'ok':len(weeks)>=10,'market':market,'symbol':symbol,'source':'KIWOOM_USA06012_WEEKLY','weeks':weeks[-80:],'count':len(weeks)}\n"""
    new_week="""        if market=='USA':\n            rows,pages=_v34_us_daily_rows(symbol,days=900,max_pages=12)\n            weeks=_v33_weekly_from_daily(rows)\n            return {'ok':len(weeks)>=10,'market':market,'symbol':symbol,'source':'KIWOOM_USA06012_WEEKLY_PAGED','weeks':weeks[-100:],'count':len(weeks),'pages':pages}\n"""
    if old_week in s:
        s=s.replace(old_week,new_week,1)
    elif "'source':'KIWOOM_USA06012_WEEKLY_PAGED'" not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: weekly USA branch')

    API.write_text(s)
    print('LONGTERM_USA_HISTORY_PAGINATION_V34_OK')

if __name__=='__main__':
    main()
