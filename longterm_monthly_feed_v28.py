from pathlib import Path
import re

API=Path('live_server/api.py')
APP=Path('app_v5.py')

API_SNIP=r'''
# ===== V28 LONG-TERM MONTHLY HISTORY FEED =====
def _v28_num(v):
    try:
        return abs(float(str(v).replace(',','').replace('+','').strip()))
    except Exception:
        return 0.0

def _v28_monthly_from_daily(rows):
    cleaned=[]
    for x in rows or []:
        if not isinstance(x,dict):
            continue
        dt=str(x.get('dt') or x.get('date') or x.get('stk_dt') or x.get('base_dt') or '').strip().replace('-','')
        close=_v28_num(x.get('cur_prc') if x.get('cur_prc') is not None else x.get('close'))
        if len(dt)>=8 and close>0:
            cleaned.append((dt[:8],close))
    cleaned=sorted(set(cleaned),key=lambda z:z[0])
    by_month={}
    for dt,close in cleaned:
        by_month[dt[:6]]=(dt,close)
    out=[]
    for m,(dt,close) in sorted(by_month.items()):
        out.append({'month':f'{m[:4]}-{m[4:6]}','date':dt,'close':close})
    return out

@app.get('/api/v5/monthly-history/{market}/{symbol}')
def v28_monthly_history(market:str,symbol:str):
    market=str(market or '').upper().strip()
    symbol=str(symbol or '').upper().strip()
    if not symbol:
        raise HTTPException(status_code=400,detail='symbol required')
    try:
        if market=='KOREA':
            code=symbol.split('_',1)[0]
            rows=[]; next_key=''; pages=0
            # ka10081 domestic daily chart; two pages is comfortably > 10 months.
            while pages<3:
                hdr=k.headers('ka10081')
                if next_key:
                    hdr['cont-yn']='Y'; hdr['next-key']=next_key
                body={'stk_cd':code,'base_dt':datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'),'upd_stkpc_tp':'1'}
                r=requests.post(s.rest_base+'/api/dostk/chart',headers=hdr,json=body,timeout=25)
                d=r.json()
                if d.get('return_code') not in (None,0):
                    raise RuntimeError(f"ka10081 {code}: {d.get('return_code')} {d.get('return_msg')}")
                raw=d.get('stk_dt_pole_chart_qry') or d.get('stk_dt_chart_qry') or []
                if not raw:
                    for v in d.values():
                        if isinstance(v,list): raw=v; break
                rows.extend(raw or [])
                pages+=1
                cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
                next_key=r.headers.get('next-key') or r.headers.get('Next-Key') or ''
                if cont!='Y' or not next_key: break
            mon=_v28_monthly_from_daily(rows)
            return {'ok':len(mon)>=10,'market':market,'symbol':code,'source':'KIWOOM_KA10081','months':mon[-24:],'count':len(mon)}

        if market=='USA':
            ex=k.active_exchange(symbol)
            from datetime import timedelta as _td
            start=(datetime.now(timezone.utc)-_td(days=500)).strftime('%Y%m%d')
            r=requests.post(s.rest_base+'/api/us/chart',headers=k.headers('usa06012'),json={
                'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'
            },timeout=25)
            d=r.json()
            if d.get('return_code') not in (None,0):
                raise RuntimeError(f"usa06012 {symbol}: {d.get('return_code')} {d.get('return_msg')}")
            mon=_v28_monthly_from_daily(d.get('result_list') or [])
            return {'ok':len(mon)>=10,'market':market,'symbol':symbol,'source':'KIWOOM_USA06012','months':mon[-24:],'count':len(mon)}

        raise HTTPException(status_code=400,detail='market must be USA or KOREA')
    except HTTPException:
        raise
    except Exception as e:
        return {'ok':False,'market':market,'symbol':symbol,'source':'KIWOOM','months':[],'count':0,'error':str(e)}

'''

APP_HELPER=r'''@st.cache_data(ttl=3600,show_spinner=False)
def _monthly_close_history(symbol,market=None):
    # V28: prefer broker-backed daily history so long-term evaluation does not
    # depend on whether this local DB happened to collect the symbol months ago.
    mk=str(market or st.session_state.get('market') or '').upper()
    if mk in {'USA','KOREA'}:
        live_hist=api(f'/api/v5/monthly-history/{mk}/{symbol}',25)
        months=list(live_hist.get('months') or []) if isinstance(live_hist,dict) else []
        if len(months)>=10:
            return {'ok':True,'rows':[(str(x.get('month')),float(x.get('close'))) for x in months if x.get('month') and x.get('close') is not None], 'source':live_hist.get('source')}
    try:
        con=sqlite3.connect(LONGTERM_DB_PATH,timeout=5)
        cols=[r[1] for r in con.execute('PRAGMA table_info(daily_history)').fetchall()]
        def pick(options):
            return next((x for x in options if x in cols),None)
        sym_col=pick(['symbol','ticker','code'])
        date_col=pick(['trade_date','date','day','ts','datetime'])
        close_col=pick(['close','close_price','price','last_price'])
        if not (sym_col and date_col and close_col):
            con.close();return {'ok':False,'reason':'월봉 데이터 없음'}
        q=f'SELECT "{date_col}","{close_col}" FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}"'
        rows=con.execute(q,(str(symbol).upper(),)).fetchall();con.close()
        if not rows:return {'ok':False,'reason':'월봉 데이터 없음'}
        df=pd.DataFrame(rows,columns=['date','close'])
        df['date']=pd.to_datetime(df['date'],errors='coerce')
        df['close']=pd.to_numeric(df['close'],errors='coerce')
        df=df.dropna().sort_values('date')
        if df.empty:return {'ok':False,'reason':'월봉 데이터 파싱 실패'}
        df['month']=df['date'].dt.to_period('M')
        mon=df.groupby('month',as_index=False).tail(1)[['month','close']].reset_index(drop=True)
        return {'ok':True,'rows':[(str(r.month),float(r.close)) for r in mon.itertuples()], 'source':'LOCAL_DB'}
    except Exception as e:
        return {'ok':False,'reason':str(e)}
'''

def main():
    a=API.read_text()
    if 'V28 LONG-TERM MONTHLY HISTORY FEED' not in a:
        anchor="manual_scan_state={'last_started_monotonic':0.0,'last_result':None}\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        a=a.replace(anchor,anchor+API_SNIP,1)
        API.write_text(a)

    s=APP.read_text()
    pat=r'@st\.cache_data\(ttl=3600,show_spinner=False\)\ndef _monthly_close_history\(symbol\):\n.*?(?=\ndef longterm_ma10_eval\()'
    m=re.search(pat,s,re.S)
    if not m: raise SystemExit('PATCH_TARGET_NOT_FOUND: monthly helper')
    s=s[:m.start()]+APP_HELPER+s[m.end():]
    s=s.replace("hist=_monthly_close_history(symbol)","hist=_monthly_close_history(symbol,market)",1)
    s=s.replace("def longterm_ma10_eval(symbol,current_price,avg_price):","def longterm_ma10_eval(symbol,current_price,avg_price,market=None):",1)
    s=s.replace("lt=longterm_ma10_eval(sym,p['cur'],p['avg'])","lt=longterm_ma10_eval(sym,p['cur'],p['avg'],market)")
    s=s.replace('class="v24-ver">v27</span>','class="v24-ver">v28</span>',1)
    APP.write_text(s)
    print('LONGTERM_MONTHLY_FEED_V28_OK')

if __name__=='__main__':
    main()
