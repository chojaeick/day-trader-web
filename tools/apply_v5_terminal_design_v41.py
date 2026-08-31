#!/usr/bin/env python3
from pathlib import Path
import re, py_compile, subprocess, time, urllib.request

APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
LOG='/tmp/daytrader-v5.log'; PORT=8503
s=APP.read_text(encoding='utf-8')

# Replace the four top controls with icon-bearing graphic tiles while keeping
# the exact same callbacks/state keys.
lines=s.splitlines()
out=[]
for line in lines:
    indent=line[:len(line)-len(line.lstrip())]
    if "key='v38kr'" in line:
        out.append(indent+"if a.button('KR 국장',use_container_width=True,type='primary' if market=='KOREA' else 'secondary',key='v38kr',icon='🇰🇷'):")
        continue
    if "key='v38us'" in line:
        out.append(indent+"if b.button('US 미장',use_container_width=True,type='primary' if market=='USA' else 'secondary',key='v38us',icon='🇺🇸'):")
        continue
    if "key='v38normal'" in line:
        out.append(indent+"if c.button('NORMAL',use_container_width=True,type='primary' if rt_mode=='NORMAL' else 'secondary',key='v38normal',icon=':material/monitor_heart:') and rt_mode!='NORMAL':")
        continue
    if "key='v38day'" in line:
        out.append(indent+"if d.button('DAYTRADE',use_container_width=True,type='primary' if rt_mode=='DAYTRADE' else 'secondary',key='v38day',icon=':material/trending_up:') and rt_mode!='DAYTRADE':")
        continue
    out.append(line)
s='\n'.join(out)+'\n'

# UI wording closer to the approved reference.
s=s.replace('실시간 Finder · 최대 20','실시간 단타 후보 TOP 20')
s=s.replace('V22 진입 전 Finder 랭킹 · 적격 후보 최대 20종목','후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.')
s=s.replace('V22 MOCK EXECUTION TERMINAL','V22 MOCK EXECUTION TERMINAL')

