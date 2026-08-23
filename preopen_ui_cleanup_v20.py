from pathlib import Path
import re

APP=Path('app_v5.py')

def main():
    s=APP.read_text()

    # 1) Remove the duplicated second NORMAL/DAYTRADE control row.
    s=re.sub(
        r"def runtime_mode_bar\(\):.*?(?=\ndef f\()",
        "def runtime_mode_bar():\n    return None\n\n",
        s,
        count=1,
        flags=re.S,
    )

    # 2) Candidate selector should be human-name first, not code first.
    old="""            labels=[];lookup={}
            for r in source[:5]:
                pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'
                label=f\"{r.get('symbol') or '-'} · {action_ko(action_of(r))} · Power {ptxt}\";labels.append(label);lookup[label]=r
            sel=st.selectbox('후보 선택',labels,key=f'sel_{market}',label_visibility='collapsed')
            selected=lookup[sel]
"""
    new="""            labels=[];lookup={}
            for r in source[:5]:
                sym=str(r.get('symbol') or '-').upper()
                nm=resolve_display_name(market,sym,r.get('name') or '')
                pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'
                label=f\"{nm} · {sym} · {action_ko(action_of(r))} · Power {ptxt}\";labels.append(label);lookup[label]=r
            sel=st.selectbox('후보 선택',labels,key=f'sel_{market}',label_visibility='collapsed')
            selected=lookup[sel]
"""
    if old in s:
        s=s.replace(old,new,1)

    # 3) Make the build visible and clean the duplicated top spacing.
    s=s.replace("st.title('DAY TRADER V5')","st.markdown(\"<div style='display:flex;align-items:baseline;gap:10px'><div style='font-size:2.35rem;font-weight:900;letter-spacing:-.04em'>DAY TRADER V5</div><div style='font-size:.72rem;color:#4d8edb;font-weight:800'>v20</div></div>\",unsafe_allow_html=True)",1)

    # 4) Final compact override.
    if 'UI v20 CLEANUP' not in s:
        css="""st.markdown(\"\"\"\n<style>\n/* ===== UI v20 CLEANUP ===== */\n.block-container{max-width:1540px!important;padding:.42rem 1rem 1.1rem!important}\n[data-testid=\"stVerticalBlock\"]{gap:.22rem!important}\n[data-testid=\"stHorizontalBlock\"]{gap:.5rem!important}\n[data-testid=\"stMetric\"]{min-height:62px!important;padding:.35rem .56rem!important}\n[data-testid=\"stMetricValue\"]{font-size:1.03rem!important}\n[data-testid=\"stDataFrame\"] [role=\"gridcell\"]{font-size:.77rem!important}\n[data-testid=\"stExpander\"] summary{min-height:1.95rem!important}\n.stButton>button{min-height:1.9rem!important}\nhr{margin:.26rem 0!important}\n</style>\n\"\"\",unsafe_allow_html=True)\n"""
        anchor='def api(path, timeout=10):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        s=s.replace(anchor,css+'\n'+anchor,1)

    APP.write_text(s)
    print('PREOPEN_UI_CLEANUP_V20_OK')

if __name__=='__main__':
    main()
