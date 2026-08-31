from pathlib import Path
import py_compile, shutil, re, sys

APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
if not APP.exists(): raise SystemExit(f'NOT_FOUND {APP}')
s=APP.read_text(encoding='utf-8')
backup=APP.with_suffix('.py.pre_terminal_redesign')
if not backup.exists(): shutil.copy2(APP, backup)

MARK='/* V5_TERMINAL_REDSESIGN_20260831 */'
CSS=r'''<style>
/* V5_TERMINAL_REDSESIGN_20260831 */
:root{--bg:#07101a;--panel:#0b1622;--panel2:#0d1a28;--line:#203246;--text:#f4f7fb;--muted:#8192a8;--green:#20d77a;--red:#ff4d61;--orange:#ff7a18;--blue:#238cff}
html,body,[class*="css"]{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif!important}
.stApp{background:radial-gradient(circle at 62% -15%,#10263a 0,#08121d 31%,#060c13 72%)!important;color:var(--text)!important}
.block-container{max-width:1540px!important;padding:.72rem 1.35rem 1.6rem!important}
#MainMenu,footer{visibility:hidden}
[data-testid="stHeader"]{background:transparent!important}
.v5-title{font-size:2.05rem!important;font-weight:950!important;letter-spacing:-.045em!important;line-height:1!important;color:#fff!important;margin:0!important}
.v5-sub{font-size:.68rem!important;color:#778aa3!important;letter-spacing:.025em!important;margin:.1rem 0 .35rem!important}
h1,h2,h3{letter-spacing:-.035em!important;color:#f6f9ff!important}
h3{font-size:1.25rem!important}
hr{border-color:#1d2b3c!important;margin:.5rem 0!important}
[data-testid="stVerticalBlock"]{gap:.36rem!important}
[data-testid="stHorizontalBlock"]{gap:.68rem!important}
/* top controls */
.stButton>button{min-height:2.35rem!important;border-radius:8px!important;border:1px solid #26384c!important;background:#0c1723!important;color:#e9f1fb!important;font-weight:800!important;box-shadow:none!important;transition:.12s ease!important}
.stButton>button:hover{border-color:#3b526b!important;background:#101f2e!important;transform:translateY(-1px)}
.stButton>button[kind="primary"]{background:linear-gradient(180deg,#ff8a25,#e85b00)!important;border-color:#ff8b2b!important;color:#fff!important;box-shadow:0 5px 16px rgba(255,104,16,.22)!important}
/* market buttons: secondary selected states remain visually quiet except Streamlit primary */
[data-testid="stMetric"]{min-height:84px!important;background:linear-gradient(180deg,#0e1b29,#0a1520)!important;border:1px solid #203246!important;border-radius:10px!important;padding:.68rem .8rem!important;box-shadow:none!important}
[data-testid="stMetricLabel"]{font-size:.68rem!important;color:#8799af!important;font-weight:750!important}
[data-testid="stMetricValue"]{font-size:1.18rem!important;font-weight:900!important;letter-spacing:-.025em!important;line-height:1.05!important}
[data-testid="stMetricDelta"]{font-size:.7rem!important}
/* tabs */
.stTabs [data-baseweb="tab-list"]{gap:.2rem!important;background:transparent!important;border-bottom:1px solid #223145!important;padding:0!important;border-radius:0!important}
.stTabs [data-baseweb="tab"]{height:2.7rem!important;padding:0 .8rem!important;border-radius:0!important;font-weight:800!important;color:#a9b5c5!important}
.stTabs [aria-selected="true"]{color:#fff!important;border-bottom:2px solid #ff4d4d!important}
/* cards / panels */
.v5-card,.v5-section{background:linear-gradient(180deg,#0d1926,#09131e)!important;border:1px solid #203246!important;border-radius:11px!important;box-shadow:none!important}
.v5-card{padding:11px 13px!important}.v5-section{padding:13px 15px!important}
.v5-note{background:#0b1825!important;border:1px solid #203246!important;border-left:3px solid #238cff!important;border-radius:8px!important}
.v5-warn{background:#201b0b!important;border:1px solid #493b13!important;border-left:3px solid #f0a51b!important;border-radius:8px!important}
/* tables */
[data-testid="stDataFrame"]{border:1px solid #203246!important;border-radius:10px!important;overflow:hidden!important;background:#08131e!important}
[data-testid="stDataFrame"] [role="columnheader"]{font-size:.67rem!important;color:#8396ae!important;font-weight:800!important}
[data-testid="stDataFrame"] [role="gridcell"]{font-size:.78rem!important;border-color:#172536!important}
/* inputs / expanders */
[data-baseweb="select"]>div,[data-baseweb="input"],[data-testid="stNumberInput"] input,[data-testid="stTextInput"] input{background:#0c1723!important;border-color:#26384c!important;border-radius:8px!important}
[data-testid="stExpander"]{border:1px solid #203246!important;border-radius:9px!important;background:#091520!important;overflow:hidden!important}
[data-testid="stExpander"] summary{min-height:2.35rem!important;background:#0c1825!important;font-weight:800!important}
/* holdings typography */
.hold-symbol{font-size:.92rem!important;font-weight:900!important;color:#f4f8ff!important}.hold-sub{font-size:.62rem!important;color:#70839c!important}
.hold-head{font-size:.62rem!important;color:#7f91a8!important;text-transform:uppercase!important;letter-spacing:.03em!important}.hold-val{font-size:.9rem!important;font-weight:850!important}
/* remove giant alert feeling */
div[data-testid="stAlert"]{padding:.55rem .75rem!important;border-radius:9px!important;border:1px solid #38401c!important;font-size:.78rem!important}
/* responsive: preserve KR/US switching and readable panels */
@media(max-width:900px){.block-container{padding:.55rem .65rem 1rem!important}[data-testid="stHorizontalBlock"]{gap:.35rem!important}.v5-title{font-size:1.65rem!important}[data-testid="stMetric"]{min-height:72px!important;padding:.5rem!important}}
</style>'''

