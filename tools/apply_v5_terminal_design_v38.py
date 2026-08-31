from __future__ import annotations

from pathlib import Path
import py_compile, re, shutil, subprocess, time, urllib.request

APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
BACKUP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py.pre_terminal_design_v38')
LOG=Path('/tmp/daytrader-v5.log')
PORT=8503

CSS = r'''st.markdown("""
<style>
:root{--bg:#07111b;--panel:#0b1622;--line:#223448;--muted:#7d8da3;--text:#eef5ff;--green:#21d982;--red:#ff5066;--amber:#ffc03a;--orange:#ff6a12}
.stApp{background:radial-gradient(circle at 55% -10%,#10283d 0,#08131e 38%,#050c13 100%)!important;color:var(--text)!important}
.block-container{max-width:1600px!important;padding:18px 24px 28px!important}
header[data-testid="stHeader"]{height:0!important;background:transparent!important}#MainMenu,footer{visibility:hidden}
[data-testid="stVerticalBlock"]{gap:.38rem!important}[data-testid="stHorizontalBlock"]{gap:.72rem!important}
.v38-brand{font-size:31px;font-weight:950;letter-spacing:-.045em;line-height:1;color:#f6f9ff}.v38-brand small{font-size:10px;color:#4ea3ff;margin-left:7px;vertical-align:top}.v38-sub{font-size:12px;color:#8494aa;margin-top:6px}
.v38-status{font-size:13px;font-weight:850;text-align:right;line-height:1.55}.v38-status .g{color:var(--green)}
.v38-summary{border:1px solid var(--line);border-radius:12px;background:linear-gradient(180deg,#0d1926,#09131f);padding:10px;margin:9px 0 10px}.v38-kpi-grid{display:grid;grid-template-columns:repeat(8,1fr)}
.v38-kpi{border-right:1px solid #203044;padding:4px 15px;min-height:76px}.v38-kpi:last-child{border-right:none}.v38-label{font-size:11px;color:#8190a5}.v38-val{font-size:20px;font-weight:900;margin-top:5px;letter-spacing:-.025em}.v38-subval{font-size:11px;color:#78869a;margin-top:4px}.pos{color:var(--green)}.neg{color:var(--red)}.amber{color:var(--amber)}
.v38-panel{border:1px solid var(--line);border-radius:12px;background:linear-gradient(180deg,#0d1723,#09131e);overflow:hidden}.v38-panel-head{padding:13px 16px;border-bottom:1px solid #1f3042;font-size:18px;font-weight:900}.v38-panel-sub{font-size:11px;color:#7d8da1;margin-top:3px}.v38-live{float:right;color:var(--red);font-size:11px;font-weight:900}
.v38-detail{border:1px solid var(--line);border-radius:12px;background:linear-gradient(180deg,#0d1723,#09131e);overflow:hidden}.v38-detail-head{padding:13px 16px;border-bottom:1px solid #213244;font-size:18px;font-weight:900}.v38-detail-main{padding:13px 16px}.v38-detail-grid{display:grid;grid-template-columns:1.4fr 1fr .8fr .8fr;gap:16px;align-items:center}
.v38-big{font-size:26px;font-weight:950;letter-spacing:-.03em}.v38-code{font-size:11px;color:#70839a}.v38-pill{display:inline-block;border:1px solid #6c3940;background:#371b20;color:#ff8b96;border-radius:5px;padding:2px 7px;font-size:10px;font-weight:800}.v38-watch{border:1px solid #2a3644;border-radius:8px;padding:10px;text-align:center}.v38-watch b{color:var(--amber);font-size:18px}
.v38-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:0;border-top:1px solid #203143;margin-top:10px}.v38-grid>div{text-align:center;padding:10px;border-right:1px solid #203143}.v38-grid>div:last-child{border-right:none}.v38-grid span{display:block;color:#8190a3;font-size:11px}.v38-grid b{font-size:14px}
.v38-reason{border:1px solid #1f3042;border-radius:8px;background:#0a1420;padding:10px 12px;min-height:138px}.v38-reason h4{margin:0 0 8px!important;font-size:13px}.v38-reason ul{margin:0;padding-left:18px;color:#cbd5e1;font-size:12px;line-height:1.75}
.v38-hold-title{font-size:18px;font-weight:900;margin:10px 0 2px}.v38-hold-sub{font-size:11px;color:#7d8da1;margin-bottom:4px}
[data-testid="stDataFrame"]{border:1px solid #213246!important;border-radius:9px!important;overflow:hidden!important;background:#09131e!important}[data-testid="stDataFrame"] [role="columnheader"]{background:#0f1b28!important;color:#8392a7!important;font-size:11px!important}[data-testid="stDataFrame"] [role="gridcell"]{font-size:12px!important}
.stButton>button{min-height:42px!important;border-radius:8px!important;border:1px solid #26394f!important;background:#0d1723!important;font-weight:850!important;color:#e8eff8!important}.stButton>button[kind="primary"]{background:linear-gradient(180deg,#ff7a1a,#ef5300)!important;border-color:#ff7a1a!important;box-shadow:0 0 18px rgba(255,106,18,.32)!important}
[data-baseweb="select"]>div{background:#0b1521!important;border-color:#26394d!important;border-radius:7px!important}.stTabs [data-baseweb="tab-list"]{gap:18px!important;border-bottom:1px solid #223247!important;background:transparent!important}.stTabs [data-baseweb="tab"]{font-weight:850!important;padding:0 2px 10px!important}.stTabs [aria-selected="true"]{color:#ff5c2e!important;border-bottom:2px solid #ff5c2e!important}
[data-testid="stExpander"]{border:1px solid #213246!important;border-radius:8px!important;background:#0a1420!important}hr{border-color:#1f3042!important;margin:.6rem 0!important}
@media(max-width:1100px){.v38-kpi-grid{grid-template-columns:repeat(4,1fr)}.v38-detail-grid{grid-template-columns:1fr 1fr}.block-container{padding:10px!important}}
</style>
""",unsafe_allow_html=True)'''

