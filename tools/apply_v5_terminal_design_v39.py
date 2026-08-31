from __future__ import annotations
from pathlib import Path
import py_compile, subprocess, time, urllib.request

APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
BACKUP=APP.with_name('app_v5.py.pre_terminal_design_v39')
PORT=8503
LOG=Path('/tmp/daytrader-v5.log')

CSS=r'''st.markdown("""
<style>
/* ===== V39 COMPACT TRADING TERMINAL ===== */
:root{--v39-bg:#07101a;--v39-panel:#0b1622;--v39-panel2:#0e1a28;--v39-line:#1c3144;--v39-text:#edf4fb;--v39-muted:#71849a;--v39-accent:#ff6a16;--v39-green:#23d68a;--v39-red:#ff5c6f;--v39-amber:#f5bd52}
.stApp{background:linear-gradient(180deg,#081421 0%,#06101a 46%,#050c14 100%)!important;color:var(--v39-text)!important}
.block-container{max-width:1720px!important;padding:14px 20px 24px!important}
[data-testid="stVerticalBlock"]{gap:.28rem!important}[data-testid="stHorizontalBlock"]{gap:.55rem!important}
.v38-brand{font-size:27px!important;font-weight:900!important;letter-spacing:-.045em!important}.v38-brand small{font-size:9px!important;color:#55a7ff!important}.v38-sub{font-size:10px!important;color:#6f8297!important;margin-top:3px!important;letter-spacing:.04em!important}
.v38-status{font-size:11px!important;color:#91a2b6!important;line-height:1.45!important}.v38-status .g{color:var(--v39-green)!important}
.stButton>button{min-height:36px!important;border-radius:8px!important;border:1px solid #21374b!important;background:#0c1824!important;font-size:12px!important;padding:.25rem .65rem!important;box-shadow:none!important}
.stButton>button[kind="primary"]{background:linear-gradient(180deg,#ff741b,#eb5700)!important;border-color:#ff7a25!important;box-shadow:0 0 0 1px rgba(255,122,37,.1),0 6px 18px rgba(255,98,15,.16)!important}
.v38-summary{border:0!important;background:transparent!important;padding:0!important;margin:7px 0 10px!important}
.v38-kpi-grid{display:grid!important;grid-template-columns:repeat(4,minmax(0,1fr))!important;gap:7px!important}
.v38-kpi{border:1px solid #1b3043!important;background:linear-gradient(180deg,#0d1926,#09131e)!important;border-radius:9px!important;padding:9px 11px!important;min-height:68px!important}
.v38-label{font-size:10px!important;color:#74879b!important;text-transform:uppercase!important;letter-spacing:.045em!important}.v38-val{font-size:18px!important;margin-top:3px!important;font-weight:850!important}.v38-subval{font-size:10px!important;color:#61758a!important;margin-top:2px!important}
.stTabs [data-baseweb="tab-list"]{gap:2px!important;background:#08131e!important;border:1px solid #1a2d3f!important;border-radius:8px!important;padding:3px!important}.stTabs [data-baseweb="tab"]{height:34px!important;padding:0 11px!important;border-radius:6px!important;font-size:12px!important}.stTabs [aria-selected="true"]{background:#102233!important;color:#fff!important;border-bottom:0!important}
.v38-panel,.v38-detail{border:1px solid #1b3043!important;border-radius:10px!important;background:linear-gradient(180deg,#0c1723,#08121c)!important;box-shadow:none!important}.v38-panel-head,.v38-detail-head{padding:11px 13px!important;font-size:16px!important;border-bottom:1px solid #1a2d3f!important}.v38-panel-sub{font-size:10px!important}.v38-live{font-size:9px!important;color:var(--v39-red)!important}
.v38-detail-main{padding:11px 13px!important}.v38-detail-grid{grid-template-columns:1.35fr 1fr .72fr .82fr!important;gap:10px!important}.v38-big{font-size:23px!important}.v38-code{font-size:10px!important}.v38-pill{font-size:9px!important;padding:2px 6px!important}.v38-watch{padding:8px!important;border-color:#22364a!important;background:#0a1621!important}.v38-watch b{font-size:16px!important}.v38-grid{margin-top:8px!important}.v38-grid>div{padding:8px!important}.v38-grid span{font-size:10px!important}.v38-grid b{font-size:13px!important}.v38-reason{min-height:112px!important;padding:9px 11px!important;background:#09141f!important}.v38-reason h4{font-size:12px!important;margin-bottom:5px!important}.v38-reason ul{font-size:11px!important;line-height:1.55!important}
[data-testid="stDataFrame"]{border-radius:8px!important;border-color:#1c3144!important;background:#07111b!important}[data-testid="stDataFrame"] [role="columnheader"]{font-size:10px!important;background:#0c1925!important}[data-testid="stDataFrame"] [role="gridcell"]{font-size:11px!important}
[data-baseweb="select"]>div{min-height:36px!important;background:#0b1722!important;border-color:#21374a!important;font-size:11px!important}
.v38-hold-title{font-size:16px!important;margin:7px 0 0!important}.v38-hold-sub{font-size:10px!important;margin-bottom:2px!important}
[data-testid="stExpander"]{border-radius:8px!important;border-color:#1b3043!important;background:#08141f!important}[data-testid="stExpander"] summary{min-height:34px!important;font-size:11px!important}
hr{margin:.35rem 0!important}
/* make bottom position rows compact */
.hold-symbol{font-size:.92rem!important}.hold-sub,.hold-head{font-size:.58rem!important}.hold-val{font-size:.82rem!important}
@media(min-width:1200px){.v38-kpi-grid{grid-template-columns:repeat(8,minmax(0,1fr))!important}.v38-kpi{min-height:64px!important}}
@media(max-width:900px){.v38-kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}.block-container{padding:10px!important}.v38-detail-grid{grid-template-columns:1fr 1fr!important}}
</style>
""",unsafe_allow_html=True)'''