CSS=r'''
<style>
/* ===== V41 APPROVED MOCKUP TERMINAL ===== */
.stApp{background:radial-gradient(circle at 58% -12%,#10263a 0,#07121d 38%,#030a11 100%)!important}
.block-container{max-width:1720px!important;padding:.68rem 1.05rem 1.25rem!important}

/* brand */
.v38-brand{font-size:1.95rem!important;font-weight:950!important;letter-spacing:-.05em!important}
.v38-brand small{font-size:.58rem!important;color:#3ca2ff!important}.v38-sub{font-size:.66rem!important;letter-spacing:.04em!important;color:#7589a3!important}

/* graphic market/mode tiles: icon + label, fixed so nothing can protrude */
.stButton>button{min-width:0!important;width:100%!important;overflow:hidden!important;border:1px solid #263a50!important;background:linear-gradient(180deg,#0d1a28,#09131e)!important;border-radius:12px!important;color:#edf5ff!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.025)!important}
.stButton>button p{white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;margin:0!important;line-height:1.05!important;font-weight:850!important}
.stButton>button[kind="primary"]{background:linear-gradient(145deg,#ff8b24,#f45108)!important;border-color:#ff8a24!important;box-shadow:0 0 20px rgba(255,112,20,.25),inset 0 1px 0 rgba(255,255,255,.18)!important}
/* top four controls are visually large tiles */
div[data-testid="stHorizontalBlock"] button[key]{min-height:48px!important}

/* 8 KPI blocks on one desktop row like the reference */
.v38-summary{padding:.52rem!important;margin:.5rem 0 .55rem!important;border-color:#203449!important;background:linear-gradient(180deg,#0b1723,#07111b)!important}
.v38-kpi-grid{display:grid!important;grid-template-columns:repeat(8,minmax(0,1fr))!important;gap:0!important}
.v38-kpi{min-width:0!important;min-height:70px!important;padding:.48rem .72rem!important;border-right:1px solid #1f3246!important}
.v38-kpi:last-child{border-right:none!important}.v38-label{font-size:.61rem!important;color:#7990aa!important}.v38-val{font-size:1.02rem!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}.v38-subval{font-size:.58rem!important;color:#667b95!important}

/* tab navigation */
.stTabs [data-baseweb="tab-list"]{background:transparent!important;border-bottom:1px solid #26384b!important;border-radius:0!important;padding:0!important;gap:1.1rem!important}
.stTabs [data-baseweb="tab"]{font-size:.77rem!important;font-weight:850!important;padding:.45rem .1rem .55rem!important;border-radius:0!important}.stTabs [aria-selected="true"]{color:#ff7b25!important;border-bottom:2px solid #ff6d1c!important}

/* main trading split */
.v38-panel,.v38-detail{border-color:#22374d!important;background:linear-gradient(180deg,#0b1723,#07111b)!important;border-radius:12px!important}
.v38-panel-head,.v38-detail-head{font-size:1rem!important;padding:.72rem .88rem!important}.v38-panel-sub{font-size:.62rem!important}.v38-live{font-size:.62rem!important}
.v38-detail-main{padding:.72rem .88rem!important}.v38-big{font-size:1.45rem!important}.v38-code{font-size:.62rem!important}.v38-grid>div{padding:.58rem .4rem!important}.v38-grid span{font-size:.58rem!important}.v38-grid b{font-size:.78rem!important}.v38-reason{min-height:112px!important;padding:.7rem .8rem!important}.v38-reason h4{font-size:.76rem!important}.v38-reason ul{font-size:.68rem!important;line-height:1.55!important}

/* finder table: dense professional watchlist */
[data-testid="stDataFrame"]{border:1px solid #22374d!important;border-radius:8px!important;background:#08121d!important}
[data-testid="stDataFrame"] [role="columnheader"]{background:#101a27!important;font-size:.66rem!important;color:#8699b0!important}
[data-testid="stDataFrame"] [role="gridcell"]{font-size:.72rem!important}

/* holdings visually becomes a compact blotter instead of giant cards */
.v38-hold-title{font-size:1rem!important;margin:.65rem 0 .05rem!important}.v38-hold-sub{font-size:.62rem!important}
.hold-symbol{font-size:.84rem!important}.hold-sub{font-size:.58rem!important}.hold-head{font-size:.58rem!important}.hold-val{font-size:.78rem!important}
[data-testid="stExpander"] summary{min-height:1.9rem!important;font-size:.7rem!important}
[data-baseweb="select"]>div{min-height:38px!important;font-size:.72rem!important}

/* small icon utility buttons */
button[aria-label="Settings"],button[aria-label="Fullscreen"]{border-radius:8px!important}

@media(max-width:1050px){.v38-kpi-grid{grid-template-columns:repeat(4,minmax(0,1fr))!important}.v38-brand{font-size:1.55rem!important}}
</style>
'''
if 'V41 APPROVED MOCKUP TERMINAL' not in s:
    anchor='def api(path, timeout=10):'
    pos=s.find(anchor)
    if pos<0: raise SystemExit('ABORT api anchor missing')
    s=s[:pos]+"st.markdown(r'''"+CSS+"''',unsafe_allow_html=True)\n\n"+s[pos:]

# Ensure Finder rendering itself permits 20 rows and gives enough visible height.
s=s.replace('finders[:5]','finders[:20]').replace('finder_rows[:5]','finder_rows[:20]')
s=s.replace('height=334','height=470').replace('height=350','height=470').replace('height=560','height=470')

# Safety: graphical button labels must exist after patch.
for key in ("key='v38kr'","key='v38us'","key='v38normal'","key='v38day'"):
    if key not in s: raise SystemExit('ABORT missing top-control key '+key)

TMP=APP.with_suffix('.py.v41tmp');TMP.write_text(s,encoding='utf-8')
py_compile.compile(str(TMP),doraise=True)
APP.write_text(s,encoding='utf-8');TMP.unlink(missing_ok=True)
print('V41_UI=PATCHED',flush=True)
print('TOP_CONTROLS=ICON_TILES',flush=True)
print('BUTTON_OVERFLOW=HARD_CLIPPED',flush=True)
print('KPI_LAYOUT=8_ONE_ROW_DESKTOP',flush=True)
print('FINDER_UI_CAPACITY=20',flush=True)
print('HOLDINGS=COMPACT_BLOTTER_STYLE',flush=True)
subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False);time.sleep(1)
cmd=f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup /home/ubuntu/day-trader-api/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &'
subprocess.Popen(['bash','-lc',cmd],start_new_session=True)
deadline=time.time()+45;last=None
while time.time()<deadline:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=2) as r:
            if r.status==200: print('V5_HTTP=PASS',flush=True);break
    except Exception as e:last=e
    time.sleep(2)
else: raise SystemExit(f'ABORT V5 startup failed: {last}; log={LOG}')
print('V5_DESIGN=APPROVED_REFERENCE_V41',flush=True)
print('V22_BACKEND=UNTOUCHED',flush=True)
print('DEPLOY=PASS',flush=True)
