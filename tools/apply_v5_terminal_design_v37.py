from __future__ import annotations

from pathlib import Path
import py_compile, re, shutil, subprocess, time, urllib.request

APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
BACKUP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py.pre_terminal_design_v37')
LOG=Path('/tmp/daytrader-v5.log')
PORT=8503

CSS=r'''st.markdown("""
<style>
:root{--bg:#07111c;--panel:#0b1623;--panel2:#0d1b2a;--line:#223449;--muted:#7f8da2;--text:#eef4fb;--green:#19d67f;--red:#ff4e63;--amber:#ffc038;--orange:#ff6a12}
.stApp{background:radial-gradient(circle at 60% -10%,#102438 0,#08131f 38%,#050c14 100%)!important;color:var(--text)!important}
.block-container{max-width:1600px!important;padding:18px 24px 22px!important}
header[data-testid="stHeader"]{background:transparent!important;height:0!important}
#MainMenu,footer{visibility:hidden}
[data-testid="stVerticalBlock"]{gap:.34rem!important}
[data-testid="stHorizontalBlock"]{gap:.75rem!important}
.v37-brand{font-size:30px;font-weight:950;letter-spacing:-.045em;line-height:1;color:#f7fbff}.v37-brand small{font-size:10px;color:#4ea3ff;margin-left:7px;vertical-align:top}.v37-sub{font-size:12px;color:#8393a8;margin-top:5px}
.v37-status{font-size:13px;font-weight:850;text-align:right;line-height:1.45}.v37-status .g{color:var(--green)}
.v37-summary{border:1px solid var(--line);border-radius:12px;background:linear-gradient(180deg,#0d1926,#09131f);padding:12px 10px;margin:8px 0 8px}
.v37-kpi{border-right:1px solid #203044;padding:3px 14px;min-height:72px}.v37-kpi:last-child{border-right:none}.v37-label{font-size:11px;color:#8190a5}.v37-val{font-size:20px;font-weight:900;margin-top:5px;letter-spacing:-.025em}.v37-subval{font-size:11px;color:#78869a;margin-top:4px}.pos{color:var(--green)}.neg{color:var(--red)}.amber{color:var(--amber)}
.v37-panel{border:1px solid var(--line);border-radius:12px;background:linear-gradient(180deg,#0d1723,#09131e);overflow:hidden}.v37-panel-head{padding:13px 16px;border-bottom:1px solid #1f3042;font-size:18px;font-weight:900}.v37-panel-sub{font-size:11px;color:#7d8da1;margin-top:3px}.v37-live{float:right;color:var(--red);font-size:11px;font-weight:900}
.v37-detail{border:1px solid var(--line);border-radius:12px;background:linear-gradient(180deg,#0d1723,#09131e);padding:0;overflow:hidden}.v37-detail-head{padding:13px 16px;border-bottom:1px solid #213244;font-size:18px;font-weight:900}.v37-detail-main{padding:13px 16px}
.v37-big{font-size:26px;font-weight:950;letter-spacing:-.03em}.v37-code{font-size:11px;color:#70839a}.v37-pill{display:inline-block;border:1px solid #723c41;background:#3b1c20;color:#ff8e99;border-radius:5px;padding:2px 7px;font-size:10px;font-weight:800}.v37-watch{border:1px solid #2a3644;border-radius:8px;padding:10px;text-align:center}.v37-watch b{color:var(--amber);font-size:18px}
.v37-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:0;border-top:1px solid #203143;margin-top:10px}.v37-grid>div{text-align:center;padding:10px;border-right:1px solid #203143}.v37-grid>div:last-child{border-right:none}.v37-grid span{display:block;color:#8190a3;font-size:11px}.v37-grid b{font-size:14px}
.v37-reason{border:1px solid #1f3042;border-radius:8px;background:#0a1420;padding:10px 12px;min-height:142px}.v37-reason h4{margin:0 0 8px!important;font-size:13px}.v37-reason ul{margin:0;padding-left:18px;color:#cbd5e1;font-size:12px;line-height:1.75}
.v37-hold-title{font-size:18px;font-weight:900;margin:8px 0 2px}.v37-hold-sub{font-size:11px;color:#7d8da1}
[data-testid="stDataFrame"]{border:1px solid #213246!important;border-radius:9px!important;overflow:hidden!important;background:#09131e!important}
[data-testid="stDataFrame"] [role="columnheader"]{background:#0f1b28!important;color:#8392a7!important;font-size:11px!important}
[data-testid="stDataFrame"] [role="gridcell"]{font-size:12px!important}
.stButton>button{min-height:42px!important;border-radius:8px!important;border:1px solid #26394f!important;background:#0d1723!important;font-weight:850!important;color:#e8eff8!important}
.stButton>button[kind="primary"]{background:linear-gradient(180deg,#ff7a1a,#f05300)!important;border-color:#ff7a1a!important;box-shadow:0 0 18px rgba(255,106,18,.32)!important}
[data-baseweb="select"]>div{background:#0b1521!important;border-color:#26394d!important;border-radius:7px!important}
.stTabs [data-baseweb="tab-list"]{gap:18px!important;border-bottom:1px solid #223247!important;background:transparent!important}.stTabs [data-baseweb="tab"]{font-weight:850!important;padding:0 2px 10px!important}.stTabs [aria-selected="true"]{color:#ff5c2e!important;border-bottom:2px solid #ff5c2e!important}
[data-testid="stExpander"]{border:1px solid #213246!important;border-radius:8px!important;background:#0a1420!important}
hr{border-color:#1f3042!important;margin:.6rem 0!important}
@media(max-width:900px){.block-container{padding:10px!important}.v37-brand{font-size:24px}.v37-val{font-size:16px}}
</style>
""",unsafe_allow_html=True)'''

