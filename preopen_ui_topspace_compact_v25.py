from pathlib import Path

APP=Path('app_v5.py')


def main():
    s=APP.read_text()
    if 'V25 TOPSPACE COMPACT' not in s:
        css='''st.markdown("""
<style>
/* ===== V25 TOPSPACE COMPACT ===== */
/* Streamlit chrome was still clipping the custom title at the top. */
.block-container{padding-top:1.45rem!important;padding-left:1rem!important;padding-right:1rem!important;max-width:1560px!important}
.v24-header{margin-top:.15rem!important;line-height:1.18!important;min-height:2.45rem!important;overflow:visible!important}
.v24-tagline{margin-top:.02rem!important;margin-bottom:.32rem!important}
/* Keep the working V23 candidate grid but tighten surrounding chrome. */
.v23-table{margin-top:.18rem!important;margin-bottom:.28rem!important}
.v23-grid-row{min-height:36px!important}
/* Portfolio rows: name gets priority, controls stay compact. */
[data-testid="stHorizontalBlock"]{gap:.5rem!important}
[data-testid="stExpander"] summary{min-height:1.9rem!important;padding-top:.18rem!important;padding-bottom:.18rem!important}
[data-testid="stDataFrame"] [role="gridcell"],[data-testid="stDataFrame"] [role="columnheader"]{font-size:.73rem!important}
.stButton>button{min-height:1.85rem!important}
hr{margin:.30rem 0!important}
</style>
""",unsafe_allow_html=True)
'''
        anchor='def api(path, timeout=10):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        s=s.replace(anchor,css+'\n'+anchor,1)

    # Visible build marker.
    s=s.replace('class="v24-ver">v24</span>','class="v24-ver">v25</span>',1)

    APP.write_text(s)
    print('PREOPEN_UI_TOPSPACE_COMPACT_V25_OK')


if __name__=='__main__':
    main()
