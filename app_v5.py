import os
import requests
import pandas as pd
import streamlit as st

try:
    API_URL = st.secrets.get('DAYTRADER_API_URL', '')
except Exception:
    API_URL = os.getenv('DAYTRADER_API_URL', '')
API_URL = str(API_URL).rstrip('/')

st.set_page_config(page_title='DAY TRADER V5', page_icon='📈', layout='wide', initial_sidebar_state='collapsed')
st.markdown('''
<style>
.block-container{padding-top:.35rem;padding-bottom:1rem;max-width:1800px}
h1{font-size:2rem!important;margin:.15rem 0!important} h2,h3{margin:.25rem 0!important}
p{margin:.15rem 0 .3rem 0!important}
[data-testid="stMetric"]{padding:.05rem .15rem}
[data-testid="stMetricLabel"]{font-size:.78rem}
[data-testid="stMetricValue"]{font-size:1.25rem}
[data-testid="stMetricDelta"]{font-size:.72rem}
[data-testid="stVerticalBlock"]{gap:.42rem}
[data-testid="stDataFrame"]{margin:.1rem 0}
.stTabs [data-baseweb="tab-list"]{gap:.25rem}.stTabs [data-baseweb="tab"]{height:2.35rem;padding:0 .75rem}
.v5-card{border:1px solid #30363d;border-radius:10px;padding:9px 12px;margin:4px 0 7px;background:#11151b}
.v5-action{font-size:1.08rem;font-weight:800}.v5-muted{opacity:.72;font-size:.82rem}.v5-kicker{font-size:.68rem;letter-spacing:.07em;opacity:.62}
.v5-note{border-left:3px solid #4f8cff;padding:7px 10px;background:#111820;border-radius:7px;margin:.3rem 0 .5rem;font-size:.82rem}
div[data-testid="stAlert"]{padding:.45rem .7rem}.stButton button{min-height:2rem;padding:.25rem .7rem}
</style>
''', unsafe_allow_html=True)

def api(path, timeout=10):
    if not API_URL: return {'ok':False,'error':'DAYTRADER_API_URL is empty'}
    try:
        r=requests.get(API_URL+path,timeout=timeout); r.raise_for_status(); return r.json()
    except Exception as e: return {'ok':False,'error':str(e)}

def post(path,payload,timeout=10):
    if not API_URL: return {'ok':False,'error':'DAYTRADER_API_URL is empty'}
    try:
        r=requests.post(API_URL+path,json=payload,timeout=timeout); r.raise_for_status(); return r.json()
    except Exception as e: return {'ok':False,'error':str(e)}

def f(v,default=0.0):
    try:return float(v)
    except Exception:return default

def money(v,market):
    x=f(v); return f'{x:,.0f}원' if market=='KOREA' else f'${x:,.2f}'

def action_of(row):
    proto=str(row.get('prototype_action') or row.get('proto_action') or '').upper(); state=str(row.get('state') or '').upper(); grade=str((row.get('entry_gate') or {}).get('signal_grade') or '').upper()
    if proto:return proto
    if state in {'HARD_EXIT','EXIT_READY'}:return 'EXIT_REVIEW'
    if state=='PARTIAL_EXIT':return 'REDUCE_REVIEW'
    if grade in {'ENTRY','ENTRY_CANDIDATE'} or state=='ENTRY':return 'BUY_REVIEW'
    if grade in {'READY','READY_STRONG'} or state in {'READY','SETUP'}:return 'WAIT'
    if state=='HOLD':return 'HOLD'
    return 'WATCH'

def action_ko(a):
    return {'BUY_REVIEW':'매수 검토','ADD_REVIEW':'추가매수 검토','HOLD':'보유','HOLD_WATCH':'보유 관찰','WAIT':'대기','WATCH':'관찰','REDUCE_REVIEW':'비중축소 검토','EXIT_REVIEW':'매도 검토','AVOID':'회피','DATA_WAIT':'데이터 대기'}.get(str(a),str(a))

def get_market_status(market):return api(f'/api/v4/{market}/status',15)
def tracker_rows(status):return (status.get('tracker') or {}).get('rows') or []
def finder_rows(status):
    finder=status.get('finder') or {}; return finder.get('rows') or status.get('finder_rows') or []

