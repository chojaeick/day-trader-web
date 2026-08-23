from pathlib import Path

API=Path('live_server/api.py')

BLOCK=r'''
# ===== V36 USA HISTORY DIAGNOSTICS =====
@app.get('/api/v5/history-debug/USA/{symbol}')
def v36_history_debug_usa(symbol:str):
    symbol=str(symbol or '').upper().strip()
    out={'ok':True,'symbol':symbol}
    try:
        rows,pages,meta=_v35_us_daily_rows(symbol)
        out['kiwoom_pages']=pages
        out['kiwoom_rows']=len(rows)
        out['kiwoom_first']=rows[0] if rows else None
        out['kiwoom_last']=rows[-1] if rows else None
        out['kiwoom_meta_keys']=sorted(list(meta.keys()))[:80] if isinstance(meta,dict) else []
    except Exception as e:
        out['kiwoom_error']=str(e)

    try:
        con=sqlite3.connect(s.db_path,timeout=5)
        con.row_factory=sqlite3.Row
        cols=[r[1] for r in con.execute('PRAGMA table_info(daily_history)').fetchall()]
        out['daily_history_cols']=cols
        sym_col=next((x for x in ('symbol','ticker','code') if x in cols),None)
        date_col=next((x for x in ('trade_date','date','day','ts','datetime') if x in cols),None)
        close_col=next((x for x in ('close','close_price','price','last_price') if x in cols),None)
        out['picked_cols']={'symbol':sym_col,'date':date_col,'close':close_col}
        if sym_col:
            out['db_symbol_count']=con.execute(f'SELECT COUNT(*) FROM daily_history WHERE UPPER("{sym_col}")=?',(symbol,)).fetchone()[0]
        else:
            out['db_symbol_count']=0
        if sym_col and date_col and close_col:
            q=f'SELECT "{date_col}" as dt, "{close_col}" as close FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}" LIMIT 3'
            out['db_first_rows']=[dict(r) for r in con.execute(q,(symbol,)).fetchall()]
            q2=f'SELECT "{date_col}" as dt, "{close_col}" as close FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}" DESC LIMIT 3'
            out['db_last_rows']=[dict(r) for r in con.execute(q2,(symbol,)).fetchall()]
        con.close()
    except Exception as e:
        out['db_error']=str(e)
    return out

'''

def main():
    s=API.read_text()
    if 'V36 USA HISTORY DIAGNOSTICS' not in s:
        anchor="@app.get('/api/v4/runtime-mode')"
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: runtime route anchor')
        pos=s.index(anchor)
        s=s[:pos]+BLOCK+'\n'+s[pos:]
        API.write_text(s)
    print('LONGTERM_USA_HISTORY_DEBUG_V36_OK')

if __name__=='__main__':
    main()