if MARK not in s:
    anchor='def api(path, timeout=10):'
    if anchor not in s: raise SystemExit('ANCHOR_NOT_FOUND def api')
    s=s.replace(anchor, "st.markdown(r'''"+CSS+"''', unsafe_allow_html=True)\n\n"+anchor,1)

# Make market choice persistent and independent of reruns if the current UI uses a transient local variable.
# This is intentionally conservative: only patch known literal button forms when present.
if 'V5_MARKET_SWITCH_PERSIST_20260831' not in s:
    inject="""# V5_MARKET_SWITCH_PERSIST_20260831\nif 'v5_market' not in st.session_state:\n    st.session_state.v5_market='KOREA'\n\n"""
    anchor='def runtime_mode_bar():'
    if anchor in s: s=s.replace(anchor,inject+anchor,1)
    # common direct assignments after market buttons
    s=re.sub(r"(if\s+[^\n]*button\([^\n]*(?:US|미국)[^\n]*\):\s*\n)(\s*)market\s*=\s*['\"]USA['\"]",r"\1\2st.session_state.v5_market='USA'\n\2market='USA'",s)
    s=re.sub(r"(if\s+[^\n]*button\([^\n]*(?:KR|국장|KOREA)[^\n]*\):\s*\n)(\s*)market\s*=\s*['\"]KOREA['\"]",r"\1\2st.session_state.v5_market='KOREA'\n\2market='KOREA'",s)

APP.write_text(s,encoding='utf-8')
try: py_compile.compile(str(APP),doraise=True)
except Exception:
    shutil.copy2(backup,APP)
    raise
print('V5_UI=PATCHED')
print('BACKUP=',backup)
print('PY_COMPILE=PASS')
print('NOTE=UI redesign installed; existing API/engine logic preserved')
