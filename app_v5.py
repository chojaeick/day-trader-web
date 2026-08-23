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
.block-container{padding-top:1.55rem;padding-bottom:.7rem;max-width:1800px}
.v5-title{font-size:2rem;font-weight:850;line-height:1.2;margin:.25rem 0 .1rem 0;white-space:nowrap}
.v5-sub{font-size:.78rem;opacity:.72;margin-bottom:.3rem}
h1,h2,h3{margin:.12rem 0!important} p{margin:.08rem 0 .18rem 0!important}
[data-testid="stMetric"]{padding:.02rem .06rem}
[data-testid="stMetricLabel"]{font-size:.68rem}
[data-testid="stMetricValue"]{font-size:1.02rem}
[data-testid="stMetricDelta"]{font-size:.64rem}
[data-testid="stVerticalBlock"]{gap:.25rem}
.stTabs [data-baseweb="tab-list"]{gap:.12rem}.stTabs [data-baseweb="tab"]{height:2.05rem;padding:0 .55rem}
.v5-card{border:1px solid #30363d;border-radius:8px;padding:6px 9px;margin:2px 0 4px;background:#11151b}
.v5-muted{opacity:.7;font-size:.74rem}.v5-kicker{font-size:.62rem;letter-spacing:.06em;opacity:.62}
.v5-warn{border-left:3px solid #ffb020;padding:5px 8px;background:#1b170d;border-radius:6px;margin:.2rem 0 .3rem;font-size:.74rem}
.v5-note{border-left:3px solid #4f8cff;padding:5px 8px;background:#111820;border-radius:6px;margin:.2rem 0 .3rem;font-size:.74rem}
.hold-head{font-size:.72rem;opacity:.62;margin-bottom:1px}.hold-val{font-size:.95rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hold-symbol{font-size:1.02rem;font-weight:850}.hold-sub{font-size:.68rem;opacity:.65}
div[data-testid="stAlert"]{padding:.35rem .55rem}.stButton button{min-height:1.75rem;padding:.15rem .5rem}
</style>
''', unsafe_allow_html=True)

def api(path, timeout=10):
    if not API_URL:return {'ok':False,'error':'DAYTRADER_API_URL is empty'}
    try:
        r=requests.get(API_URL+path,timeout=timeout);r.raise_for_status();return r.json()
    except Exception as e:return {'ok':False,'error':str(e)}

def post(path,payload,timeout=10):
    if not API_URL:return {'ok':False,'error':'DAYTRADER_API_URL is empty'}
    try:
        r=requests.post(API_URL+path,json=payload,timeout=timeout);r.raise_for_status();return r.json()
    except Exception as e:return {'ok':False,'error':str(e)}

def f(v,default=0.0):
    try:return float(v)
    except Exception:return default

def first_value(d,*keys):
    for k in keys:
        v=d.get(k) if isinstance(d,dict) else None
        if v is not None and v!='':return v
    return None

def money(v,market,missing='-'):
    if v is None or v=='':return missing
    x=f(v);return f'{x:,.0f}원' if market=='KOREA' else f'${x:,.2f}'

def action_of(row):
    row=row or {};proto=str(row.get('prototype_action') or row.get('proto_action') or '').upper();state=str(row.get('state') or '').upper();grade=str((row.get('entry_gate') or {}).get('signal_grade') or '').upper()
    if proto:return proto
    if state in {'HARD_EXIT','EXIT_READY'}:return 'EXIT_REVIEW'
    if state=='PARTIAL_EXIT':return 'REDUCE_REVIEW'
    if grade in {'ENTRY','ENTRY_CANDIDATE'} or state=='ENTRY':return 'BUY_REVIEW'
    if grade in {'READY','READY_STRONG'} or state in {'READY','SETUP'}:return 'WAIT'
    if state=='HOLD':return 'HOLD'
    return 'WATCH'

def action_ko(a):
    return {'BUY_REVIEW':'매수 검토','ADD_REVIEW':'추가매수','HOLD':'유지','HOLD_WATCH':'유지/관찰','WAIT':'대기','WATCH':'관찰','REDUCE_REVIEW':'비중축소','EXIT_REVIEW':'매도 검토','AVOID':'회피','DATA_WAIT':'데이터 대기','UP_HOLD':'상승 보유'}.get(str(a),str(a))

def get_market_status(market):return api(f'/api/v4/{market}/status',15)
def tracker_rows(status):return (status.get('tracker') or {}).get('rows') or []
def finder_rows(status):
    finder=status.get('finder') or {};return finder.get('rows') or status.get('finder_rows') or []

def position_rows():
    x=api('/api/v4/positions',10)
    if not isinstance(x,dict):return [],x
    rows=x.get('data') or x.get('rows') or x.get('positions') or []
    return rows if isinstance(rows,list) else [],x

def recommendation_table(rows,market,limit=5):
    out=[]
    for r in rows[:limit]:
        gate=r.get('entry_gate') or {}
        out.append({'종목':r.get('symbol') or '-','종목명':r.get('name') or r.get('symbol') or '-','판단':action_ko(action_of(r)),'현재가':money(r.get('price') or r.get('current_price'),market),'Power':round(f(r.get('power')),1),'상태':r.get('state') or gate.get('signal_grade') or '-','위험':r.get('risk') or r.get('risk_level') or '-'})
    return pd.DataFrame(out)

def normalize_position(p,live=None):
    base=p.get('position') if isinstance(p.get('position'),dict) else {};m={**base,**p}
    avg=first_value(m,'avg_entry','avg_price','average_price','avg_cost','average_cost','entry_price','buy_price','registered_price','registered_avg_price')
    qty=first_value(m,'qty','quantity','shares','registered_qty');cur=first_value(m,'current_price','last_price','market_price','price')
    if live:cur=first_value(live,'price','current_price','last_price') or cur
    pnl=first_value(m,'unrealized_pnl','pnl','profit_loss','eval_pnl');pct=first_value(m,'unrealized_pct','pnl_pct','profit_rate','return_pct')
    avg_n=f(avg,None) if avg is not None else None;qty_n=f(qty,0);cur_n=f(cur,None) if cur is not None else None
    if pnl is None and avg_n is not None and cur_n is not None:pnl=(cur_n-avg_n)*qty_n
    if pct is None and avg_n not in (None,0) and cur_n is not None:pct=(cur_n/avg_n-1)*100
    return {'symbol':m.get('symbol') or '-','avg':avg_n,'qty':qty_n,'cur':cur_n,'pnl':f(pnl,None) if pnl is not None else None,'pct':f(pct,None) if pct is not None else None,'floor':first_value(m,'hard_floor','floor','dynamic_floor','current_floor'),'warning_floor':first_value(m,'warning_floor','warn_floor'),'ceiling':first_value(m,'dynamic_ceiling','ceiling'),'t1':first_value(m,'t1','target1'),'t2':first_value(m,'t2','target2')}

def engine_matrix(live):
    cp=f((live or {}).get('power'),None) if live else None
    return pd.DataFrame([
        {'엔진':'Core','상태':'LIVE' if live else '대기','점수':f'{cp:+.1f}' if cp is not None else '-','판단':action_ko(action_of(live)) if live else '데이터 대기','위험':(live or {}).get('risk') or (live or {}).get('risk_level') or '-'},
        {'엔진':'Fujimoto','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'MA20','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Ethan','상태':'검증중','점수':'-','판단':'V-zone 재현 대기','위험':'-'},
        {'엔진':'Jared 3/4','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Predator','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'}])

def render_manual_holding(market):
    with st.expander('➕ 보유주식 직접 등록',expanded=False):
        c1,c2,c3=st.columns([1.1,.7,1]);symbol=c1.text_input('종목코드',placeholder='SOXL / NVDA / 005930',key=f'msym_{market}').strip().upper();qty=c2.number_input('수량',min_value=0,value=0,step=1,key=f'mqty_{market}');avg=c3.number_input('평균매수가',min_value=0.0,value=0.0,step=100.0 if market=='KOREA' else 0.01,key=f'mavg_{market}')
        if st.button('보유주식 등록',disabled=(not symbol or qty<=0 or avg<=0),key=f'mreg_{market}'):
            result=post('/api/v4/position/buy',{'market':market,'symbol':symbol,'qty':int(qty),'price':float(avg),'note':'V5 manual holding registration'})
            if result.get('ok'):st.success(f'{symbol} 등록 완료');st.rerun()
            else:st.error(f"등록 실패: {result.get('error') or result}")

def render_buy_box(r,market):
    symbol=r.get('symbol') or '-';price=max(f(r.get('price') or r.get('current_price')),0.0)
    st.markdown('##### 💰 매수 계산');c1,c2,c3=st.columns([1,1,.65]);buy_px=c1.number_input('실제 매수가',min_value=0.0,value=price,step=100.0 if market=='KOREA' else 0.01,key=f'px_{market}_{symbol}');amount=c2.number_input('투입 금액',min_value=0.0,value=0.0,step=100000.0 if market=='KOREA' else 100.0,key=f'amt_{market}_{symbol}');qty=int(amount//buy_px) if buy_px>0 else 0;c3.metric('예상 수량',f'{qty:,}주')
    if st.button('보유 단타 등록',disabled=(qty<=0 or buy_px<=0),key=f'reg_{market}_{symbol}'):
        result=post('/api/v4/position/buy',{'market':market,'symbol':symbol,'qty':qty,'price':buy_px,'note':'V5 manual registration'})
        if result.get('ok'):st.success(f'{symbol} {qty:,}주 등록 완료');st.rerun()
        else:st.error(f"등록 실패: {result.get('error') or result}")

def render_selected_detail(r,market):
    symbol=r.get('symbol') or '-';name=r.get('name') or symbol;reason=r.get('prototype_reason') or r.get('reason') or r.get('core_reason') or '엔진 판단 근거 대기'
    st.markdown('### 🎯 선택 종목');c1,c2,c3,c4=st.columns(4);c1.metric('판단',action_ko(action_of(r)));c2.metric('현재가',money(r.get('price') or r.get('current_price'),market));c3.metric('Power',f"{f(r.get('power')):+.1f}");c4.metric('위험',r.get('risk') or r.get('risk_level') or '-')
    st.markdown(f'<div class="v5-card"><div class="v5-kicker">SELECTED · {market}</div><b>{name} ({symbol})</b><div class="v5-muted">{reason}</div></div>',unsafe_allow_html=True);render_buy_box(r,market)

def render_positions(market,tracker):
    title,add=st.columns([2.5,1]);title.markdown('### 🛡 실제 보유 종목')
    with add:render_manual_holding(market)
    pos_rows,_=position_rows();shown=0
    for raw in pos_rows:
        if str(raw.get('market') or '').upper() not in {'',market}:continue
        sym=raw.get('symbol') or (raw.get('position') or {}).get('symbol') or '-';live=next((r for r in tracker if str(r.get('symbol')).upper()==str(sym).upper()),None);p=normalize_position(raw,live);shown+=1
        c0,c1,c2,c3,c4,c5,c6=st.columns([1.05,.8,1,1,1.15,1.05,.58])
        with c0:st.markdown(f'<div class="hold-symbol">{sym}</div><div class="hold-sub">{p["qty"]:,.0f}주</div>',unsafe_allow_html=True)
        with c1:st.markdown(f'<div class="hold-head">현재</div><div class="hold-val">{money(p["cur"],market,"미연결")}</div>',unsafe_allow_html=True)
        with c2:st.markdown(f'<div class="hold-head">평단</div><div class="hold-val">{money(p["avg"],market,"미연결")}</div>',unsafe_allow_html=True)
        with c3:st.markdown(f'<div class="hold-head">손익</div><div class="hold-val">{money(p["pnl"],market,"미연결")}</div>',unsafe_allow_html=True)
        with c4:st.markdown(f'<div class="hold-head">수익률</div><div class="hold-val">{f"{p[\"pct\"]:+.2f}%" if p["pct"] is not None else "-"}</div>',unsafe_allow_html=True)
        with c5:st.markdown(f'<div class="hold-head">종합판단</div><div class="hold-val">{action_ko(action_of(live)) if live else "미커버"}</div>',unsafe_allow_html=True)
        with c6:
            remove=st.button('삭제',key=f'del_{market}_{sym}',use_container_width=True)
        if remove:
            if p['qty']<=0:st.warning(f'{sym} 수량이 0이라 삭제할 수 없습니다.')
            else:
                close_px=p['avg'] or p['cur'] or 1.0
                result=post('/api/v4/position/sell',{'market':market,'symbol':sym,'qty':p['qty'],'price':close_px,'note':'V5 MANUAL LEDGER REMOVE - ZERO PNL CORRECTION'})
                if result.get('ok'):st.success(f'{sym} 보유 장부에서 제거했습니다.');st.rerun()
                else:st.error(f"삭제 실패: {result.get('error') or result}")
        meta=f'Floor {money(p["floor"],market)} · Warning {money(p["warning_floor"],market)} · Ceiling {money(p["ceiling"],market)} · T1 {money(p["t1"],market)} · T2 {money(p["t2"],market)}'
        st.caption(meta)
        with st.expander(f'🧠 {sym} 엔진별 평가',expanded=False):
            st.dataframe(engine_matrix(live),use_container_width=True,hide_index=True,height=228)
            if live is None:st.markdown('<div class="v5-warn">Core Tracker 미커버. 나머지 엔진은 실제 신호 연결 전이라 임의 점수를 만들지 않습니다.</div>',unsafe_allow_html=True)
        st.divider()
    if not shown:st.caption('등록된 보유종목이 없습니다.')

def render_trading(market):
    status=get_market_status(market);rows=tracker_rows(status);finders=finder_rows(status);source=rows if rows else finders;session=status.get('session') or status.get('market_session') or '-'
    a,b,c,d=st.columns(4);a.metric('시장','미국장' if market=='USA' else '국장');b.metric('세션',session);c.metric('후보',len(finders));d.metric('관리',len(rows))
    left,right=st.columns([1.15,1.2],gap='medium')
    with left:
        st.markdown('### ⚡ 지금 단타 후보')
        if source:
            st.dataframe(recommendation_table(source,market),use_container_width=True,hide_index=True,height=205)
            labels=[];lookup={}
            for r in source[:5]:
                label=f"{r.get('symbol') or '-'} · {r.get('name') or r.get('symbol') or '-'} · {action_ko(action_of(r))}";labels.append(label);lookup[label]=r
            sel=st.selectbox('상세 종목',labels,key=f'sel_{market}',label_visibility='collapsed');render_selected_detail(lookup[sel],market)
        else:st.info('현재 추천/Tracker 데이터가 없습니다.')
    with right:render_positions(market,rows)

def render_portfolio(market):
    rows,_=position_rows();status=get_market_status(market);live_rows=tracker_rows(status);table=[]
    for p in rows:
        if str(p.get('market') or '').upper() not in {'',market}:continue
        sym=p.get('symbol') or (p.get('position') or {}).get('symbol') or '-';live=next((r for r in live_rows if str(r.get('symbol')).upper()==str(sym).upper()),None);x=normalize_position(p,live)
        table.append({'종목':sym,'수량':int(x['qty']),'평단':money(x['avg'],market,'미연결'),'현재':money(x['cur'],market,'미연결'),'손익':money(x['pnl'],market,'미연결'),'수익률':f"{x['pct']:+.2f}%" if x['pct'] is not None else '-','판단':action_ko(action_of(live)) if live else '미커버'})
    st.markdown('### 💼 Portfolio')
    if table:st.dataframe(pd.DataFrame(table),use_container_width=True,hide_index=True)
    render_manual_holding(market)

def render_briefing(market):
    status=get_market_status(market);rows=tracker_rows(status);finders=finder_rows(status);st.markdown('### 📰 Market Briefing');c1,c2,c3=st.columns(3);c1.metric('시장','미국장' if market=='USA' else '국장');c2.metric('관리종목',len(rows));c3.metric('신규후보',len(finders));
    if finders:st.dataframe(recommendation_table(finders,market),use_container_width=True,hide_index=True,height=210)
    st.info('지수·환율·뉴스·테마 데이터는 다음 연결 단계입니다.')

def render_settings():
    st.markdown('### ⚙️ Settings');c1,c2=st.columns(2)
    with c1:st.toggle('📰 07:00 Morning Brief',value=True);st.toggle('⚡ 단타 BUY / ADD',value=True);st.toggle('🚨 긴급 EXIT / 손절',value=True)
    with c2:st.toggle('📈 중장기 추천',value=True);st.toggle('🛡 보유종목 중요 변화',value=True);st.toggle('📊 일일 자산 결산',value=False)

st.markdown('<div class="v5-title">📈 DAY TRADER V5</div><div class="v5-sub">DECISION TERMINAL · 무엇을 살지 → 얼마를 살지 → 어떻게 관리할지 · MANUAL ORDER</div>',unsafe_allow_html=True)
if 'v5_market' not in st.session_state:st.session_state['v5_market']='USA'
mc1,mc2,mc3=st.columns([.72,.72,4.5]);usa=mc1.button('🇺🇸 미국장',use_container_width=True,type='primary' if st.session_state['v5_market']=='USA' else 'secondary');kor=mc2.button('🇰🇷 국장',use_container_width=True,type='primary' if st.session_state['v5_market']=='KOREA' else 'secondary')
if usa:st.session_state['v5_market']='USA';st.rerun()
if kor:st.session_state['v5_market']='KOREA';st.rerun()
market=st.session_state['v5_market']
t1,t2,t3,t4,t5=st.tabs(['⚡ Trading','💼 Portfolio','📰 Market Briefing','⚙️ Settings','🧪 Legacy / Debug'])
with t1:render_trading(market)
with t2:render_portfolio(market)
with t3:render_briefing(market)
with t4:render_settings()
with t5:
    st.warning('기존 V4 진단 기능은 삭제하지 않고 분리 유지합니다.')
    with st.expander('V5 포지션 API 원본 확인'):
        _,raw=position_rows();st.json(raw)
    st.code('streamlit run app.py')
