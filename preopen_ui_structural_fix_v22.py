from pathlib import Path
import re

APP = Path('app_v5.py')


def main():
    s = APP.read_text()

    # 1) Replace Streamlit title with a custom header so it can never be clipped.
    s = s.replace(
        "st.title('DAY TRADER V5')",
        "st.markdown('<div class=\"v22-head\"><span class=\"v22-bolt\">⚡</span> DAY TRADER V5 <span class=\"v22-ver\">v22</span></div>', unsafe_allow_html=True)",
        1,
    )

    # 2) Candidate list renderer: fixed compact grid instead of horizontally scrolling dataframe.
    if 'def render_candidate_grid(' not in s:
        anchor = 'def normalize_position(p,live=None):\n'
        helper = '''def render_candidate_grid(rows, market, limit=5):
    rows = enrich_display_names(rows, market) if 'enrich_display_names' in globals() else (rows or [])
    html = ['<div class="v22-candidates">',
            '<div class="v22-cand v22-cand-head"><div>종목명</div><div>코드</div><div>현재가</div><div>Power</div><div>판단</div></div>']
    for r in (rows or [])[:limit]:
        sym = str(r.get('symbol') or '-').upper()
        name = resolve_display_name(market, sym, r.get('name') or '') if 'resolve_display_name' in globals() else (r.get('name') or sym)
        px = money(r.get('price') or r.get('current_price'), market)
        power = '-' if r.get('power') is None else f"{f(r.get('power')):+.1f}"
        action = action_ko(action_of(r))
        html.append(f'<div class="v22-cand"><div class="v22-cand-name">{name}</div><div class="v22-cand-code">{sym}</div><div>{px}</div><div>{power}</div><div class="v22-cand-action">{action}</div></div>')
    html.append('</div>')
    st.markdown(''.join(html), unsafe_allow_html=True)

'''
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: normalize_position anchor')
        s = s.replace(anchor, helper + anchor, 1)

    # Replace the TOP5 dataframe only. Engine-matrix dataframes remain untouched.
    old = "st.dataframe(recommendation_table(source,market),use_container_width=True,hide_index=True,height=205)"
    if old in s:
        s = s.replace(old, "render_candidate_grid(source,market,5)", 1)
    else:
        old2 = "st.dataframe(recommendation_table(source,market),use_container_width=True,hide_index=True,height=220)"
        if old2 in s:
            s = s.replace(old2, "render_candidate_grid(source,market,5)", 1)

    # 3) Force name-first selector under the candidate grid.
    pat = re.compile(
        r"labels=\[\];lookup=\{\}\n\s*for r in source\[:5\]:\n\s*pv=r\.get\('power'\); ptxt='-' if pv is None else f'\{f\(pv\):\+\.1f\}'\n\s*label=f\"\{r\.get\('symbol'\) or '-'\} · \{action_ko\(action_of\(r\)\)\} · Power \{ptxt\}\";labels\.append\(label\);lookup\[label\]=r"
    )
    repl = "labels=[];lookup={}\n            for r in source[:5]:\n                pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'\n                sym=str(r.get('symbol') or '-').upper(); nm=resolve_display_name(market,sym,r.get('name') or '')\n                label=f'{nm} · {sym} · {action_ko(action_of(r))} · Power {ptxt}';labels.append(label);lookup[label]=r"
    s = pat.sub(repl, s, count=1)

    # 4) Holdings: make the name area wider; keep controls usable.
    for old_cols in (
        "st.columns([1.85,.82,.82,.72,.72,1.08,.82,.46])",
        "st.columns([1.55,.8,.85,.72,.72,1.05,.9,.5])",
        "st.columns([1.1,.9,.9,.75,.82,1.05,1.05,.58])",
    ):
        if old_cols in s:
            s = s.replace(old_cols, "st.columns([2.35,.78,.78,.68,.68,1.0,.78,.42])", 1)
            break

    # 5) Final CSS override. Structural fix, not cosmetic-only tweaking.
    if 'UI v22 STRUCTURAL FIX' not in s:
        css = '''st.markdown("""
<style>
/* ===== UI v22 STRUCTURAL FIX ===== */
.block-container{max-width:1580px!important;padding:1.05rem 1rem 1.2rem!important}
.v22-head{display:flex;align-items:center;gap:.45rem;font-size:2.05rem;font-weight:950;line-height:1.15;letter-spacing:-.045em;margin:.15rem 0 .18rem;color:#f4f8ff;overflow:visible!important}
.v22-bolt{font-size:1.7rem}.v22-ver{font-size:.66rem;color:#438fff;font-weight:850;margin-left:.15rem}
.v22-candidates{border:1px solid #203954;border-radius:11px;overflow:hidden;background:#09131f;margin:.15rem 0 .34rem}
.v22-cand{display:grid;grid-template-columns:minmax(0,2.15fr) .72fr .9fr .62fr .72fr;align-items:center;gap:.35rem;min-height:37px;padding:0 .7rem;border-top:1px solid #1b2b3e;font-size:.78rem}
.v22-cand:first-child{border-top:0}.v22-cand-head{min-height:31px;background:#111925;color:#899bb2;font-size:.68rem;font-weight:800}
.v22-cand-name{font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.v22-cand-code{color:#8ca0b8;font-size:.71rem}.v22-cand-action{font-weight:850}
[data-testid="stSelectbox"]{margin-top:.05rem!important}
[data-testid="stSelectbox"]>div>div{min-height:2.15rem!important}
/* Holdings name area */
.hold-symbol,.v12-name{white-space:normal!important;overflow:visible!important;text-overflow:clip!important;line-height:1.12!important;word-break:keep-all!important}
[data-testid="stHorizontalBlock"]{gap:.48rem!important}
[data-testid="stVerticalBlock"]{gap:.25rem!important}
[data-testid="stMetric"]{min-height:62px!important}
hr{margin:.28rem 0!important}
</style>
""", unsafe_allow_html=True)
'''
        anchor = 'def api(path, timeout=10):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        s = s.replace(anchor, css + '\n' + anchor, 1)

    APP.write_text(s)
    print('PREOPEN_UI_STRUCTURAL_FIX_V22_OK')


if __name__ == '__main__':
    main()