TRADING = r'''def render_trading(market):
    status=get_market_status(market)
    rows=tracker_rows(status)
    finders=finder_rows(status)
    source=(finders[:20] if market=='KOREA' and finders else (rows[:20] if market=='KOREA' else (rows[:5] or finders[:5])))
    left,right=st.columns([.74,1.26],gap='medium')
    with left:
        ttl='실시간 단타 후보 TOP 20' if market=='KOREA' else '실시간 단타 후보 TOP 5'
        st.markdown('<div class="v38-panel"><div class="v38-panel-head">'+ttl+'<span class="v38-live">● LIVE</span><div class="v38-panel-sub">후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.</div></div></div>',unsafe_allow_html=True)
        selected=None
        if source:
            show=[]
            labels=[]
            lookup={}
            for r in source:
                sym=str(r.get('symbol') or '-')
                name=resolve_display_name(market,sym,r.get('name') or '')
                if market=='KOREA':
                    score='-' if r.get('finder_score') is None else round(f(r.get('finder_score')),1)
                    chg='-' if r.get('change_pct') is None else f"{f(r.get('change_pct')):+.2f}%"
                    show.append({'#':r.get('rank') or '-','종목':name,'현재가':money(r.get('price') or r.get('current_price'),market),'Finder':score,'등락':chg,'상태':action_ko(action_of(r))})
                    label=name+' · '+sym+' · Finder '+str(score)
                else:
                    p='-' if r.get('power') is None else f"{f(r.get('power')):+.1f}"
                    show.append({'#':r.get('rank') or '-','종목':name,'현재가':money(r.get('price') or r.get('current_price'),market),'Power':p,'상태':action_ko(action_of(r))})
                    label=name+' · '+sym+' · Power '+str(p)
                labels.append(label);lookup[label]=r
            st.dataframe(pd.DataFrame(show),hide_index=True,use_container_width=True,height=334)
            chosen=st.selectbox('다른 종목 선택',labels,key=f'v38sel_{market}',label_visibility='collapsed')
            selected=lookup[chosen]
            live=next((x for x in rows if str(x.get('symbol') or '').upper()==str(selected.get('symbol') or '').upper()),None)
            if live:
                merged=dict(selected);merged.update(live)
                for k in ('finder_score','finder_reason','rank','change_pct','dollar_volume','quality','finder_components'):
                    if selected.get(k) is not None: merged[k]=selected[k]
                selected=merged
        else:
            st.info('현재 후보 데이터가 없습니다.')
    with right:
        st.markdown('<div class="v38-detail"><div class="v38-detail-head">🎯 선택 종목 상세</div></div>',unsafe_allow_html=True)
        if selected:
            sym=str(selected.get('symbol') or '-')
            name=resolve_display_name(market,sym,selected.get('name') or '')
            px=money(selected.get('price') or selected.get('current_price'),market)
            v22=selected.get('engine5_v22_decision') or {}
            score=(v22.get('effective_score') if v22 else selected.get('finder_score'))
            score_txt='-' if score is None else f"{f(score):.1f}"
            score_lbl='V22' if v22 else 'Finder'
            decision=action_ko(action_of(selected))
            change=selected.get('change_pct')
            change_txt='-' if change is None else f"{f(change):+.2f}%"
            change_cls='pos' if f(change)>=0 else 'neg'
            power=selected.get('power')
            power_txt='-' if power is None else f"{f(power):+.1f}"
            finder_txt='-' if selected.get('finder_score') is None else f"{f(selected.get('finder_score')):.1f}"
            dv=selected.get('dollar_volume')
            dv_txt='-' if not dv else f"{f(dv)/100000000:,.0f}억"
            risk=str(selected.get('risk') or '-')
            html='<div class="v38-detail-main"><div class="v38-detail-grid">'
            html+='<div><div class="v38-big">'+name+'</div><div class="v38-code">'+sym+'</div><span class="v38-pill">관찰</span></div>'
            html+='<div><div class="v38-big">'+px+'</div><div class="v38-subval">등락 <span class="'+change_cls+'">'+change_txt+'</span></div></div>'
            html+='<div style="text-align:center"><div class="v38-label">'+score_lbl+'</div><div class="v38-big pos">'+score_txt+'</div></div>'
            html+='<div class="v38-watch"><span class="v38-label">엔진 판단</span><br><b>'+decision+'</b></div></div>'
            html+='<div class="v38-grid"><div><span>현재가</span><b>'+px+'</b></div><div><span>Power</span><b>'+power_txt+'</b></div><div><span>Finder</span><b>'+finder_txt+'</b></div><div><span>거래대금</span><b>'+dv_txt+'</b></div><div><span>위험</span><b>'+risk+'</b></div></div></div>'
            st.markdown(html,unsafe_allow_html=True)
            a,b=st.columns([.9,1.1])
            with a:
                comps=selected.get('finder_components') or {}
                if comps:
                    bullets=['신호 '+str(comps.get('signal','-')),'모멘텀 '+str(comps.get('mover','-')),'거래대금 '+str(comps.get('flow','-')),'거래량 '+str(comps.get('volume','-')),'리스크 '+str(comps.get('chase','-'))]
                else:
                    bullets=['추세/모멘텀 평가','거래량 참여도 평가','진입 조건 확인 중']
                st.markdown('<div class="v38-reason"><h4>진입 조건 요약</h4><ul>'+''.join('<li>'+x+'</li>' for x in bullets)+'</ul></div>',unsafe_allow_html=True)
            with b:
                reason=str(selected.get('finder_reason') or selected.get('reason') or '엔진 근거 대기')
                st.markdown('<div class="v38-reason"><h4>엔진 근거 (요약)</h4><ul><li>'+reason+'</li><li>실제 주문 판단은 ENGINE5 V22</li><li>Finder는 후보 발굴 전용</li></ul></div>',unsafe_allow_html=True)
            with st.expander('상세 엔진 평가 보기',expanded=False):
                st.dataframe(engine_matrix(selected),hide_index=True,use_container_width=True,height=220)
        else:
            st.info('왼쪽 후보를 선택하세요.')
    st.markdown('<div class="v38-hold-title">🛡 보유 포지션</div><div class="v38-hold-sub">실시간 보유 현황 및 엔진 판단</div>',unsafe_allow_html=True)
    render_positions(market,rows)'''

