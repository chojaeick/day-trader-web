from pathlib import Path
import re

APP=Path('app_v5.py')

BLOCK=r'''

# ===== UI FUJIMOTO COLUMNS V59 =====
_recommendation_table_pre_v59 = recommendation_table

def recommendation_table(rows, market, limit=5):
    if market != 'KOREA':
        return _recommendation_table_pre_v59(rows, market, limit)
    rows=enrich_display_names(rows,market) if 'enrich_display_names' in globals() else (rows or [])
    out=[]
    for r in (rows or [])[:limit]:
        gate=r.get('entry_gate') or {}
        sym=str(r.get('symbol') or '-')
        name=resolve_display_name(market,sym,r.get('name') or '') if 'resolve_display_name' in globals() else (r.get('name') or sym)
        fs=r.get('fujimoto_score')
        fstate=str(r.get('engine_state') or r.get('state') or '-')
        priority=r.get('trade_priority')
        sig=str(r.get('signal') or '').upper()
        if sig=='ENTRY_CANDIDATE': fj='진입 후보'
        elif sig=='READY': fj='진입 준비'
        elif sig in ('EXIT','HARD_EXIT'): fj='매도 검토'
        elif sig=='PARTIAL_EXIT': fj='비중 축소'
        elif fstate=='PREPARE': fj='준비'
        elif fstate in ('WATCH','NOT_EVALUATED'): fj='관찰'
        else: fj=fstate
        out.append({
            '종목':f'{name}  ·  {sym}',
            '판단':action_ko(action_of(r)),
            'Fujimoto':('-' if fs is None else int(float(fs))),
            'F상태':fj,
            '우선순위':('-' if priority is None else round(float(priority),1)),
            '현재가':money(r.get('price') or r.get('current_price'),market),
            'Power':('-' if r.get('power') is None else round(f(r.get('power')),1)),
        })
    return pd.DataFrame(out)
'''

def main():
    s=APP.read_text()
    if 'UI FUJIMOTO COLUMNS V59' in s:
        print('UI_FUJIMOTO_COLUMNS_V59_ALREADY_OK')
        return
    # Put the override immediately before render_trading so it is active for the table,
    # independent of earlier local patches to recommendation_table.
    m=re.search(r'\ndef render_trading\(market\):',s)
    if not m:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading')
    s=s[:m.start()]+BLOCK+s[m.start():]
    APP.write_text(s)
    print('UI_FUJIMOTO_COLUMNS_V59_OK')

if __name__=='__main__':
    main()