TRADING=r'''def render_trading(market):
    status=get_market_status(market)
    rows=tracker_rows(status)
    finders=finder_rows(status)
    source=(finders[:20] if market=='KOREA' and finders else (rows[:20] if market=='KOREA' else (rows[:5] or finders[:5])))
    left,right=st.columns([.74,1.26],gap='medium')
    with left:
        ttl='실시간 단타 후보 TOP 20' if market=='KOREA' else '실시간 단타 후보 TOP 5'
        st.markdown(f'<div class="v37-panel"><div class="v37-panel-head">{ttl}<span class="v37-live">● LIVE</span><div class="v37-panel-sub">후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.</div></div></div>',unsafe_allow_html=True)
        if source:
            show=[]
            for r in source:
                sym=str(r.get('symbol') or '-')
                name=resolve_display_name(market,sym,r.get('name') or '')
                if market=='KOREA':
                    show.append({'#':r.get('rank') or '-','종목':name,'현재가':money(r.get('price') or r.get('current_price'),market),'Finder':('-' if r.get('finder_score') is None else round(f(r.get('finder_score')),1)),'등락':('-' if r.get('change_pct') is None else f"{f(r.get('change_pct')):+.2f}%"),'상태':action_ko(action_of(r))})
                else:
                    show.append({'#':r.get('rank') or '-','종목':name,'현재가':money(r.get('price') or r.get('current_price'),market),'Power':('-' if r.get('power') is None else f"{f(r.get('power')):+.1f}"),'상태':action_ko(action_of(r))})
            st.dataframe(pd.DataFrame(show),hide_index=True,use_container_width=True,height=334)
            labels=[]; lookup={}
            for r in source:
                sym=str(r.get('symbol') or '-')
                nm=resolve_display_name(market,sym,r.get('name') or '')
                score=(r.get('finder_score') if market=='KOREA' else r.get('power'))
                label=f"{nm} · {sym} · {('-' if score is None else round(f(score),1))}"
                labels.append(label);lookup[label]=r
            chosen=st.selectbox('다른 종목 선택',labels,key=f'v37sel_{market}',label_visibility='collapsed')
            selected=lookup[chosen]
            live=next((x for x in rows if str(x.get('symbol') or '').upper()==str(selected.get('symbol') or '').upper()),None)
            if live:
                m=dict(selected);m.update(live)
                for k in ('finder_score','finder_reason','rank','change_pct','dollar_volume','quality'):
                    if selected.get(k) is not None:m[k]=selected[k]
                selected=m
        else:
            st.info('현재 후보 데이터가 없습니다.')
            selected=None
    with right:
        st.markdown('<div class="v37-detail"><div class="v37-detail-head">🎯 선택 종목 상세</div></div>',unsafe_allow_html=True)
        if selected:
            sym=str(selected.get('symbol') or '-')
            name=resolve_display_name(market,sym,selected.get('name') or '')
            px=money(selected.get('price') or selected.get('current_price'),market)
            v22=selected.get('engine5_v22_decision') or {}
            score=v22.get('effective_score') if v22 else selected.get('finder_score')
            decision=action_ko(action_of(selected))
            change=selected.get('change_pct')
            power=selected.get('power')
            st.markdown(f'''<div class="v37-detail-main"><div style="display:grid;grid-template-columns:1.4fr 1fr .8fr .8fr;gap:16px;align-items:center"><div><div class="v37-big">{name}</div><div class="v37-code">{sym}</div><span class="v37-pill">관찰</span></div><div><div class="v37-big">{px}</div><div class="v37-subval">등락 <span class="{'pos' if f(change)>=0 else 'neg'}">{('-' if change is None else f'{f(change):+.2f}%')}</span></div></div><div style="text-align:center"><div class="v37-label">{'V22' if v22 else 'Finder'}</div><div class="v37-big pos">{('-' if score is None else f'{f(score):.1f}')}</div></div><div class="v37-watch"><span class="v37-label">엔진 판단</span><br><b>{decision}</b></div></div>''',unsafe_allow_html=True)
            c=selected.get('components') or {}
            st.markdown(f'''<div class="v37-grid"><div><span>현재가</span><b>{px}</b></div><div><span>Power</span><b>{('-' if power is None else f'{f(power):+.1f}')}</b></div><div><span>Finder</span><b>{('-' if selected.get('finder_score') is None else f'{f(selected.get('finder_score')):.1f}')}</b></div><div><span>거래대금</span><b>{('-' if not selected.get('dollar_volume') else f'{f(selected.get('dollar_volume'))/100000000:,.0f}억')}</b></div><div><span>위험</span><b>{selected.get('risk') or '-'}</b></div></div>''',unsafe_allow_html=True)
            a,b=st.columns([.9,1.1])
            with a:
                comps=selected.get('finder_components') or {}
                bullets=[]
                if comps: bullets=[f"신호 {comps.get('signal','-')}",f"모멘텀 {comps.get('mover','-')}",f"거래대금 {comps.get('flow','-')}",f"거래량 {comps.get('volume','-')}",f"리스크 {comps.get('chase','-')}"]
                else: bullets=['추세/모멘텀 평가','거래량 참여도 평가','진입 조건 확인 중']
                st.markdown('<div class="v37-reason"><h4>진입 조건 요약</h4><ul>'+''.join(f'<li>{x}</li>' for x in bullets)+'</ul></div>',unsafe_allow_html=True)
            with b:
                reason=selected.get('finder_reason') or selected.get('reason') or '엔진 근거 대기'
                st.markdown(f'<div class="v37-reason"><h4>엔진 근거 (요약)</h4><ul><li>{reason}</li><li>실제 주문 판단은 ENGINE5 V22</li><li>Finder는 후보 발굴 전용</li></ul></div>',unsafe_allow_html=True)
            with st.expander('상세 엔진 평가 보기',expanded=False): st.dataframe(engine_matrix(selected),hide_index=True,use_container_width=True,height=220)
        else:
            st.info('왼쪽 후보를 선택하세요.')
    st.markdown('<div class="v37-hold-title">🛡 보유 포지션</div><div class="v37-hold-sub">실시간 보유 현황 및 엔진 판단</div>',unsafe_allow_html=True)
    render_positions(market,rows)'''

