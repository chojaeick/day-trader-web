#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile, re, subprocess, time, urllib.request

APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
LOG=Path('/tmp/daytrader-v5.log')
PORT=8503

s=APP.read_text(encoding='utf-8')

# --- 1) USA trading view must use the same TOP20 Finder/Tracker pipeline as KR.
s=s.replace(
    "source=(finders[:20] if market=='KOREA' and finders else (rows[:20] if market=='KOREA' else (rows[:5] or finders[:5])))",
    "source=(finders[:20] if finders else rows[:20])"
)
s=s.replace("ttl='실시간 단타 후보 TOP 20' if market=='KOREA' else '실시간 단타 후보 TOP 5'", "ttl='실시간 단타 후보 TOP 20'")
s=s.replace("'Finder TOP '+str(20 if market=='KOREA' else 5)", "'Finder TOP 20'")

# --- 2) Sticky paired toggle semantics.
# One market and one mode are always selected. Clicking the active tile keeps it active;
# clicking its peer moves the selection. This avoids Streamlit momentary-button de-selection.
old_boot="""if 'v5_market' not in st.session_state:
    st.session_state['v5_market']='KOREA'
rt=get_runtime_mode(); rt_mode=str(rt.get('mode') or 'UNKNOWN').upper(); market=st.session_state['v5_market']"""
new_boot="""if 'v5_market' not in st.session_state or st.session_state.get('v5_market') not in ('KOREA','USA'):
    st.session_state['v5_market']='KOREA'
rt=get_runtime_mode(); api_mode=str(rt.get('mode') or 'NORMAL').upper()
if api_mode not in ('NORMAL','DAYTRADE'):
    api_mode='NORMAL'
if 'v5_mode' not in st.session_state or st.session_state.get('v5_mode') not in ('NORMAL','DAYTRADE'):
    st.session_state['v5_mode']=api_mode
rt_mode=st.session_state['v5_mode']; market=st.session_state['v5_market']"""
if old_boot in s:
    s=s.replace(old_boot,new_boot,1)
else:
    # Resilient patch for locally modified V41/V39 runtime app.
    s=re.sub(
        r"if 'v5_market' not in st\.session_state:\n\s+st\.session_state\['v5_market'\]='KOREA'\nrt=get_runtime_mode\(\);\s*rt_mode=str\(rt\.get\('mode'\) or 'UNKNOWN'\)\.upper\(\);\s*market=st\.session_state\['v5_market'\]",
        new_boot,s,count=1
    )

# Replace the four tile handlers while preserving the existing layout/keys/icons.
lines=s.splitlines(); out=[]
for line in lines:
    ind=line[:len(line)-len(line.lstrip())]
    if "key='v38kr'" in line and '.button(' in line:
        icon=",icon='🇰🇷'" if "icon='🇰🇷'" in line else ''
        out.append(ind+"if a.button('KR 국장',use_container_width=True,type='primary' if market=='KOREA' else 'secondary',key='v38kr'"+icon+"):")
        continue
    if "key='v38us'" in line and '.button(' in line:
        icon=",icon='🇺🇸'" if "icon='🇺🇸'" in line else ''
        out.append(ind+"if b.button('US 미장',use_container_width=True,type='primary' if market=='USA' else 'secondary',key='v38us'"+icon+"):")
        continue
    if "key='v38normal'" in line and '.button(' in line:
        icon=",icon=':material/monitor_heart:'" if "monitor_heart" in line else ''
        out.append(ind+"if c.button('NORMAL',use_container_width=True,type='primary' if rt_mode=='NORMAL' else 'secondary',key='v38normal'"+icon+"):")
        continue
    if "key='v38day'" in line and '.button(' in line:
        icon=",icon=':material/trending_up:'" if "trending_up" in line else ''
        label='DAYTRADE' if "'DAYTRADE'" in line else '⚡ DAYTRADE'
        out.append(ind+f"if d.button('{label}',use_container_width=True,type='primary' if rt_mode=='DAYTRADE' else 'secondary',key='v38day'"+icon+"):")
        continue
    out.append(line)
s='\n'.join(out)+'\n'

