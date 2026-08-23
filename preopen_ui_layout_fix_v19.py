from pathlib import Path
import re

APP = Path('app_v5.py')


def must_replace(src, old, new, label):
    if old not in src:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return src.replace(old, new, 1)


def main():
    s = APP.read_text()

    # 1) Candidate selector: always name-first.  This removes the code-first
    # selector that remained under the TOP5 table after v18.
    s = re.sub(
        r"labels=\[f\"\{r\.get\('symbol'\).*?for r in source\]",
        "labels=[f\"{resolve_display_name(market,r.get('symbol'),r.get('name') or '')} · {str(r.get('symbol') or '').upper()} · {action_ko(action_of(r))}\" for r in source]",
        s,
        count=1,
        flags=re.S,
    )

    # 2) Holdings row: give the human-readable name substantially more room.
    # Old proportions made long ETF names wrap into 2-3 lines.
    for old in (
        "st.columns([1.1,.9,.9,.75,.82,1.05,1.05,.58])",
        "st.columns([1.55,.8,.85,.72,.72,1.05,.9,.5])",
    ):
        if old in s:
            s = s.replace(old, "st.columns([1.85,.82,.82,.72,.72,1.08,.82,.46])", 1)
            break

    # 3) Management KPI must count actual registered positions, not live tracker rows.
    old = "c.metric('후보',len(finders) if active else ('대기' if standby else 0));d.metric('관리',len(rows))"
    new = "c.metric('후보',len(finders) if active else ('대기' if standby else 0));_pos,_=position_rows();_managed=sum(1 for p in _pos if str(p.get('market') or '').upper() in {'',market});d.metric('관리',_managed)"
    if old in s:
        s = s.replace(old, new, 1)

    # 4) Name-first engine expander label (v18 may already have applied it).
    s = s.replace(
        "with st.expander(f'{sym} 상세 엔진 평가',expanded=False):",
        "with st.expander(f'{display_name} · {sym} 상세 엔진 평가',expanded=False):",
        1,
    )

    # 5) Final visual override. Appended just before api() so it executes after
    # the accumulated legacy CSS and wins deterministically.
    if 'UI v19 FINAL LAYOUT' not in s:
        css = """st.markdown('''
<style>
/* ===== UI v19 FINAL LAYOUT ===== */
.block-container{max-width:1540px!important;padding:.55rem 1rem 1.2rem!important}
[data-testid="stHorizontalBlock"]{gap:.55rem!important}
[data-testid="stVerticalBlock"]{gap:.28rem!important}
[data-testid="stMetric"]{min-height:66px!important;padding:.42rem .62rem!important;background:#0b1726!important;border:1px solid #1d3653!important;border-radius:11px!important}
[data-testid="stMetricLabel"]{font-size:.66rem!important;color:#7f93ad!important}
[data-testid="stMetricValue"]{font-size:1.08rem!important;line-height:1.05!important}
[data-testid="stDataFrame"]{border:1px solid #203b59!important;border-radius:11px!important;overflow:hidden!important}
[data-testid="stDataFrame"] [role="columnheader"]{font-size:.69rem!important;font-weight:800!important}
[data-testid="stDataFrame"] [role="gridcell"]{font-size:.78rem!important}
[data-testid="stExpander"]{border:1px solid #203b59!important;border-radius:10px!important;background:#091521!important}
[data-testid="stExpander"] summary{min-height:2.05rem!important;font-size:.78rem!important}
.v18-selected{grid-template-columns:2.05fr .95fr .72fr .78fr!important;padding:12px 15px!important;min-height:74px!important}
.v18-selected-name{font-size:1.25rem!important}.v18-selected-code{font-size:.66rem!important}.v18-selected-price{font-size:1.16rem!important}.v18-selected-power{font-size:1.12rem!important}.v18-selected-action{font-size:1.06rem!important}
.v18-reason{padding:7px 10px!important;margin:5px 0!important}
/* Holdings should read like a compact portfolio ledger, not stacked cards. */
.v5-section-title{font-size:1.18rem!important;margin:.05rem 0!important}.v5-section-sub{font-size:.68rem!important;margin-bottom:.25rem!important}
.stButton>button{min-height:1.95rem!important;padding:.12rem .45rem!important}
[data-baseweb="select"]>div{min-height:1.95rem!important}
hr{margin:.32rem 0!important}
</style>
''', unsafe_allow_html=True)
"""
        anchor = 'def api(path, timeout=10):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        s = s.replace(anchor, css + '\n' + anchor, 1)

    # Visible build marker: verify the deployed screen is really this build.
    if "v19" not in s:
        s = s.replace(
            "DAY TRADER V5 <span style='font-size:.62rem;color:#4d8edb'>v18</span>",
            "DAY TRADER V5 <span style='font-size:.62rem;color:#4d8edb'>v19</span>",
            1,
        )
        s = s.replace('DAY TRADER V5</div>', "DAY TRADER V5 <span style='font-size:.62rem;color:#4d8edb'>v19</span></div>", 1)

    APP.write_text(s)
    print('PREOPEN_UI_LAYOUT_FIX_V19_OK')


if __name__ == '__main__':
    main()