def main():
    s=APP.read_text(encoding='utf-8')
    if '# V39_COMPACT_TERMINAL' in s:
        print('V39_UI=ALREADY_PATCHED',flush=True)
    else:
        if not BACKUP.exists(): BACKUP.write_text(s,encoding='utf-8')
        anchor="if 'v5_market' not in st.session_state:"
        p=s.find(anchor)
        if p<0: raise SystemExit('ABORT V39 UI anchor missing')
        payload="# V39_COMPACT_TERMINAL\n"+CSS+"\n"
        s=s[:p]+payload+s[p:]
        s=s.replace("<small>v38</small>","<small>v39</small>",1)
        s=s.replace('DECISION TERMINAL · MANUAL ORDER','V22 MOCK EXECUTION TERMINAL',1)
        # Improve trading balance: wider candidate panel, still preserve detail dominance.
        s=s.replace("st.columns([.74,1.26],gap='medium')","st.columns([.88,1.12],gap='medium')",1)
        tmp=APP.with_suffix('.py.v39tmp'); tmp.write_text(s,encoding='utf-8')
        try: py_compile.compile(str(tmp),doraise=True)
        finally: tmp.unlink(missing_ok=True)
        APP.write_text(s,encoding='utf-8')
        print('V39_UI=PATCHED',flush=True)
    subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False)
    time.sleep(1)
    cmd=f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup /home/ubuntu/day-trader-api/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &'
    subprocess.Popen(['bash','-lc',cmd],start_new_session=True)
    deadline=time.time()+45; last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=2) as r:
                if r.status==200: print('V5_HTTP=PASS',flush=True); break
        except Exception as e: last=e
        time.sleep(2)
    else: raise SystemExit(f'ABORT V5 startup failed: {last}')
    print('V5_DESIGN=COMPACT_TERMINAL_V39',flush=True)
    print('LAYOUT=CANDIDATE_44_DETAIL_56',flush=True)
    print('KPI=8_COMPACT_CARDS_DESKTOP',flush=True)
    print('V22_BACKEND=UNTOUCHED',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__': main()
