from pathlib import Path
import re

APP=Path('app_v5.py')

NEW_HELPER=r'''@st.cache_data(ttl=300,show_spinner=False)
def _monthly_close_history(symbol,market=None,cache_epoch='v32'):
    # V32: refresh monthly feed cache quickly and never hide broker-feed diagnostics
    # behind the generic local-DB fallback message.
    mk=str(market or st.session_state.get('market') or '').upper()
    live_reason=''
    if mk in {'USA','KOREA'}:
        live_hist=api(f'/api/v5/monthly-history/{mk}/{symbol}',45)
        if isinstance(live_hist,dict):
            months=list(live_hist.get('months') or [])
            if len(months)>=10:
                rows=[]
                for x in months:
                    try:
                        m=str(x.get('month') or '')
                        c=float(x.get('close'))
                    except Exception:
                        continue
                    if m and c>0:
                        rows.append((m,c))
                if len(rows)>=10:
                    return {'ok':True,'rows':rows,'source':live_hist.get('source') or 'KIWOOM'}
            live_reason=(live_hist.get('error') or live_hist.get('detail') or
                         f"broker monthly count={len(months)}")
        else:
            live_reason='broker monthly response invalid'

    # Local DB remains only a fallback for symbols with previously stored history.
    try:
        con=sqlite3.connect(LONGTERM_DB_PATH,timeout=5)
        cols=[r[1] for r in con.execute('PRAGMA table_info(daily_history)').fetchall()]
        def pick(options):
            return next((x for x in options if x in cols),None)
        sym_col=pick(['symbol','ticker','code'])
        date_col=pick(['trade_date','date','day','ts','datetime'])
        close_col=pick(['close','close_price','price','last_price'])
        if not (sym_col and date_col and close_col):
            con.close()
            return {'ok':False,'reason':live_reason or '월봉 데이터 없음'}
        q=f'SELECT "{date_col}","{close_col}" FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}"'
        rows=con.execute(q,(str(symbol).upper(),)).fetchall();con.close()
        if not rows:
            return {'ok':False,'reason':live_reason or '월봉 데이터 없음'}
        df=pd.DataFrame(rows,columns=['date','close'])
        df['date']=pd.to_datetime(df['date'],errors='coerce')
        df['close']=pd.to_numeric(df['close'],errors='coerce')
        df=df.dropna().sort_values('date')
        if df.empty:
            return {'ok':False,'reason':live_reason or '월봉 데이터 파싱 실패'}
        df['month']=df['date'].dt.to_period('M')
        mon=df.groupby('month',as_index=False).tail(1)[['month','close']].reset_index(drop=True)
        if len(mon)<10:
            return {'ok':False,'reason':live_reason or f'완료 월봉 {len(mon)}개 · 10개 필요'}
        return {'ok':True,'rows':[(str(r.month),float(r.close)) for r in mon.itertuples()], 'source':'LOCAL_DB'}
    except Exception as e:
        return {'ok':False,'reason':live_reason or str(e)}
'''

def main():
    s=APP.read_text()
    pat=r'@st\.cache_data\(ttl=\d+,show_spinner=False\)\ndef _monthly_close_history\(symbol,market=None\):\n.*?(?=\ndef longterm_ma10_eval\()'
    m=re.search(pat,s,re.S)
    if not m:
        # tolerate an already-versioned signature from a partial rerun
        pat=r'@st\.cache_data\(ttl=\d+,show_spinner=False\)\ndef _monthly_close_history\(symbol,market=None(?:,cache_epoch=.*?)?\):\n.*?(?=\ndef longterm_ma10_eval\()'
        m=re.search(pat,s,re.S)
    if not m:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: monthly helper')
    s=s[:m.start()]+NEW_HELPER+s[m.end():]
    s=s.replace('class="v24-ver">v31</span>','class="v24-ver">v32</span>',1)
    APP.write_text(s)
    print('LONGTERM_MONTHLY_CACHEFIX_V32_OK')

if __name__=='__main__':
    main()
