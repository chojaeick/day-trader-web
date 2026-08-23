from pathlib import Path
import re

APP=Path('app_v5.py')

def main():
    s=APP.read_text()

    # Candidate table: name-first, code secondary, reclaim horizontal space.
    pat=r"def recommendation_table\(rows,market,limit=5\):.*?return pd\.DataFrame\(out\)"
    repl=r'''def recommendation_table(rows,market,limit=5):
    rows=enrich_display_names(rows,market) if 'enrich_display_names' in globals() else rows
    out=[]
    for r in rows[:limit]:
        gate=r.get('entry_gate') or {}
        sym=str(r.get('symbol') or '-')
        name=resolve_display_name(market,sym,r.get('name') or '') if 'resolve_display_name' in globals() else (r.get('name') or sym)
        out.append({
            '종목':f'{name}  ·  {sym}',
            '판단':action_ko(action_of(r)),
            '현재가':money(r.get('price') or r.get('current_price'),market),
            'Power':('-' if r.get('power') is None else round(f(r.get('power')),1)),
            '상태':r.get('state') or gate.get('signal_grade') or '-',
            '위험':r.get('risk') or r.get('risk_level') or '-'
        })
    return pd.DataFrame(out)'''
    m=re.search(pat,s,re.S)
    if not m:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: recommendation_table')
    s=s[:m.start()]+repl+s[m.end():]

    # Selected card: display human name prominently, code as secondary caption.
    # Works against v11+ render_selected_detail variants.
    s=s.replace("c1.metric('종목',symbol)","c1.metric('종목',name)",1)
    s=s.replace("st.markdown(f'<div class=\"v5-card\"><b>{name}</b><div class=\"v5-muted\">최근 TOP5 기록 · NORMAL 대기모드에서는 실시간 Tracker 계산을 중지합니다.</div></div>',unsafe_allow_html=True)",
                "st.markdown(f'<div class=\"v5-card\"><b>{name}</b><div class=\"v5-muted\">{symbol} · 최근 TOP5 기록 · NORMAL 대기모드에서는 실시간 Tracker 계산을 중지합니다.</div></div>',unsafe_allow_html=True)",1)

    # If the selected metric is generated through a compact columns block, force name-first.
    s=re.sub(r"(metric\('종목',\s*)symbol(\))",r"\1name\2",s,count=1)

    # Visual polish: denser table, wider candidate name, cleaner holdings rows.
    css=r'''
<style>
[data-testid="stDataFrame"] [role="columnheader"]{font-size:.68rem!important;color:#8fa2bb!important}
[data-testid="stDataFrame"] [role="gridcell"]{font-size:.78rem!important}
.v12-name{line-height:1.15!important;white-space:normal!important}
.v12-code{margin-top:2px!important}
[data-testid="stMetric"]{min-height:68px!important}
[data-testid="stMetricValue"]{white-space:normal!important;line-height:1.08!important}
[data-testid="stExpander"] summary{min-height:2.15rem!important}
</style>
'''
    if 'white-space:normal!important;line-height:1.08' not in s:
        pos=s.rfind('</style>')
        if pos>=0:
            s=s[:pos+8]+css+s[pos+8:]
        else:
            s+='\n'+css

    APP.write_text(s)
    print('PREOPEN_UI_NAMEFIRST_COMPACT_V17_OK')

if __name__=='__main__':
    main()
