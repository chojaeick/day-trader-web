from pathlib import Path

APP=Path('app_v5.py')

HELPER=r'''

# ===== UI DAYTRADE ENTRY CACHE V61 =====
def daytrade_entry_auto_cache():
    try:
        return get_json('/api/v5/daytrade-entry-auto/KOREA',timeout=8) or {}
    except Exception:
        return {}

def daytrade_entry_cache_rows(limit=5):
    d=daytrade_entry_auto_cache()
    rows=d.get('rows') or []
    out=[]
    for r in rows[:limit]:
        out.append({
            '종목명':r.get('name') or r.get('symbol') or '-',
            '코드':r.get('symbol') or '-',
            'Finder':r.get('finder_score'),
            'Entry':r.get('entry_score'),
            '상태':r.get('state') or '-',
            '현재가':r.get('last_price'),
            'Trigger':r.get('trigger_price'),
            '거리%':r.get('trigger_distance_pct'),
        })
    return d,out
'''

BLOCK=r'''
        # ===== DAYTRADE ENTRY CACHE V61 =====
        if market=='KOREA':
            auto_d,auto_rows=daytrade_entry_cache_rows(5)
            if auto_d:
                sess='장중' if auto_d.get('regular_open') else '장외'
                st.markdown(f"**단타 Entry Auto · {sess} · READY {auto_d.get('ready_count',0)} · ENTRY {auto_d.get('entry_candidate_count',0)}**")
                if auto_rows:
                    st.dataframe(pd.DataFrame(auto_rows),hide_index=True,use_container_width=True,height=210)
                elif not auto_d.get('regular_open'):
                    st.caption('한국장 종료 · 다음 정규장 시작 시 자동 평가')
'''


def main():
    s=APP.read_text()
    if 'UI DAYTRADE ENTRY CACHE V61' not in s:
        anchor='def render_trading(market):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading')
        s=s.replace(anchor,HELPER+'\n'+anchor,1)

    if 'DAYTRADE ENTRY CACHE V61 =====' not in s[s.find('def render_trading(market):'):]:
        anchor="    left,right=st.columns([1.0,1.3],gap='medium')\n"
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading columns')
        s=s.replace(anchor,BLOCK+'\n'+anchor,1)

    APP.write_text(s)
    print('UI_DAYTRADE_ENTRY_CACHE_V61_PATCH_OK')

if __name__=='__main__': main()