TOP = r'''# V38_TERMINAL_DESIGN
'''+CSS+r'''
if 'v5_market' not in st.session_state:
    st.session_state['v5_market']='KOREA'
rt=get_runtime_mode(); rt_mode=str(rt.get('mode') or 'UNKNOWN').upper(); market=st.session_state['v5_market']
status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)
kr_state=api('/api/korea-live/state',5) if market=='KOREA' else {}

h1,h2,h3=st.columns([1.35,1.45,1.2])
with h1:
    st.markdown('<div class="v38-brand">⚡ DAY TRADER V5 <small>v38</small></div><div class="v38-sub">DECISION TERMINAL · MANUAL ORDER</div>',unsafe_allow_html=True)
with h2:
    a,b,c,d=st.columns(4)
    if a.button('KR 국장',use_container_width=True,type='primary' if market=='KOREA' else 'secondary',key='v38kr'):
        st.session_state['v5_market']='KOREA';st.rerun()
    if b.button('US 미장',use_container_width=True,type='primary' if market=='USA' else 'secondary',key='v38us'):
        st.session_state['v5_market']='USA';st.rerun()
    if c.button('NORMAL',use_container_width=True,type='primary' if rt_mode=='NORMAL' else 'secondary',key='v38normal') and rt_mode!='NORMAL':
        set_runtime_mode('NORMAL');st.rerun()
    if d.button('⚡ DAYTRADE',use_container_width=True,type='primary' if rt_mode=='DAYTRADE' else 'secondary',key='v38day') and rt_mode!='DAYTRADE':
        set_runtime_mode('DAYTRADE');st.rerun()
with h3:
    sess=str(status.get('session') or status.get('market_session') or '-')
    mk='KR' if market=='KOREA' else 'US'
    stream=str(rt.get('streaming') or '-')
    trsec=str(rt.get('tracker_seconds') or '-')
    fsec=str(rt.get('finder_seconds') or '-')
    st.markdown('<div class="v38-status">'+mk+' <span class="g">● '+sess+'</span> &nbsp;&nbsp; ENGINE5 V22 &nbsp;&nbsp; <span class="g">LIVE</span><br><span style="font-size:11px;color:#7f8da2">Streaming '+stream+' · Tracker '+trsec+'s · Finder '+fsec+'s</span></div>',unsafe_allow_html=True)

if market=='KOREA':
    total=f(kr_state.get('total_assets')); cash=f(kr_state.get('cash')); invested=f(kr_state.get('stock_value')); holds=len(kr_state.get('holdings') or [])
    upd=str(kr_state.get('updated_at') or '-')
    if 'T' in upd: upd=upd.split('T',1)[1][:8]
else:
    pr,_=position_rows(); total=0; cash=0; invested=0; holds=len([x for x in pr if str(x.get('market') or '').upper() in ('',market)]); upd='-'
summary=[('총자산',money(total,market),'실시간 계좌'),('투자금(평가)',money(invested,market),'보유 평가액'),('현금',money(cash,market),'주문 가능 기준'),('오늘 손익','-','실현/평가 연동'),('보유 종목',str(holds),'현재 계좌'),('세션',str(status.get('session') or '-'),'09:00 - 15:30' if market=='KOREA' else 'US session'),('후보',str(len(finders)),'Finder TOP 20' if market=='KOREA' else 'Finder TOP 5'),('최종 업데이트',upd,'실시간')]
box='<div class="v38-summary"><div class="v38-kpi-grid">'
for a,b,c in summary:
    box+='<div class="v38-kpi"><div class="v38-label">'+str(a)+'</div><div class="v38-val">'+str(b)+'</div><div class="v38-subval">'+str(c)+'</div></div>'
box+='</div></div>'
st.markdown(box,unsafe_allow_html=True)

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
    if not BACKUP.exists():
        shutil.copy2(APP,BACKUP);print('BACKUP',BACKUP,flush=True)
    text=replace_function(text,'render_trading',TRADING)
    anchors=["st.title('DAY TRADER V5')",'# V37_TERMINAL_DESIGN','# V38_TERMINAL_DESIGN']
    pos=-1
    for a in anchors:
        p=text.find(a)
        if p>=0: pos=p;break
    if pos<0: raise SystemExit('ABORT top-level anchor missing')
    text=text[:pos]+TOP+'\n'
    tmp=APP.with_suffix('.py.v38tmp')
    tmp.write_text(text,encoding='utf-8')
    try:
        py_compile.compile(str(tmp),doraise=True)
        APP.write_text(text,encoding='utf-8')
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass
    print('APP_V5_DESIGN_PATCH=PASS',flush=True)
    subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False)
    time.sleep(1)
    cmd=f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup /home/ubuntu/day-trader-api/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &'
    subprocess.Popen(['bash','-lc',cmd],start_new_session=True)
    deadline=time.time()+45;last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=2) as r:
                if r.status==200:
                    print('V5_HTTP=PASS',flush=True);break
        except Exception as e: last=e
        time.sleep(2)
    else:
        raise SystemExit(f'ABORT V5 startup failed: {last}; log={LOG}')
    print('V5_DESIGN=REFERENCE_TERMINAL_V38',flush=True)
    print('KR_FINDER_UI=TOP20_PRESERVED',flush=True)
    print('V22_BACKEND=UNTOUCHED',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__':
    main()
