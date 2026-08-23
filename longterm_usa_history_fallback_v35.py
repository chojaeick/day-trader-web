from pathlib import Path
import re

API=Path('live_server/api.py')

HELPER=r'''
# ===== V35 USA HISTORY ROBUST FALLBACK =====
def _v35_us_daily_rows(symbol,days=1400,max_pages=24):
    from datetime import timedelta as _td
    symbol=str(symbol or '').upper().strip()
    ex=k.active_exchange(symbol)
    start=(datetime.now(timezone.utc)-_td(days=int(days))).strftime('%Y%m%d')
    rows=[]; next_key=''; pages=0; last_meta={}
    while pages<max_pages:
        hdr=k.headers('usa06012')
        if next_key:
            hdr['cont-yn']='Y'; hdr['next-key']=next_key
        r=requests.post(
            s.rest_base+'/api/us/chart',
            headers=hdr,
            json={'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'},
            timeout=25,
        )
        d=r.json(); last_meta=d if isinstance(d,dict) else {}
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"usa06012 {symbol}: {d.get('return_code')} {d.get('return_msg')}")
        raw=d.get('result_list') or d.get('data') or []
        if isinstance(raw,dict): raw=list(raw.values())
        rows.extend(x for x in raw if isinstance(x,dict))
        pages+=1
        cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or d.get('cont_yn') or d.get('cont-yn') or '').upper()
        nk=(r.headers.get('next-key') or r.headers.get('Next-Key') or d.get('next_key') or d.get('next-key') or '')
        next_key=str(nk or '')
        if cont!='Y' or not next_key:
            break
    return rows,pages,last_meta

def _v35_us_history_with_local_fallback(symbol,kind='month'):
    rows,pages,meta=_v35_us_daily_rows(symbol)
    if kind=='month':
        built=_v28_monthly_from_daily(rows)
        if len(built)>=10:
            return built,pages,'KIWOOM_USA06012_PAGED'
    else:
        built=_v33_weekly_from_daily(rows)
        if len(built)>=10:
            return built,pages,'KIWOOM_USA06012_WEEKLY_PAGED'

    # Broker can return only a short window for some US names. Reuse the local
    # daily_history archive when available so evaluation does not become empty.
    try:
        con=sqlite3.connect(s.db_path,timeout=5)
        con.row_factory=sqlite3.Row
        cols=[r[1] for r in con.execute('PRAGMA table_info(daily_history)').fetchall()]
        sym_col=next((x for x in ('symbol','ticker','code') if x in cols),None)
        date_col=next((x for x in ('trade_date','date','day','ts','datetime') if x in cols),None)
        close_col=next((x for x in ('close','close_price','price','last_price') if x in cols),None)
        if sym_col and date_col and close_col:
            q=f'SELECT "{date_col}" as dt, "{close_col}" as cur_prc FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}"'
            local=[dict(r) for r in con.execute(q,(str(symbol).upper(),)).fetchall()]
        else:
            local=[]
        con.close()
    except Exception:
        local=[]

    if local:
        if kind=='month':
            built=_v28_monthly_from_daily(local)
            if len(built)>=10:
                return built,pages,'LOCAL_DB_DAILY_HISTORY'
        else:
            built=_v33_weekly_from_daily(local)
            if len(built)>=10:
                return built,pages,'LOCAL_DB_DAILY_HISTORY_WEEKLY'
    return built,pages,'KIWOOM_USA06012_INSUFFICIENT'
'''


def main():
    s=API.read_text()
    if 'V35 USA HISTORY ROBUST FALLBACK' not in s:
        anchor='# ===== V34 USA DAILY HISTORY PAGINATION =====\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: v34 anchor')
        pos=s.index(anchor)
        s=s[:pos]+HELPER+'\n'+s[pos:]

    pat=r"        if market=='USA':\n            rows,pages=_v34_us_daily_rows\(symbol,days=1200,max_pages=16\)\n            mon=_v28_monthly_from_daily\(rows\)\n            return \{'ok':len\(mon\)>=10,'market':market,'symbol':symbol,'source':'KIWOOM_USA06012_PAGED','months':mon\[-36:\],'count':len\(mon\),'pages':pages\}\n"
    repl="""        if market=='USA':\n            mon,pages,source=_v35_us_history_with_local_fallback(symbol,'month')\n            return {'ok':len(mon)>=10,'market':market,'symbol':symbol,'source':source,'months':mon[-36:],'count':len(mon),'pages':pages}\n"""
    s,n=re.subn(pat,repl,s,count=1)
    if n==0 and "_v35_us_history_with_local_fallback(symbol,'month')" not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: v34 monthly branch')

    pat2=r"        if market=='USA':\n            rows,pages=_v34_us_daily_rows\(symbol,days=900,max_pages=12\)\n            weeks=_v33_weekly_from_daily\(rows\)\n            return \{'ok':len\(weeks\)>=10,'market':market,'symbol':symbol,'source':'KIWOOM_USA06012_WEEKLY_PAGED','weeks':weeks\[-100:\],'count':len\(weeks\),'pages':pages\}\n"
    repl2="""        if market=='USA':\n            weeks,pages,source=_v35_us_history_with_local_fallback(symbol,'week')\n            return {'ok':len(weeks)>=10,'market':market,'symbol':symbol,'source':source,'weeks':weeks[-100:],'count':len(weeks),'pages':pages}\n"""
    s,n2=re.subn(pat2,repl2,s,count=1)
    if n2==0 and "_v35_us_history_with_local_fallback(symbol,'week')" not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: v34 weekly branch')

    API.write_text(s)
    print('LONGTERM_USA_HISTORY_FALLBACK_V35_OK')

if __name__=='__main__':
    main()