TOP=r'''# V37_TERMINAL_DESIGN
'''+CSS+r'''
if 'v5_market' not in st.session_state: st.session_state['v5_market']='KOREA'
rt=get_runtime_mode(); rt_mode=str(rt.get('mode') or 'UNKNOWN').upper()
market=st.session_state['v5_market']
kr_state=api('/api/korea-live/state',5) if market=='KOREA' else {}
status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)

h1,h2,h3=st.columns([1.35,1.45,1.2])
with h1:
    st.markdown('<div class="v37-brand">⚡ DAY TRADER V5 <small>v37</small></div><div class="v37-sub">DECISION TERMINAL · MANUAL ORDER</div>',unsafe_allow_html=True)
with h2:
    a,b,c,d=st.columns(4)
    if a.button('KR 국장',use_container_width=True,type='primary' if market=='KOREA' else 'secondary',key='v37kr'): st.session_state['v5_market']='KOREA';st.rerun()
    if b.button('US 미장',use_container_width=True,type='primary' if market=='USA' else 'secondary',key='v37us'): st.session_state['v5_market']='USA';st.rerun()
    if c.button('NORMAL',use_container_width=True,type='primary' if rt_mode=='NORMAL' else 'secondary',key='v37normal') and rt_mode!='NORMAL': set_runtime_mode('NORMAL');st.rerun()
    if d.button('⚡ DAYTRADE',use_container_width=True,type='primary' if rt_mode=='DAYTRADE' else 'secondary',key='v37day') and rt_mode!='DAYTRADE': set_runtime_mode('DAYTRADE');st.rerun()
with h3:
    sess=status.get('session') or status.get('market_session') or '-'
    st.markdown(f'<div class="v37-status">{("KR" if market=="KOREA" else "US")} <span class="g">● {sess}</span> &nbsp;&nbsp; ENGINE5 V22 &nbsp;&nbsp; <span class="g">LIVE</span><br><span style="font-size:11px;color:#7f8da2">Streaming {rt.get("streaming") or "-"} · Tracker {rt.get("tracker_seconds") or "-"}s · Finder {rt.get("finder_seconds") or "-"}s</span></div>',unsafe_allow_html=True)

if market=='KOREA':
    total=f(kr_state.get('total_assets')); cash=f(kr_state.get('cash')); invested=f(kr_state.get('stock_value')); holds=len(kr_state.get('holdings') or [])
    upd=str(kr_state.get('updated_at') or '-')[-14:-6] if kr_state.get('updated_at') else '-'
else:
    pr,_=position_rows(); total=cash=invested=0; holds=len([x for x in pr if str(x.get('market') or '').upper() in ('',market)]); upd='-'
summary=[('총자산',money(total,market),'평가/현금 실시간'),('투자금(원금)',money(invested,market),'보유 평가액'),('현금',money(cash,market),'주문 가능 기준'),('오늘 손익','-','실현/평가 연동'),('보유 종목',str(holds),'현재 계좌'),('세션',str(status.get('session') or '-'),'09:00 - 15:30' if market=='KOREA' else 'US session'),('후보',str(len(finders)),f'Finder TOP {20 if market=="KOREA" else 5}'),('최종 업데이트',upd,'실시간')]
st.markdown('<div class="v37-summary"><div style="display:grid;grid-template-columns:repeat(8,1fr)">'+''.join(f'<div class="v37-kpi"><div class="v37-label">{a}</div><div class="v37-val">{b}</div><div class="v37-subval">{c}</div></div>' for a,b,c in summary)+'</div></div>',unsafe_allow_html=True)

t1,t2,t3,t4,t5=st.tabs(['⚡ Trading','💼 Portfolio','📊 Market Briefing','⚙ Settings','</> Legacy / Debug'])
with t1: render_trading(market)
with t2: render_portfolio(market)
with t3: render_briefing(market)
with t4: render_settings()
with t5:
    st.warning('기존 V4 진단 기능은 분리 유지합니다.')
    with st.expander('V5 포지션 API 원본 확인'):
        _,raw=position_rows();st.json(raw)
'''

