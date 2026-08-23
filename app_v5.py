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

/* ===== V11 VISUAL OVERHAUL ===== */
.block-container{padding-top:.75rem!important;max-width:1540px!important;padding-left:1.15rem!important;padding-right:1.15rem!important}
html,body,[class*="css"]{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.v5-title{font-size:2.15rem!important;font-weight:900!important;letter-spacing:-.035em;margin:.15rem 0 0!important}
.v5-sub{font-size:.72rem!important;color:#8b98ab!important;margin:.05rem 0 .35rem!important}
h1,h2,h3{letter-spacing:-.025em!important}
hr{border-color:#223044!important;margin:.55rem 0!important}
[data-testid="stVerticalBlock"]{gap:.42rem!important}
[data-testid="stHorizontalBlock"]{gap:.65rem!important}
[data-testid="stMetric"]{background:linear-gradient(180deg,#0e1826 0%,#0a121d 100%);border:1px solid #20334b;border-radius:10px;padding:.55rem .7rem!important;box-shadow:0 8px 24px rgba(0,0,0,.16)}
[data-testid="stMetricLabel"]{font-size:.68rem!important;color:#94a3b8!important;font-weight:700!important}
[data-testid="stMetricValue"]{font-size:1.18rem!important;font-weight:850!important;letter-spacing:-.02em}
.v5-card{background:linear-gradient(180deg,#0c1725 0%,#0a131f 100%)!important;border:1px solid #213652!important;border-radius:12px!important;padding:10px 12px!important;box-shadow:0 10px 28px rgba(0,0,0,.15)!important}
.v5-note{background:#0c1d31!important;border:1px solid #17395f!important;border-left:3px solid #258cff!important;border-radius:9px!important}
.v5-warn{background:#241d0e!important;border:1px solid #493812!important;border-left:3px solid #f6b73c!important;border-radius:9px!important}
.hold-symbol{font-size:1.05rem!important;font-weight:900!important;letter-spacing:-.02em!important}
.hold-sub{font-size:.66rem!important;color:#7890ac!important}
.hold-head{font-size:.64rem!important;color:#8190a5!important;text-transform:uppercase!important;letter-spacing:.04em!important}
.hold-val{font-size:.94rem!important;font-weight:800!important}
.stButton>button{border-radius:8px!important;border:1px solid #2a3a51!important;background:#101a28!important;font-weight:750!important;transition:.15s ease!important}
.stButton>button:hover{border-color:#258cff!important;color:#fff!important;transform:translateY(-1px)!important}
.stButton>button[kind="primary"]{background:linear-gradient(180deg,#1988ff,#0764d8)!important;border-color:#2c92ff!important;box-shadow:0 5px 16px rgba(16,116,255,.24)!important}
[data-testid="stExpander"]{border:1px solid #20344e!important;border-radius:10px!important;background:#0a1420!important;overflow:hidden!important}
[data-testid="stExpander"] summary{font-weight:800!important;background:#0d1826!important}
[data-testid="stDataFrame"]{border:1px solid #20334b!important;border-radius:10px!important;overflow:hidden!important}
[data-baseweb="input"]{background:#0d1724!important;border-color:#273a53!important;border-radius:8px!important}
[data-baseweb="select"]>div{background:#0d1724!important;border-color:#273a53!important;border-radius:8px!important}
.stTabs [data-baseweb="tab-list"]{background:#09111b!important;border-bottom:1px solid #203044!important;padding:.18rem!important;border-radius:9px 9px 0 0!important}
.stTabs [data-baseweb="tab"]{font-weight:750!important;border-radius:7px!important}
/* make trading top two columns feel like panels */
div[data-testid="column"]:has(h3){min-width:0}
/* cleaner alerts */
div[data-testid="stAlert"]{border-radius:9px!important;border:1px solid #26384e!important;padding:.45rem .65rem!important}
/* compact top controls */
@media (min-width:1100px){.block-container{padding-top:.55rem!important}.stButton button{min-height:2rem!important}}

</style>
<style>
:root{--v5-bg:#0a0f17;--v5-panel:#0d1623;--v5-border:#20334a;--v5-text:#eef5ff;--v5-muted:#8092aa;--v5-blue:#1f8cff;--v5-green:#00d97e;--v5-red:#ff4d61;--v5-amber:#ffb020}
.stApp{background:radial-gradient(circle at 70% -10%,#0d2035 0,#0a0f17 34%,#080d14 72%);color:var(--v5-text)}
.block-container{padding-top:.55rem!important;padding-bottom:1rem!important;max-width:1540px!important}
.v5-title{font-size:2.18rem!important;font-weight:900!important;letter-spacing:-.04em;margin:0!important}
.v5-sub{font-size:.72rem!important;color:var(--v5-muted)!important;margin:.08rem 0 .28rem!important}
h1,h2,h3{letter-spacing:-.025em}
[data-testid="stVerticalBlock"]{gap:.32rem!important}
[data-testid="stHorizontalBlock"]{gap:.7rem!important}
[data-testid="stMetric"]{background:linear-gradient(180deg,#101b29,#0c1521);border:1px solid var(--v5-border);border-radius:10px;padding:.48rem .72rem!important;min-height:72px}
[data-testid="stMetricLabel"]{color:#8da0b9!important;font-size:.7rem!important}
[data-testid="stMetricValue"]{font-size:1.22rem!important;font-weight:800!important}
[data-testid="stDataFrame"]{border:1px solid var(--v5-border);border-radius:10px;overflow:hidden;background:#0b131e}
.v5-card{background:linear-gradient(180deg,#0e1927,#0b1420)!important;border:1px solid var(--v5-border)!important;border-radius:10px!important;padding:10px 12px!important;box-shadow:0 8px 24px rgba(0,0,0,.16)}
.hold-symbol{font-size:1rem!important;font-weight:850!important;color:#f5f8ff}.hold-sub{font-size:.62rem!important;color:#70839c!important}
.hold-head{font-size:.64rem!important;color:#7d90aa!important}.hold-val{font-size:.92rem!important;font-weight:780!important}
.stButton>button{border-radius:8px!important;border:1px solid #29415c!important;background:#101a27!important;font-weight:720!important;min-height:2rem!important}
.stButton>button[kind="primary"]{background:linear-gradient(180deg,#238cff,#0875e8)!important;border-color:#3b9cff!important;color:white!important}
[data-baseweb="select"]>div,[data-baseweb="input"]{border-radius:8px!important;background:#111925!important;border-color:#27384d!important}
[data-testid="stExpander"]{border:1px solid var(--v5-border)!important;border-radius:10px!important;background:#0b1420!important}
hr{border-color:#1d2a3a!important;margin:.45rem 0!important}
</style>

''', unsafe_allow_html=True)
st.markdown('''
<style>
:root{--v5-bg:#08111f;--v5-panel:#0d1726;--v5-line:#20324a;--v5-text:#eaf2ff;--v5-muted:#8292aa;--v5-blue:#2788ff;--v5-green:#20d87a;--v5-red:#ff4d5e;--v5-amber:#ffb020}
.block-container{max-width:1680px!important;padding:1.0rem 1.35rem 1.2rem!important}
.v5-title{font-size:2.35rem!important;font-weight:900!important;letter-spacing:-.04em;margin:0!important}
.v5-sub{color:#8492a8!important;font-size:.78rem!important}
[data-testid="stHorizontalBlock"]{gap:.7rem!important}
[data-testid="stDataFrame"]{border:1px solid #20324a;border-radius:12px;overflow:hidden}
div[data-testid="stExpander"]{border:1px solid #20324a!important;border-radius:12px!important;background:#0b1422!important}
.v5-section{border:1px solid #20324a;border-radius:14px;background:linear-gradient(180deg,#0d1828 0%,#0a1320 100%);padding:14px 16px;margin:5px 0 10px}
.v5-section-title{font-size:1.22rem;font-weight:850;margin-bottom:4px}.v5-section-sub{color:#8190a7;font-size:.72rem}
.v5-kpi{border:1px solid #20324a;border-radius:10px;padding:10px 12px;background:#0b1524;min-height:72px}.v5-kpi-label{color:#8190a7;font-size:.68rem}.v5-kpi-value{font-size:1.1rem;font-weight:800;margin-top:3px}
.v5-good{color:#20d87a}.v5-bad{color:#ff5a69}.v5-warn-t{color:#ffb020}.v5-blue{color:#3d95ff}
.stButton>button{border-radius:9px!important;font-weight:700!important}
[data-baseweb="select"]>div,[data-testid="stNumberInput"] input,[data-testid="stTextInput"] input{border-radius:8px!important}
</style>
<style>
[data-testid="stDataFrame"] [role="columnheader"]{font-size:.68rem!important;color:#8fa2bb!important}
[data-testid="stDataFrame"] [role="gridcell"]{font-size:.78rem!important}
.v12-name{line-height:1.15!important;white-space:normal!important}
.v12-code{margin-top:2px!important}
[data-testid="stMetric"]{min-height:68px!important}
[data-testid="stMetricValue"]{white-space:normal!important;line-height:1.08!important}
[data-testid="stExpander"] summary{min-height:2.15rem!important}
</style>

''',unsafe_allow_html=True)

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


def runtime_mode_bar():
    state=api('/api/v4/runtime-mode',5)
    mode=str(state.get('mode') or 'NORMAL').upper()
    c1,c2,c3,c4=st.columns([1.0,1.0,1.15,3.2])
    c1.markdown('**⚙ 분석모드**')
    if c2.button('NORMAL',use_container_width=True,type='primary' if mode=='NORMAL' else 'secondary',key='mode_normal'):
        post('/api/v4/runtime-mode/NORMAL',{},5);st.rerun()
    if c3.button('⚡ DAYTRADE',use_container_width=True,type='primary' if mode=='DAYTRADE' else 'secondary',key='mode_daytrade'):
        post('/api/v4/runtime-mode/DAYTRADE',{},5);st.rerun()
    c4.caption(f"{mode} · Tracker {state.get('tracker_seconds','-')}s · Finder {state.get('finder_seconds','-')}s · Streaming ALWAYS ON")

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

def get_runtime_mode():
    return api('/api/v4/runtime-mode',5)

def set_runtime_mode(mode):
    return post(f'/api/v4/runtime-mode/{str(mode).upper()}',{},5)

def get_market_status(market):return api(f'/api/v4/{market}/status',15)
def tracker_rows(status):return (status.get('tracker') or {}).get('rows') or []
def finder_rows(status):
    finder=status.get('finder') or {};return finder.get('rows') or status.get('finder_rows') or []

@st.cache_data(ttl=20,show_spinner=False)
def quote_snapshot(symbol,market='USA'):
    sym=str(symbol or '').upper().strip()
    path=f'/api/v5/korea-quote/{sym}' if market=='KOREA' else f'/api/quote/{sym}'
    x=api(path,8)
    return x if isinstance(x,dict) and not x.get('error') and x.get('ok',True) else {}

def standby_candidates(status,market,limit=5):
    out=[]; seen=set()
    for e in (status.get('events') or []):
        if str(e.get('event_type') or '').upper()!='TOP5_IN': continue
        sym=str(e.get('symbol') or '').upper()
        if not sym or sym in seen: continue
        seen.add(sym)
        q=quote_snapshot(sym,market)
        out.append({
            'symbol':sym,
            'name':q.get('name') or sym,
            'price':q.get('price') or q.get('last') or q.get('close'),
            'power':None,
            'state':'STANDBY',
            'risk':'-',
            'prototype_action':'WATCH',
            'reason':'최근 TOP5 기록 · NORMAL 대기모드에서는 실시간 Tracker 계산을 중지합니다.',
            '_standby':True,
        })
        if len(out)>=limit: break
    return out

def position_rows():
    x=api('/api/v4/positions',10)
    if not isinstance(x,dict):return [],x
    rows=x.get('data') or x.get('rows') or x.get('positions') or []
    return rows if isinstance(rows,list) else [],x

def holding_profile(market,symbol):
    x=api(f'/api/v5/holding-profile/{market}/{symbol}',5)
    return str(x.get('holding_type') or 'SHORT_TERM').upper()

def set_holding_profile(market,symbol,holding_type):
    return post('/api/v5/holding-profile',{
        'market':market,'symbol':symbol,
        'holding_type':holding_type,'source':'MANUAL'
    },5)

def search_symbol_ui(market,query):
    q=str(query or '').strip()
    if not q:return []
    if market=='KOREA':
        x=api(f'/api/v5/korea-symbol-search?q={requests.utils.quote(q)}&limit=12',8)
        return x.get('rows') or [] if isinstance(x,dict) else []
    # USA: keep symbol-first until a dedicated company-name directory is wired.
    return []

def validate_symbol_ui(market,query):
    q=str(query or '').strip()
    if not q:return {'ok':False,'valid':False,'reason':'EMPTY'}
    return api(f'/api/v5/symbol-validate/{market}/{q}',8)


@st.cache_data(ttl=300,show_spinner=False)
def resolve_display_name(market,symbol,fallback=''):
    sym=str(symbol or '').strip().upper()
    fb=str(fallback or '').strip()
    if not sym:
        return fb or '-'
    if market=='KOREA':
        try:
            rows=search_symbol_ui('KOREA',sym)
            for r in rows:
                if str(r.get('symbol') or '').strip().upper()==sym:
                    nm=str(r.get('name') or '').strip()
                    if nm and nm!=sym:
                        return nm
        except Exception:
            pass
    return fb if fb and fb!=sym else sym


def enrich_display_names(rows,market):
    out=[]
    for src in rows or []:
        r=dict(src)
        sym=str(r.get('symbol') or '').strip().upper()
        old=str(r.get('name') or '').strip()
        r['name']=resolve_display_name(market,sym,old)
        out.append(r)
    return out

def recommendation_table(rows,market,limit=5):
    rows=enrich_display_names(rows,market) if 'enrich_display_names' in globals() else rows
    out=[]
    for r in rows[:limit]:
        gate=r.get('entry_gate') or {}
        sym=str(r.get('symbol') or '-')
        name=resolve_display_name(market,sym,r.get('name') or '') if 'resolve_display_name' in globals() else (r.get('name') or sym)
        out.append({
            '종목':f'{name}  ·  {sym}',
            '판단':action_ko(action_of(r)),
            '현재가':money(r.get('price') or r.get('current_price'),market),
            'Power':('-' if r.get('power') is None else round(f(r.get('power')),1)),
            '상태':r.get('state') or gate.get('signal_grade') or '-',
            '위험':r.get('risk') or r.get('risk_level') or '-'
        })
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

# FUJIMOTO_V01_REJECT: 369 trades @ cost 0.20%, WR 20.33%, PF 0.384, NET -73.402%. Informational only; excluded from aggregate vote.
def engine_matrix(live):
    cp=f((live or {}).get('power'),None) if live else None
    return pd.DataFrame([
        {'엔진':'Core','상태':'LIVE' if live else '대기','점수':f'{cp:+.1f}' if cp is not None else '-','판단':action_ko(action_of(live)) if live else '데이터 대기','위험':(live or {}).get('risk') or (live or {}).get('risk_level') or '-'},
        {'엔진':'Fujimoto','상태':'검증완료','점수':'PF 0.384','판단':'비채택 · v0.1','위험':'REJECT'},
        {'엔진':'MA20','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Ethan','상태':'검증중','점수':'-','판단':'V-zone 재현 대기','위험':'-'},
        {'엔진':'Jared 3/4','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Predator','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'}])

def render_manual_holding(market,scope='holdings'):
    with st.expander('＋ 보유주식 등록',expanded=False):
        st.caption('국장은 종목명 또는 종목코드로 검색할 수 있습니다. 실제 상장 종목만 등록됩니다.')
        a,b,c,d=st.columns([1.35,.65,.9,.8])
        raw=a.text_input('종목 검색',placeholder='삼성전자 / KODEX 미국S&P500 / 005930 / 0193T0',key=f'msym_{market}_{scope}').strip()
        qty=b.number_input('수량',min_value=0,value=0,step=1,key=f'mqty_{market}_{scope}')
        avg=c.number_input('평균매수가',min_value=0.0,value=0.0,step=100.0 if market=='KOREA' else 0.01,key=f'mavg_{market}_{scope}')
        kind=d.selectbox('투자유형',['단타','중장기'],key=f'mkind_{market}_{scope}')

        selected_symbol=''; selected_name=''
        if market=='KOREA' and raw:
            rows=search_symbol_ui(market,raw)
            if rows:
                labels=[f"{r.get('name') or r.get('symbol')}  ·  {r.get('symbol')}" for r in rows]
                chosen=st.selectbox('검색 결과',labels,key=f'mpick_{market}_{scope}')
                hit=rows[labels.index(chosen)]
                selected_symbol=str(hit.get('symbol') or '').upper()
                selected_name=str(hit.get('name') or selected_symbol)
        elif raw:
            selected_symbol=raw.upper()

        check=validate_symbol_ui(market,selected_symbol) if selected_symbol else {'valid':False}
        if selected_symbol:
            if check.get('valid'):
                resolved_name=(check.get('name') or selected_name or selected_symbol)
                st.success(f"확인됨 · {resolved_name} · {selected_symbol}")
            else:
                reason=check.get('reason') or check.get('error') or '미확인 종목'
                st.error(f'등록 불가 · {reason}')
        enabled=bool(check.get('valid') and qty>0 and avg>0)
        if st.button('확인된 종목을 보유주식으로 등록',type='primary',disabled=not enabled,key=f'mreg_{market}_{scope}',use_container_width=True):
            symbol=str(check.get('symbol') or selected_symbol).upper()
            result=post('/api/v4/position/buy',{'market':market,'symbol':symbol,'qty':int(qty),'price':float(avg),'note':'V5 verified manual holding registration'})
            if result.get('ok'):
                p=set_holding_profile(market,symbol,'SHORT_TERM' if kind=='단타' else 'LONG_TERM')
                if p.get('ok'): st.success(f'{symbol} 등록 완료'); st.rerun()
                else: st.warning(f'종목 등록 완료, 투자유형 저장 실패: {p}')
            else: st.error(f"등록 실패: {result.get('error') or result}")

def render_buy_box(r,market):
    symbol=r.get('symbol') or '-';price=max(f(r.get('price') or r.get('current_price')),0.0)
    st.markdown('##### 💰 매수 계산');c1,c2,c3=st.columns([1,1,.65]);buy_px=c1.number_input('실제 매수가',min_value=0.0,value=price,step=100.0 if market=='KOREA' else 0.01,key=f'px_{market}_{symbol}');amount=c2.number_input('투입 금액',min_value=0.0,value=0.0,step=100000.0 if market=='KOREA' else 100.0,key=f'amt_{market}_{symbol}');qty=int(amount//buy_px) if buy_px>0 else 0;c3.metric('예상 수량',f'{qty:,}주')
    if st.button('보유 단타 등록',disabled=(qty<=0 or buy_px<=0),key=f'reg_{market}_{symbol}'):
        result=post('/api/v4/position/buy',{'market':market,'symbol':symbol,'qty':qty,'price':buy_px,'note':'V5 manual registration'})
        if result.get('ok'):st.success(f'{symbol} {qty:,}주 등록 완료');st.rerun()
        else:st.error(f"등록 실패: {result.get('error') or result}")

def render_selected_detail(r,market):
    symbol=r.get('symbol') or '-';name=resolve_display_name(market,symbol,r.get('name') or '');reason=r.get('prototype_reason') or r.get('reason') or r.get('core_reason') or '엔진 판단 근거 대기'
    st.markdown('<div class="v5-section-title">🎯 선택 종목 상세</div>',unsafe_allow_html=True)
    a,b,c,d=st.columns(4)
    a.metric('종목',name);b.metric('현재가',money(r.get('price') or r.get('current_price'),market));c.metric('Power',f"{f(r.get('power')):+.1f}");d.metric('판단',action_ko(action_of(r)))
    st.markdown(f'<div class="v5-section"><b>{name}</b><div class="v5-section-sub">{reason}</div></div>',unsafe_allow_html=True)
    with st.expander('엔진 평가 요약',expanded=False):
        st.dataframe(engine_matrix(r),use_container_width=True,hide_index=True,height=245)
    with st.expander('매수 계산 / 보유등록',expanded=False): render_buy_box(r,market)

@st.cache_data(ttl=300,show_spinner=False)
def holding_display_name(market,symbol):
    return resolve_display_name(market,symbol,'')


def render_positions(market,tracker):
    st.markdown('<div class="v5-section-title">🛡 보유주식 관리</div><div class="v5-section-sub">전체 폭 관리 · 단타/중장기 즉시 전환 · 검증된 종목만 등록</div>',unsafe_allow_html=True)
    render_manual_holding(market,'holdings')
    pos_rows,_=position_rows(); shown=0
    for raw in pos_rows:
        if str(raw.get('market') or '').upper() not in {'',market}: continue
        sym=raw.get('symbol') or (raw.get('position') or {}).get('symbol') or '-'
        live=next((r for r in tracker if str(r.get('symbol')).upper()==str(sym).upper()),None)
        quote=quote_snapshot(sym,market) if not live else {}
        p=normalize_position(raw,live or quote);shown+=1
        current_type=holding_profile(market,sym); pct='-' if p['pct'] is None else f"{p['pct']:+.2f}%"
        pnl_cls='v5-good' if (p['pnl'] or 0)>=0 else 'v5-bad'
        if live:
            judgment=action_ko(action_of(live))
        elif current_type=='LONG_TERM':
            judgment='중장기 평가대기'
        else:
            judgment='단타 대기'
        c0,c1,c2,c3,c4,c5,c6,c7=st.columns([1.1,.9,.9,.75,.82,1.05,1.05,.58])
        display_name=holding_display_name(market,sym)
        c0.markdown(f'**{display_name}**  \n<span style="color:#8190a7;font-size:.68rem">{sym} · {p["qty"]:,.0f}주</span>',unsafe_allow_html=True)
        c1.markdown(f'현재가  \n**{money(p["cur"],market,"-")}**')
        c2.markdown(f'평균가  \n**{money(p["avg"],market,"-")}**')
        c3.markdown(f'손익  \n<span class="{pnl_cls}"><b>{money(p["pnl"],market,"-")}</b></span>',unsafe_allow_html=True)
        c4.markdown(f'수익률  \n<span class="{pnl_cls}"><b>{pct}</b></span>',unsafe_allow_html=True)
        c5.markdown(f'판단  \n**{judgment}**')
        new_label=c6.selectbox('투자유형',['단타','중장기'],index=0 if current_type=='SHORT_TERM' else 1,key=f'kind_{market}_{sym}',label_visibility='collapsed')
        new_type='SHORT_TERM' if new_label=='단타' else 'LONG_TERM'
        if new_type!=current_type and c6.button('변경',key=f'kind_apply_{market}_{sym}',use_container_width=True):
            rr=set_holding_profile(market,sym,new_type)
            if rr.get('ok'): st.rerun()
            else: st.error(f'구분 변경 실패: {rr}')
        if c7.button('삭제',key=f'del_{market}_{sym}',use_container_width=True):
            close_px=p['avg'] or p['cur'] or 1.0
            rr=post('/api/v4/position/sell',{'market':market,'symbol':sym,'qty':p['qty'],'price':close_px,'note':'V5 manual ledger remove'})
            if rr.get('ok'): st.rerun()
            else: st.error(f"삭제 실패: {rr.get('error') or rr}")
        with st.expander(f'{sym} 상세 엔진 평가',expanded=False):
            if live:
                st.dataframe(engine_matrix(live),hide_index=True,use_container_width=True,height=220)
            else:
                msg='중장기 평가엔진 연결 대기' if current_type=='LONG_TERM' else 'DAYTRADE 활성화 시 단타 엔진 평가 재개'
                st.info(msg)
                st.dataframe(engine_matrix(None),hide_index=True,use_container_width=True,height=220)
        st.divider()
    if shown==0: st.info('등록된 실제 보유종목이 없습니다.')

def render_trading(market):
    status=get_market_status(market);rows=tracker_rows(status);finders=finder_rows(status);active=rows if rows else finders;session=status.get('session') or status.get('market_session') or '-'
    standby=standby_candidates(status,market,5) if not active else []
    source=active or standby
    a,b,c,d=st.columns(4)
    a.metric('시장','미국장' if market=='USA' else '국장');b.metric('세션',session)
    c.metric('후보',len(finders) if active else ('대기' if standby else 0));d.metric('관리',len(rows))
    left,right=st.columns([1.05,1.35],gap='large')
    with left:
        title='⚡ 지금 단타 후보 TOP 5' if active else '🕘 최근 단타 후보 TOP 5'
        sub='후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.' if active else 'NORMAL/CLOSED 상태 · 마지막 TOP5 기록을 참고용으로 표시합니다. 실시간 추천이 아닙니다.'
        status_badge='● LIVE' if active else '● STANDBY'
        st.caption(status_badge)
        st.markdown(f'<div class="v5-section-title">{title}</div><div class="v5-section-sub">{sub}</div>',unsafe_allow_html=True)
        if source:
            st.dataframe(recommendation_table(source,market),use_container_width=True,hide_index=True,height=205)
            labels=[];lookup={}
            for r in source[:5]:
                pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'
                label=f"{r.get('symbol') or '-'} · {action_ko(action_of(r))} · Power {ptxt}";labels.append(label);lookup[label]=r
            sel=st.selectbox('후보 선택',labels,key=f'sel_{market}',label_visibility='collapsed')
            selected=lookup[sel]
        else:
            st.info('현재 추천/Tracker 데이터가 없습니다. DAYTRADE를 켜거나 장 시작 후 갱신을 기다려주세요.')
            selected=None
    with right:
        if selected:
            render_selected_detail(selected,market)
        else:
            st.markdown('<div class="v5-section"><b>선택 종목 없음</b><div class="v5-section-sub">후보가 생성되면 상세 평가가 이 영역에 표시됩니다.</div></div>',unsafe_allow_html=True)
    st.divider()
    render_positions(market,rows)

def render_portfolio(market):
    rows,_=position_rows();status=get_market_status(market);live_rows=tracker_rows(status);table=[]
    for p in rows:
        if str(p.get('market') or '').upper() not in {'',market}:continue
        sym=p.get('symbol') or (p.get('position') or {}).get('symbol') or '-';live=next((r for r in live_rows if str(r.get('symbol')).upper()==str(sym).upper()),None);x=normalize_position(p,live)
        table.append({'종목':sym,'수량':int(x['qty']),'평단':money(x['avg'],market,'미연결'),'현재':money(x['cur'],market,'미연결'),'손익':money(x['pnl'],market,'미연결'),'수익률':f"{x['pct']:+.2f}%" if x['pct'] is not None else '-','판단':action_ko(action_of(live)) if live else '미커버'})
    st.markdown('### 💼 Portfolio')
    if table:st.dataframe(pd.DataFrame(table),use_container_width=True,hide_index=True)
    render_manual_holding(market,'portfolio')

def render_briefing(market):
    status=get_market_status(market);rows=tracker_rows(status);finders=finder_rows(status);st.markdown('### 📰 Market Briefing');c1,c2,c3=st.columns(3);c1.metric('시장','미국장' if market=='USA' else '국장');c2.metric('관리종목',len(rows));c3.metric('신규후보',len(finders));
    if finders:st.dataframe(recommendation_table(finders,market),use_container_width=True,hide_index=True,height=210)
    st.info('지수·환율·뉴스·테마 데이터는 다음 연결 단계입니다.')

def render_settings():
    st.markdown('### ⚙️ Settings');c1,c2=st.columns(2)
    with c1:st.toggle('📰 07:00 Morning Brief',value=True);st.toggle('⚡ 단타 BUY / ADD',value=True);st.toggle('🚨 긴급 EXIT / 손절',value=True)
    with c2:st.toggle('📈 중장기 추천',value=True);st.toggle('🛡 보유종목 중요 변화',value=True);st.toggle('📊 일일 자산 결산',value=False)

st.title('DAY TRADER V5')
st.caption('DECISION TERMINAL · MANUAL ORDER · 실시간 연결은 항상 유지, 단타 분석만 필요할 때 가속')
if 'v5_market' not in st.session_state:
    st.session_state['v5_market']='USA'

# Market selector + runtime mode are deliberately always visible above tabs.
mc1,mc2,mc3,mc4,mc5=st.columns([.8,.8,1.25,1.25,2.6])
usa=mc1.button('🇺🇸 미국장',use_container_width=True,type='primary' if st.session_state['v5_market']=='USA' else 'secondary')
kor=mc2.button('🇰🇷 국장',use_container_width=True,type='primary' if st.session_state['v5_market']=='KOREA' else 'secondary')
if usa:
    st.session_state['v5_market']='USA'
    st.rerun()
if kor:
    st.session_state['v5_market']='KOREA'
    st.rerun()
market=st.session_state['v5_market']

rt=get_runtime_mode()
rt_mode=str(rt.get('mode') or 'UNKNOWN').upper()
normal=mc3.button('NORMAL 대기',use_container_width=True,type='primary' if rt_mode=='NORMAL' else 'secondary')
daytrade=mc4.button('⚡ DAYTRADE',use_container_width=True,type='primary' if rt_mode=='DAYTRADE' else 'secondary')
if normal and rt_mode!='NORMAL':
    rr=set_runtime_mode('NORMAL')
    if rr.get('ok'): st.rerun()
    else: st.error(f'NORMAL 전환 실패: {rr}')
if daytrade and rt_mode!='DAYTRADE':
    rr=set_runtime_mode('DAYTRADE')
    if rr.get('ok'): st.rerun()
    else: st.error(f'DAYTRADE 전환 실패: {rr}')

streaming=rt.get('streaming') or '-'
tracker_sec=rt.get('tracker_seconds') or '-'
finder_sec=rt.get('finder_seconds') or '-'
mc5.markdown(f'**MODE {rt_mode}**  \nStreaming `{streaming}` · Tracker `{tracker_sec}s` · Finder `{finder_sec}s`')
if rt_mode=='NORMAL':
    st.caption('🟢 NORMAL: 키움/WS/시세 연결 유지 · 무거운 단타 Finder/Tracker 계산 대기')
elif rt_mode=='DAYTRADE':
    st.warning('⚡ DAYTRADE ON: 고속 단타 분석 활성화. 실제 단타가 끝나면 NORMAL로 복귀하세요.')
else:
    st.warning('런타임 모드 확인 실패. API 상태를 확인하세요.')
runtime_mode_bar()
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
