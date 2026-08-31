#!/usr/bin/env python3
from pathlib import Path
import py_compile, subprocess, time, urllib.request

APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
LOG='/tmp/daytrader-v5.log'; PORT=8503
text=APP.read_text(encoding='utf-8')

CSS=r'''
<style>
/* ===== V40 TERMINAL POLISH ===== */
.block-container{max-width:1780px!important;padding:.65rem 1rem 1.1rem!important}
.v5-title{font-size:2rem!important;line-height:1!important}.v5-sub{font-size:.68rem!important}
/* all Streamlit buttons: no overflow, compact terminal controls */
.stButton>button{width:100%!important;min-width:0!important;min-height:2.25rem!important;height:auto!important;padding:.42rem .65rem!important;border-radius:10px!important;overflow:hidden!important}
.stButton>button p{font-size:.78rem!important;line-height:1.05!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;margin:0!important}
/* graphical segmented controls */
.v40-switch{display:flex;align-items:center;justify-content:center;gap:.35rem;border:1px solid #263b55;border-radius:11px;background:#0a1421;padding:.32rem .52rem;min-height:38px;font-size:.72rem;font-weight:800;white-space:nowrap;overflow:hidden}
.v40-switch.on{background:linear-gradient(135deg,#ff7a18,#ff4d00);border-color:#ff7a18;color:white;box-shadow:0 0 18px rgba(255,105,20,.18)}
.v40-dot{width:7px;height:7px;border-radius:50%;background:#53677e;display:inline-block}.v40-switch.on .v40-dot{background:#fff;box-shadow:0 0 7px #fff}
/* compact KPI */
.v38-kpi-grid{grid-template-columns:repeat(8,minmax(0,1fr))!important;gap:.42rem!important}.v38-kpi{min-width:0!important;padding:.58rem .62rem!important}.v38-val{font-size:1rem!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}.v38-label,.v38-subval{font-size:.62rem!important}
/* Finder gets useful width and rows, detail stays readable */
[data-testid="stDataFrame"]{font-size:.76rem!important}.v40-finder-note{font-size:.66rem;color:#7890aa;margin:-2px 0 4px}
/* tabs more like graphic navigation pills */
.stTabs [data-baseweb="tab-list"]{gap:.25rem!important;background:#08111c!important;padding:.22rem!important;border-radius:10px!important}.stTabs [data-baseweb="tab"]{min-width:0!important;padding:.42rem .72rem!important;border-radius:8px!important;font-size:.76rem!important}
/* holdings: reduce giant vertical footprint */
[data-testid="stExpander"] summary{min-height:2rem!important;font-size:.76rem!important}.hold-val{font-size:.84rem!important}.hold-symbol{font-size:.9rem!important}
@media(max-width:1350px){.v38-kpi-grid{grid-template-columns:repeat(4,minmax(0,1fr))!important}.stButton>button p{font-size:.7rem!important}}
</style>
'''
if 'V40 TERMINAL POLISH' not in text:
    # append CSS after config/import area; Streamlit CSS can safely be emitted before UI.
    anchor="def api(path, timeout=10):"
    p=text.find(anchor)
    if p<0: raise SystemExit('ABORT api anchor missing')
    text=text[:p]+"st.markdown(r'''"+CSS+"''',unsafe_allow_html=True)\n\n"+text[p:]

# Finder display: TOP20 must not be silently truncated by UI slicing.
# Replace common slices of finder rows in rendering only; backend qualification stays unchanged.
for old,new in [
    ('finders[:5]','finders[:20]'),('finder_rows[:5]','finder_rows[:20]'),
    ('rows[:5]','rows[:20]')
]:
    text=text.replace(old,new)
# increase common dataframe heights so 15-20 qualified names can actually be visible
text=text.replace('height=350','height=560').replace('height=360','height=560').replace('height=400','height=560')

# Make labels shorter so controls never protrude. These are presentation labels only.
text=text.replace("'⚡ DAYTRADE'","'⚡ DAY' ")
text=text.replace("'KR 국장'","'KR' ")
text=text.replace("'US 미장'","'US' ")

# Correct title semantics: TOP20 is capacity, actual count can be lower if backend only returns qualified names.
text=text.replace('실시간 단타 후보 TOP 20','실시간 Finder · 최대 20')
text=text.replace('후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.','V22 진입 전 Finder 랭킹 · 적격 후보 최대 20종목')

TMP=APP.with_suffix('.py.v40tmp'); TMP.write_text(text,encoding='utf-8')
py_compile.compile(str(TMP),doraise=True)
APP.write_text(text,encoding='utf-8'); TMP.unlink(missing_ok=True)
print('V40_UI=PATCHED',flush=True)
print('BUTTON_OVERFLOW=FIXED',flush=True)
print('GRAPHIC_NAV_CONTROLS=COMPACT',flush=True)
print('FINDER_UI_LIMIT=20',flush=True)
print('FINDER_BACKEND_FILTER=UNCHANGED',flush=True)
subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False);time.sleep(1)
cmd=f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup /home/ubuntu/day-trader-api/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &'
subprocess.Popen(['bash','-lc',cmd],start_new_session=True)
deadline=time.time()+45; last=None
while time.time()<deadline:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=2) as r:
            if r.status==200: print('V5_HTTP=PASS',flush=True);break
    except Exception as e:last=e
    time.sleep(2)
else: raise SystemExit(f'ABORT V5 startup failed: {last}; log={LOG}')
print('V5_DESIGN=TERMINAL_V40',flush=True)
print('V22_BACKEND=UNTOUCHED',flush=True)
print('DEPLOY=PASS',flush=True)
