from pathlib import Path
import re

APP = Path('app_v5.py')


def main():
    s = APP.read_text()

    # 1) Replace Streamlit title with a compact HTML hero so the top is never clipped.
    s = s.replace(
        "st.title('DAY TRADER V5')",
        "st.markdown(\"<div class='v21-hero'><span class='v21-bolt'>⚡</span><span class='v21-title'>DAY TRADER V5</span><span class='v21-build'>v21</span></div>\", unsafe_allow_html=True)",
        1,
    )

    # 2) Candidate table: explicit name/code widths. Long Korean names get usable room.
    old = "st.dataframe(recommendation_table(source,market),use_container_width=True,hide_index=True,height=205)"
    new = "cand_df=recommendation_table(source,market)\n            st.dataframe(cand_df,use_container_width=True,hide_index=True,height=205,column_config={'종목명':st.column_config.TextColumn('종목명',width='large'),'코드':st.column_config.TextColumn('코드',width='small'),'현재가':st.column_config.TextColumn('현재가',width='small'),'Power':st.column_config.TextColumn('Power',width='small'),'상태':st.column_config.TextColumn('상태',width='small'),'판단':st.column_config.TextColumn('판단',width='small')})"
    if old in s:
        s = s.replace(old, new, 1)

    # 3) Candidate selector must also be name-first (v20 fixed only one historical form).
    old_block = """labels=[];lookup={}
            for r in source[:5]:
                pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'
                label=f\"{r.get('symbol') or '-'} · {action_ko(action_of(r))} · Power {ptxt}\";labels.append(label);lookup[label]=r"""
    new_block = """labels=[];lookup={}
            for r in source[:5]:
                pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'
                sym=str(r.get('symbol') or '-').upper()
                nm=resolve_display_name(market,sym,r.get('name') or '')
                label=f\"{nm} · {sym} · {action_ko(action_of(r))} · Power {ptxt}\";labels.append(label);lookup[label]=r"""
    if old_block in s:
        s = s.replace(old_block, new_block, 1)

    # 4) Holdings: reserve more width for the human-readable name.
    for oldcols in (
        "st.columns([1.85,.82,.82,.72,.72,1.08,.82,.46])",
        "st.columns([1.1,.9,.9,.75,.82,1.05,1.05,.58])",
    ):
        if oldcols in s:
            s = s.replace(oldcols, "st.columns([2.25,.78,.78,.68,.68,1.0,.78,.42])", 1)
            break

    # 5) Final deterministic CSS. This is injected after legacy styles and wins.
    if 'UI v21 FINISH' not in s:
        css = '''st.markdown("""
<style>
/* ===== UI v21 FINISH ===== */
.block-container{max-width:1560px!important;padding:.9rem 1.05rem 1.25rem!important}
.v21-hero{display:flex;align-items:center;gap:.55rem;margin:.05rem 0 .1rem;line-height:1}
.v21-bolt{font-size:1.75rem}.v21-title{font-size:2rem;font-weight:950;letter-spacing:-.045em;color:#f4f8ff}.v21-build{font-size:.62rem;font-weight:800;color:#4b96ff;border:1px solid #244b78;border-radius:999px;padding:.16rem .42rem;background:#0b1c30}
[data-testid="stDataFrame"] [role="gridcell"]{white-space:nowrap!important;text-overflow:ellipsis!important;overflow:hidden!important}
[data-testid="stDataFrame"] [role="columnheader"]{white-space:nowrap!important}
[data-testid="stHorizontalBlock"]{align-items:stretch!important}
.v18-selected{border-color:#21466d!important;background:linear-gradient(180deg,#0d1b2c,#091521)!important}
.v18-selected-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* Portfolio ledger */
[data-testid="stExpander"] summary{font-weight:780!important}
.stButton>button{white-space:nowrap!important}
</style>
""", unsafe_allow_html=True)
'''
        anchor = 'def api(path, timeout=10):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        s = s.replace(anchor, css + '\n' + anchor, 1)

    # Remove stale inline v18/v19/v20 markers if any HTML title survived.
    s = re.sub(r"<span style='font-size:\.62rem;color:#4d8edb'>v(?:18|19|20)</span>", '', s)

    APP.write_text(s)
    print('PREOPEN_UI_FINISH_V21_OK')


if __name__ == '__main__':
    main()