def recommendation_table(rows,market,limit=5):
    out=[]
    for r in rows[:limit]:
        gate=r.get('entry_gate') or {}; out.append({'종목':r.get('symbol') or '-','종목명':r.get('name') or r.get('symbol') or '-','판단':action_ko(action_of(r)),'현재가':money(r.get('price') or r.get('current_price'),market),'Power':round(f(r.get('power')),1),'상태':r.get('state') or gate.get('signal_grade') or '-','위험':r.get('risk') or r.get('risk_level') or '-'})
    return pd.DataFrame(out)

def render_buy_box(r,market):
    symbol=r.get('symbol') or '-'; price=max(f(r.get('price') or r.get('current_price')),0.0)
    st.markdown('##### 💰 매수 계산')
    c1,c2,c3=st.columns([1,1,.65]); buy_px=c1.number_input('실제 매수가',min_value=0.0,value=price,step=100.0 if market=='KOREA' else 0.01,key=f'px_{market}_{symbol}'); amount=c2.number_input('투입 금액',min_value=0.0,value=0.0,step=100000.0 if market=='KOREA' else 100.0,key=f'amt_{market}_{symbol}'); qty=int(amount//buy_px) if buy_px>0 else 0; c3.metric('예상 수량',f'{qty:,}주')
    actual=qty*buy_px; st.caption(f'체결 {money(actual,market)} · 잔여 {money(max(amount-actual,0),market)} · 수동 장부 등록')
    if st.button('보유 단타 등록',disabled=(qty<=0 or buy_px<=0),key=f'reg_{market}_{symbol}'):
        result=post('/api/v4/position/buy',{'market':market,'symbol':symbol,'qty':qty,'price':buy_px,'note':'V5 manual registration'})
        if result.get('ok'):st.success(f'{symbol} {qty:,}주 등록 완료');st.rerun()
        else:st.error(f"등록 실패: {result.get('error') or result}")

def render_selected_detail(r,market):
    symbol=r.get('symbol') or '-'; name=r.get('name') or symbol; act=action_of(r); price=r.get('price') or r.get('current_price'); power=f(r.get('power')); reason=r.get('prototype_reason') or r.get('reason') or r.get('core_reason') or '엔진 판단 근거 대기'; risk=r.get('risk') or r.get('risk_level') or '-'
    st.markdown('### 🎯 선택 종목')
    c1,c2,c3,c4=st.columns(4); c1.metric('판단',action_ko(act));c2.metric('현재가',money(price,market));c3.metric('Power',f'{power:+.1f}');c4.metric('위험',risk)
    st.markdown(f'<div class="v5-card"><div class="v5-kicker">SELECTED · {market}</div><div class="v5-action">{name} ({symbol})</div><div class="v5-muted">{reason}</div></div>',unsafe_allow_html=True)
    render_buy_box(r,market)
    with st.expander('세부 지표 / 엔진 근거',expanded=False):
        gate=r.get('entry_gate') or {};comp=r.get('components') or {};d={'State':r.get('state'),'Prototype Action':r.get('prototype_action'),'Signal Grade':gate.get('signal_grade'),'Power':r.get('power'),'ΔPower':r.get('power_delta'),'Direction':r.get('direction'),'5m Setup':comp.get('shadow_setup_count') or r.get('setup_count'),'1m Trigger':comp.get('shadow_trigger_count') or r.get('trigger_count'),'RVOL':r.get('rvol'),'MFI':r.get('mfi14') or r.get('mfi'),'VO':r.get('vo')};st.json({k:v for k,v in d.items() if v is not None})

def position_values(p,market):
    avg=f(p.get('avg_price') or p.get('entry_price'));qty=f(p.get('qty') or p.get('quantity'));cur=f(p.get('current_price') or p.get('price'));value=cur*qty;cost=avg*qty;pnl=value-cost;pct=(pnl/cost*100) if cost else 0;floor=p.get('hard_floor') or p.get('floor') or p.get('dynamic_floor');ceiling=p.get('dynamic_ceiling') or p.get('ceiling');t1=p.get('t1') or p.get('target1');t2=p.get('t2') or p.get('target2');return avg,qty,cur,pnl,pct,floor,ceiling,t1,t2

def render_positions(market,tracker):
    st.markdown('### 🛡 실제 보유 단타');positions=api('/api/v4/positions',10);pos_rows=positions.get('data') if isinstance(positions,dict) else None;shown=0;tracker_hold={str(r.get('symbol')) for r in tracker if action_of(r) in {'HOLD','HOLD_WATCH'}}
    for p in pos_rows or []:
        if str(p.get('market') or '').upper() not in {'',market}:continue
        shown+=1;sym=p.get('symbol') or '-';live=next((r for r in tracker if str(r.get('symbol'))==str(sym)),None)
        if live and not (p.get('current_price') or p.get('price')):p=dict(p);p['current_price']=live.get('price') or live.get('current_price')
        avg,qty,cur,pnl,pct,floor,ceiling,t1,t2=position_values(p,market);c1,c2,c3,c4,c5=st.columns(5);c1.metric(sym,f'{qty:,.0f}주');c2.metric('현재',money(cur,market));c3.metric('평단',money(avg,market));c4.metric('손익',money(pnl,market),f'{pct:+.2f}%');c5.metric('판단',action_ko(action_of(p)))
        st.markdown(f'<div class="v5-card"><b>Floor</b> {money(floor,market) if floor is not None else "-"} · <b>Ceiling</b> {money(ceiling,market) if ceiling is not None else "-"} · <b>T1</b> {money(t1,market) if t1 is not None else "-"} · <b>T2</b> {money(t2,market) if t2 is not None else "-"}</div>',unsafe_allow_html=True)
    if not shown:st.caption('실제 등록된 단타 포지션이 없습니다.')
    if tracker_hold:st.markdown(f'<div class="v5-note"><b>Tracker HOLD:</b> {", ".join(sorted(tracker_hold))} · 실제 보유는 포지션 API 등록 종목만 표시</div>',unsafe_allow_html=True)

def render_trading(market):
    status=get_market_status(market);rows=tracker_rows(status);finders=finder_rows(status);session=status.get('session') or status.get('market_session') or '-';source=rows if rows else finders
    top1,top2,top3,top4=st.columns(4);top1.metric('시장',market);top2.metric('세션',session);top3.metric('후보',len(finders));top4.metric('관리',len(rows))
    left,right=st.columns([1.45,1],gap='medium')
    with left:
        st.markdown('### ⚡ 지금 단타 후보')
        if source:
            st.dataframe(recommendation_table(source,market),width='stretch',hide_index=True,height=220)
            options=[];by_label={}
            for r in source[:5]:
                sym=r.get('symbol') or '-';name=r.get('name') or sym;label=f'{sym} · {name} · {action_ko(action_of(r))}';options.append(label);by_label[label]=r
            selected=st.selectbox('상세 종목',options,key=f'selected_{market}',label_visibility='collapsed');render_selected_detail(by_label[selected],market)
        else:st.info('현재 추천/Tracker 데이터가 없습니다.')
    with right:
        render_positions(market,rows)
        with st.expander('📈 중장기 후보'):st.info('장기 엔진/월봉·기본정보 연결 단계')

def render_portfolio():
    st.header('💼 Portfolio');c1,c2,c3,c4=st.columns(4);c1.metric('총 자산','DB 연결 예정');c2.metric('현금','-');c3.metric('주식','-');c4.metric('오늘 손익','-');st.info('다음 단계: holdings / portfolio_daily_snapshots DB와 API 연결')
def render_briefing():st.header('📰 Market Briefing');st.info('KOSPI · KOSDAQ · S&P500 · Nasdaq · Dow · USD/KRW · 핵심 뉴스 · 인기 테마 · 보유종목 뉴스')
def render_settings():
    st.header('⚙️ Settings');c1,c2=st.columns(2)
    with c1:st.toggle('📰 07:00 Morning Brief',True,disabled=True);st.toggle('⚡ 단타 BUY / ADD',True,disabled=True);st.toggle('🚨 긴급 EXIT / 손절',True,disabled=True)
    with c2:st.toggle('📈 중장기 추천',True,disabled=True);st.toggle('🛡 보유종목 중요 변화',True,disabled=True);st.toggle('📊 일일 자산 결산',False,disabled=True)

h1,h2=st.columns([2.2,1]);h1.title('📈 DAY TRADER V5');market=h2.radio('시장',['USA','KOREA'],horizontal=True,key='v5_market',label_visibility='collapsed');st.caption('DECISION TERMINAL · 무엇을 살지 → 얼마를 살지 → 어떻게 관리할지 · MANUAL ORDER')
tab_trade,tab_port,tab_news,tab_settings,tab_debug=st.tabs(['⚡ Trading','💼 Portfolio','📰 Market Briefing','⚙️ Settings','🧪 Legacy / Debug'])
with tab_trade:render_trading(market)
with tab_port:render_portfolio()
with tab_news:render_briefing()
with tab_settings:render_settings()
with tab_debug:st.warning('기존 V4 진단 기능은 삭제하지 않고 분리 유지합니다.');st.code('streamlit run app.py')