def replace_function(text,name,new):
    pat=re.compile(rf'^def {re.escape(name)}\(.*?(?=^def |^@st\.|\Z)',re.M|re.S)
    m=pat.search(text)
    if not m: raise SystemExit('ABORT function not found: '+name)
    return text[:m.start()]+new.rstrip()+'\n\n'+text[m.end():]

def main():
    text=APP.read_text(encoding='utf-8')
    if not BACKUP.exists(): shutil.copy2(APP,BACKUP);print('BACKUP',BACKUP,flush=True)
    text=replace_function(text,'render_trading',TRADING)
    anchors=["st.title('DAY TRADER V5')",'# V37_TERMINAL_DESIGN']
    pos=-1
    for a in anchors:
        p=text.find(a)
        if p>=0: pos=p;break
    if pos<0: raise SystemExit('ABORT top-level anchor missing')
    text=text[:pos]+TOP+'\n'
    APP.write_text(text,encoding='utf-8')
    py_compile.compile(str(APP),doraise=True)
    print('APP_V5_DESIGN_PATCH=PASS',flush=True)
    subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False)
    time.sleep(1)
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
    print('V5_DESIGN=REFERENCE_TERMINAL_V37',flush=True)
    print('KR_FINDER_UI=TOP20_PRESERVED',flush=True)
    print('V22_BACKEND=UNTOUCHED',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__':main()
