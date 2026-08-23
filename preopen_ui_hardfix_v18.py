from pathlib import Path
import re

APP=Path('app_v5.py')


def replace_func(src,name,new_body,next_name):
    pat=rf'def {re.escape(name)}\(.*?\n(?=def {re.escape(next_name)}\()'
    m=re.search(pat,src,re.S)
    if not m:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {name}')
    return src[:m.start()]+new_body+src[m.end():]


def main():
    s=APP.read_text()

    recommendation=r'''def recommendation_table(rows,market,limit=5):
    rows=enrich_display_names(rows,market) if 'enrich_display_names' in globals() else rows
    out=[]
    for r in (rows or [])[:limit]:
        gate=r.get('entry_gate') or {}
        sym=str(r.get('symbol') or '-').upper()
        name=resolve_display_name(market,sym,r.get('name') or '') if 'resolve_display_name' in globals() else (r.get('name') or sym)
        out.append({
            '종목명':name,
            '코드':sym,
            '현재가':money(r.get('price') or r.get('current_price'),market),
            'Power':('-' if r.get('power') is None else f"{f(r.get('power')):+.1f}"),
            '상태':r.get('state') or gate.get('signal_grade') or '-',
            '판단':action_ko(action_of(r)),
        })
    return pd.DataFrame(out)

'''
    s=replace_func(s,'recommendation_table',recommendation,'normalize_position')

    selected=r'''def render_selected_detail(r,market):
    symbol=str(r.get('symbol') or '-').upper()
    name=resolve_display_name(market,symbol,r.get('name') or '')
    reason=r.get('prototype_reason') or r.get('reason') or r.get('core_reason') or '엔진 판단 근거 대기'
    st.markdown('<div class="v18-section-title">🎯 선택 종목 상세</div>',unsafe_allow_html=True)
    st.markdown(f'<div class="v18-selected"><div><div class="v18-selected-name">{name}</div><div class="v18-selected-code">{symbol}</div></div><div class="v18-selected-price">{money(r.get("price") or r.get("current_price"),market)}</div><div class="v18-selected-power">{f(r.get("power")):+.1f}</div><div class="v18-selected-action">{action_ko(action_of(r))}</div></div>',unsafe_allow_html=True)
    st.markdown(f'<div class="v18-reason">{reason}</div>',unsafe_allow_html=True)
    x1,x2=st.columns(2)
    with x1:
        with st.expander('🧠 엔진 평가 요약',expanded=False):
            st.dataframe(engine_matrix(r),use_container_width=True,hide_index=True,height=245)
    with x2:
        with st.expander('💰 매수 계산 / 보유등록',expanded=False):
            render_buy_box(r,market)

'''
    # next function may be holding_display_name decorator; replace with regex directly
    m=re.search(r'def render_selected_detail\(.*?\n(?=@st\.cache_data\(ttl=300,show_spinner=False\)\ndef holding_display_name)',s,re.S)
    if not m: raise SystemExit('PATCH_TARGET_NOT_FOUND: render_selected_detail')
    s=s[:m.start()]+selected+s[m.end():]

    # holding expander title should also be name-first
    s=s.replace("with st.expander(f'{sym} 상세 엔진 평가',expanded=False):",
                "with st.expander(f'{display_name} · {sym} 상세 엔진 평가',expanded=False):")

    # force candidate selector to name-first and keep the actual row mapping unchanged
    s=re.sub(
        r"labels=\[.*?for r in source\]",
        "labels=[f\"{resolve_display_name(market,r.get('symbol'),r.get('name') or '')} · {str(r.get('symbol') or '').upper()} · {action_ko(action_of(r))}\" for r in source]",
        s,count=1,flags=re.S)

    # final CSS overrides: intentionally last so old accumulated CSS cannot win.
    css=r'''
st.markdown('''
<style>
/* ===== UI v18 HARD OVERRIDE ===== */
.block-container{max-width:1560px!important;padding:.65rem 1.2rem 1.2rem!important}
[data-testid="stMetric"]{background:#0c1726!important;border:1px solid #1f3652!important;border-radius:12px!important;box-shadow:none!important}
[data-testid="stDataFrame"]{border:1px solid #1f3652!important;border-radius:12px!important;overflow:hidden!important}
.v18-section-title{font-size:1.28rem;font-weight:900;letter-spacing:-.025em;margin:.1rem 0 .45rem}
.v18-selected{display:grid;grid-template-columns:1.7fr 1fr .8fr .9fr;gap:10px;align-items:center;border:1px solid #24415f;background:linear-gradient(180deg,#0e1c2d,#0b1624);border-radius:14px;padding:16px 18px}
.v18-selected-name{font-size:1.35rem;font-weight:900;line-height:1.1;color:#f5f9ff}.v18-selected-code{font-size:.72rem;color:#7489a3;margin-top:4px}
.v18-selected-price{font-size:1.3rem;font-weight:850}.v18-selected-power{font-size:1.25rem;font-weight:900;color:#29d981}.v18-selected-action{font-size:1.15rem;font-weight:900;text-align:right}
.v18-reason{border:1px solid #1d334e;border-radius:10px;background:#0a1422;color:#8fa1b8;padding:9px 12px;margin:7px 0 5px;font-size:.76rem}
[data-testid="stExpander"]{border-color:#203a58!important;background:#0a1422!important}
[data-testid="stDataFrame"] [role="gridcell"]{font-size:.81rem!important}
[data-testid="stDataFrame"] [role="columnheader"]{font-size:.72rem!important;font-weight:800!important}
/* name-first holdings */
.v5-good{color:#20d87a!important}.v5-bad{color:#ff5267!important}
</style>
''',unsafe_allow_html=True)
'''
    if 'UI v18 HARD OVERRIDE' not in s:
        # append immediately before first helper function so it always executes after base style definitions
        anchor='def api(path, timeout=10):\n'
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        s=s.replace(anchor,css+'\n'+anchor,1)

    # visible build marker for verification
    s=s.replace("DAY TRADER V5</div>","DAY TRADER V5 <span style='font-size:.62rem;color:#4d8edb'>v18</span></div>",1)

    APP.write_text(s)
    print('PREOPEN_UI_HARDFIX_V18_OK')

if __name__=='__main__':
    main()