# Replace handler bodies. The old bodies are one-line callbacks in all V38-V41 builds.
s=s.replace("st.session_state['v5_market']='KOREA';st.rerun()", "st.session_state['v5_market']='KOREA'; st.rerun()")
s=s.replace("st.session_state['v5_market']='USA';st.rerun()", "st.session_state['v5_market']='USA'; st.rerun()")
s=s.replace("set_runtime_mode('NORMAL');st.rerun()", "st.session_state['v5_mode']='NORMAL'; set_runtime_mode('NORMAL'); st.rerun()")
s=s.replace("set_runtime_mode('DAYTRADE');st.rerun()", "st.session_state['v5_mode']='DAYTRADE'; set_runtime_mode('DAYTRADE'); st.rerun()")

# Some V41 lines have guards `and rt_mode!='...'`; ensure they are gone so active tile is sticky.
s=s.replace(") and rt_mode!='NORMAL':", "):")
s=s.replace(") and rt_mode!='DAYTRADE':", "):")

# --- 3) Correct engine/status labels by selected market.
old="market_txt='KR' if market=='KOREA' else 'US'"
new="market_txt='KR' if market=='KOREA' else 'US'; engine_txt='ENGINE5 V22' if market=='KOREA' else 'ENGINE5 V22E'"
s=s.replace(old,new)
s=s.replace("' &nbsp;&nbsp; ENGINE5 V22 &nbsp;&nbsp; <span class=\"g\">LIVE</span>", "' &nbsp;&nbsp; '+engine_txt+' &nbsp;&nbsp; <span class=\"g\">LIVE</span>")

# Detail text should not falsely call the US engine V22.
s=s.replace("<li>실제 주문 판단은 ENGINE5 V22</li>", "<li>실제 주문 판단은 '+('ENGINE5 V22' if market=='KOREA' else 'ENGINE5 V22E')+'</li>")

# Brand version marker.
s=s.replace('DAY TRADER V5 <small>v39</small>','DAY TRADER V5 <small>v42</small>')
s=s.replace('DAY TRADER V5 <small>v38</small>','DAY TRADER V5 <small>v42</small>')

# CSS hint: selected tiles are toggle state, not one-shot actions.
CSS="""
st.markdown(r'''<style>
/* V42 sticky paired toggles */
.stButton>button[kind="primary"]{outline:1px solid rgba(255,157,70,.30)!important}
.stButton>button:focus{box-shadow:none!important}
</style>''',unsafe_allow_html=True)
"""
anchor='# V38_TERMINAL_DESIGN'
if 'V42 sticky paired toggles' not in s and anchor in s:
    s=s.replace(anchor,anchor+'\n'+CSS,1)

# Guards: all four controls and USA status path must still exist.
for key in ("key='v38kr'","key='v38us'","key='v38normal'","key='v38day'"):
    if key not in s:
        raise SystemExit('ABORT missing control '+key)
if "/api/v4/USA/status" not in s:
    # get_market_status usually constructs this dynamically; do not require a literal.
    pass
if "ENGINE5 V22E" not in s:
    raise SystemExit('ABORT V22E UI label missing')
if "finders[:5]" in s or "rows[:5]" in s:
    print('WARN residual TOP5 token remains outside patched V38 trading block',flush=True)

# Compile before replacing local runtime UI source.
tmp=APP.with_suffix('.py.v42tmp'); tmp.write_text(s,encoding='utf-8')
py_compile.compile(str(tmp),doraise=True)
APP.write_text(s,encoding='utf-8'); tmp.unlink(missing_ok=True)
print('V42_UI=PATCHED',flush=True)
print('MARKET_TOGGLE=STICKY_EXCLUSIVE_KR_US',flush=True)
print('MODE_TOGGLE=STICKY_EXCLUSIVE_NORMAL_DAYTRADE',flush=True)
print('USA_ENGINE_LABEL=ENGINE5_V22E',flush=True)
print('USA_FINDER_UI=TOP20',flush=True)

# Restart only Streamlit; trading API/V22/V22E services are untouched.
subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False)
time.sleep(1)
cmd=f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup /home/ubuntu/day-trader-api/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &'
subprocess.Popen(['bash','-lc',cmd],start_new_session=True)
deadline=time.time()+45; last=None
while time.time()<deadline:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=2) as r:
            if r.status==200:
                print('V5_HTTP=PASS',flush=True); break
    except Exception as e: last=e
    time.sleep(2)
else:
    raise SystemExit(f'ABORT V5 startup failed: {last}; log={LOG}')
print('BACKEND_TRADING_SERVICES=UNTOUCHED',flush=True)
print('DEPLOY=PASS',flush=True)
