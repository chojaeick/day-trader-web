import os, time, requests, pandas as pd, streamlit as st
try: API_URL=st.secrets.get('DAYTRADER_API_URL','')
except Exception: API_URL=os.getenv('DAYTRADER_API_URL','')
API_URL=str(API_URL).rstrip('/')
st.set_page_config(page_title='DAY TRADER V4',page_icon='📈',layout='wide',initial_sidebar_state='collapsed')
st.markdown('<style>.block-container{padding-top:1rem;max-width:1500px}[data-testid="stMetricValue"]{font-size:1.55rem}</style>',unsafe_allow_html=True)
def api(path,timeout=10):
    try:r=requests.get(API_URL+path,timeout=timeout); r.raise_for_status(); return r.json()
    except Exception as e:return {'ok':False,'error':str(e)}
def post(path,payload,timeout=20):
    try:
        r=requests.post(API_URL+path,json=payload,timeout=timeout)
        return r.json() if r.status_code<400 else {'ok':False,'error':r.text}
    except Exception as e:return {'ok':False,'error':str(e)}
def f(v,d=0):
    try:return float(v)
    except Exception:return d
def sko(s):return {'REGULAR':'정규장 거래중','PREMARKET':'프리마켓','AFTER':'애프터마켓','PREOPEN':'장 시작 전','CLOSED':'장 마감'}.get(str(s),str(s or '-'))
def stko(s):return {'WATCH':'관찰','SETUP':'준비','READY':'진입 준비','ENTRY':'진입 신호','HOLD':'보유 유지','TAKE_PROFIT':'일부 익절 검토','REDUCE':'비중 축소','EXIT':'청산','STOP':'손절','DATA_INVALID':'데이터 오류'}.get(str(s),str(s or '-'))
def rko(s):return {'NORMAL':'정상','CHASE':'추격주의','HIGH':'고위험','PENDING':'대기'}.get(str(s),str(s or '-'))
def _focus_bars(rows, minutes_back):
    if not rows:return []
    try:
        df=pd.DataFrame(rows)
        tcol='time' if 'time' in df.columns else ('ts' if 'ts' in df.columns else None)
        if not tcol:return rows[-120:]
        df[tcol]=pd.to_datetime(df[tcol],utc=True,errors='coerce')
        df=df.dropna(subset=[tcol]).sort_values(tcol)
        if df.empty:return rows[-120:]
        cut=df[tcol].iloc[-1]-pd.Timedelta(minutes=minutes_back)
        return df[df[tcol]>=cut].to_dict('records')
    except Exception:
        return rows[-120:]

def _chart_frame(rows):
    if not rows:return None
    try:
        df=pd.DataFrame(rows)
        tcol='time' if 'time' in df.columns else ('ts' if 'ts' in df.columns else None)
        if not tcol or 'close' not in df.columns:return None
        df[tcol]=pd.to_datetime(df[tcol],utc=True,errors='coerce')
        df=df.dropna(subset=[tcol]).sort_values(tcol)
        if df.empty:return None
        close=pd.to_numeric(df['close'],errors='coerce')
        vol=pd.to_numeric(df.get('volume',0),errors='coerce').fillna(0)
        high=pd.to_numeric(df.get('high',close),errors='coerce').fillna(close)
        low=pd.to_numeric(df.get('low',close),errors='coerce').fillna(close)
        tp=(high+low+close)/3
        cv=vol.cumsum()
        df['VWAP']=(tp*vol).cumsum()/cv.replace(0,pd.NA)
        df['EMA9']=close.ewm(span=9,adjust=False).mean()
        df['EMA20']=close.ewm(span=20,adjust=False).mean()
        df['Price']=close
        return df[[tcol,'Price','VWAP','EMA9','EMA20']].rename(columns={tcol:'time'}).set_index('time')
    except Exception:
        return None

def px(v,m):
    if v in (None,''):return '-'
    try:return f'${float(v):,.2f}' if m=='USA' else f'{float(v):,.0f}원'
    except Exception:return '-'
def controls(m,row):
    sym=row['symbol']; pos=bool(row.get('position_open'))
    with st.expander(f"수동 매매 입력 · {row.get('name') or sym} ({sym})"):
        if not pos:
            a,b,c=st.columns(3); q=a.number_input('매수 수량',min_value=0.0,step=1.0,key=f'bq{m}{sym}'); p=b.number_input('실제 매수가',min_value=0.0,step=.01 if m=='USA' else 10.0,key=f'bp{m}{sym}')
            if c.button('매수 등록',key=f'buy{m}{sym}',use_container_width=True):
                rr=post('/api/v4/position/buy',{'market':m,'symbol':sym,'qty':q,'price':p}); st.success('매수 등록 완료') if rr.get('ok') else st.error(rr.get('error')); st.rerun() if rr.get('ok') else None
        else:
            held=f(row.get('qty')); st.caption(f'현재 보유 {held:g}주 · 평균단가 {px(row.get("avg_entry"),m)}'); a,b,c=st.columns(3); q=a.number_input('매도 수량',min_value=0.0,max_value=max(held,0.0),step=1.0,key=f'sq{m}{sym}'); p=b.number_input('실제 매도가',min_value=0.0,step=.01 if m=='USA' else 10.0,key=f'sp{m}{sym}')
            if c.button('매도 등록',key=f'sell{m}{sym}',use_container_width=True):
                rr=post('/api/v4/position/sell',{'market':m,'symbol':sym,'qty':q,'price':p}); st.success('매도 등록 완료') if rr.get('ok') else st.error(rr.get('error')); st.rerun() if rr.get('ok') else None
st.title('📈 DAY TRADER V4'); st.caption('CLEAN ENGINE · Finder → 최대 5종목 Tracker → Data Integrity Gate → 5m Setup + 1m Trigger → Position / Exit → Validation · NO AUTO ORDER')
ml=st.radio('시장',['🇺🇸 USA','🇰🇷 KOREA'],horizontal=True,label_visibility='collapsed'); m='USA' if 'USA' in ml else 'KOREA'; t=st.tabs(['📈 Trading','🗞️ Briefing','🧪 Validation','📚 Archive'])
# Real-time display option: backend always refreshes every 5s.
# UI auto-refresh is opt-in so manual order-entry fields are not unexpectedly reset.
auto_live=st.toggle('실시간 화면 자동갱신 (5초)',value=False,key='v4_auto_live')

with t[0]:
    s=api(f'/api/v4/{m}/status'); tr=s.get('tracker') or {}; fi=s.get('finder') or {}; rows=tr.get('rows') or []; fr=fi.get('rows') or []; pos=s.get('positions') or []; ev=s.get('events') or []
    a,b,c,d=st.columns(4); a.metric('현재 시장',sko(s.get('session'))); b.metric('Finder 후보',len(fr)); c.metric('실시간 추적',tr.get('tracked_count',len(rows))); d.metric('보유 종목',len(pos)); a.caption('시장 시간'); b.caption('전체 시장 저빈도 선별'); c.caption('Heavy Tracker 최대 5개'); d.caption('추적 슬롯 최우선')
    if m=='KOREA':st.info('국장은 현재 체결강도 기반 Power까지만 사용합니다. 검증된 1분/5분봉 Gate 연결 전에는 ENTRY 신호를 내지 않습니다.')
    live_now=bool(tr.get('is_live'))
    st.markdown('### 🔥 실시간 Power 순위' if live_now else '### 🕘 장 마감 기준 Power 순위')
    if not live_now: st.caption('현재 장중 실시간 값이 아니라 마지막 사용 가능한 시장 데이터 기준 참고값입니다.')
    if rows:
        st.dataframe(pd.DataFrame([{'실시간순위':r.get('tracker_rank') or '-','Finder순위':r.get('finder_rank') or '-','종목':r.get('symbol'),'종목명':r.get('name'),'상태':stko(r.get('state')),'방향':r.get('direction'),'힘':r.get('power_label') or '-','Power':r.get('power'),'ΔPower':r.get('power_delta'),'위험':rko(r.get('risk')),'데이터':('정상' if (r.get('data_integrity') or {}).get('valid',True) else 'INVALID'),'현재가':r.get('price'),'보유':'YES' if r.get('position_open') else '','핵심 이유':r.get('reason')} for r in rows]),use_container_width=True,hide_index=True)
        pri={'STOP':0,'EXIT':1,'REDUCE':2,'TAKE_PROFIT':3,'ENTRY':4,'READY':5,'SETUP':6,'HOLD':7,'WATCH':8,'DATA_INVALID':99}; lead=sorted(rows,key=lambda r:(pri.get(r.get('state'),99),-abs(f(r.get('power')))))[0]; st.markdown('### 🚨 지금 가장 중요한 행동' if live_now else '### 📌 마지막 상태 요약'); st.info(f"{'장 마감 참고 · ' if not live_now else ''}{lead.get('name')} ({lead.get('symbol')}) · **{stko(lead.get('state'))}** · {lead.get('power_label') or ''} Power {f(lead.get('power')):+.0f} ({f(lead.get('power_delta')):+.0f}) · {lead.get('reason')}")
        sel=st.selectbox('종목 상세',[r['symbol'] for r in rows],format_func=lambda x:next((f"{r.get('name')} ({x})" for r in rows if r['symbol']==x),x)); r=next(x for x in rows if x['symbol']==sel); q1,q2,q3,q4,q5=st.columns(5); q1.metric('상태',stko(r.get('state'))); q2.metric(r.get('power_label') or 'Power',f"{f(r.get('power')):+.0f}",delta=f"{f(r.get('power_delta')):+.0f}"); q3.metric('현재가',px(r.get('price'),m)); q4.metric('Floor 모드',r.get('floor_mode') or '-'); q5.metric('위험',rko(r.get('risk')))
        if m=='USA':
            di=r.get('data_integrity') or {}
            if not di.get('valid',True):
                st.error('DATA INVALID · 신호/Floor/Target 계산 중단 · '+' / '.join(di.get('reasons') or []))
                st.caption(f"마지막 1분봉 {di.get('last_1m_time') or '-'} / 마지막 5분봉 {di.get('last_5m_time') or '-'} / 1분 종가 {di.get('last_1m_close') or '-'} / 5분 종가 {di.get('last_5m_close') or '-'}")
            elif not live_now:
                st.info('장 마감 참고 상태입니다. Floor/T1/T2는 정규장 중에만 계산합니다.')
            else:
                a,b,c,d=st.columns(4); a.metric('경고 Floor',px(r.get('warning_floor'),m)); b.metric('Hard Floor',px(r.get('hard_floor'),m)); c.metric('T1',px(r.get('target1'),m)); d.metric('T2',px(r.get('target2'),m))
            comp=r.get('components') or {}; st.caption(f"Power 구성 · 가격구조 {f(comp.get('structure')):+.0f} / 거래량 {f(comp.get('volume')):+.0f} / 모멘텀 {f(comp.get('momentum')):+.0f} / 시장·섹터 {f(comp.get('market_sector')):+.0f} / 위험감점 {f(comp.get('risk_penalty')):.0f}")
            gate=r.get('entry_gate') or {}
            if gate:
                g1,g2,g3=st.columns(3)
                g1.metric('5분 Setup',f"{gate.get('setup_count',0)}/{gate.get('setup_total',4)}",delta='완료' if gate.get('setup_ok') else '대기')
                g2.metric('1분 Trigger',f"{gate.get('trigger_count',0)}/{gate.get('trigger_total',5)}",delta='진입' if gate.get('entry') else '준비' if gate.get('ready') else '대기')
                g3.metric('추격 방지','통과' if gate.get('chase_ok') else '차단')
                labels={'price_above_vwap':'가격 > VWAP','ema9_above_ema20':'EMA9 > EMA20','five_min_rising':'5분 상승','five_min_structure':'5분 구조',
                        'green_1m':'1분 양봉','break_prev_high':'직전 1분 고가 돌파','volume_expansion':'거래량 ≥1.5x','one_min_impulse':'1분 +0.15%','power_acceleration':'Power ≥60 & Δ≥4'}
                checks=[]
                for k,v in (gate.get('setup_checks') or {}).items(): checks.append(('✅' if v else '⬜')+' '+labels.get(k,k))
                for k,v in (gate.get('trigger_checks') or {}).items(): checks.append(('✅' if v else '⬜')+' '+labels.get(k,k))
                st.caption(' · '.join(checks))
            b1=api(f'/api/bars/{sel}?minutes=1&limit=240').get('data') or []
            b5=api(f'/api/bars/{sel}?minutes=5&limit=240').get('data') or []
            b1f=_focus_bars(b1,60); b5f=_focus_bars(b5,180); c1,c2=st.columns(2)
            with c1:
                st.caption('1분봉 · Trigger · 최근 60분')
                cf1=_chart_frame(b1f)
                if cf1 is not None and len(cf1):st.line_chart(cf1,height=260)
                else:st.info('1분봉 데이터 준비 중')
            with c2:
                st.caption('5분봉 · Setup · 최근 3시간')
                cf5=_chart_frame(b5f)
                if cf5 is not None and len(cf5):st.line_chart(cf5,height=260)
                else:st.info('5분봉 데이터 준비 중')
        controls(m,r)
    else:st.warning('Tracker 데이터가 아직 없습니다. 서버 시작 직후라면 수 초 후 자동 생성됩니다.')
    st.markdown('### 🎯 Finder TOP5 · 오늘 볼 종목'); st.caption('Finder는 종목 선정용입니다. 위 Power 순위는 진입 준비도를 실시간으로 다시 정렬합니다. TOP5 진입 = 즉시 매수가 아닙니다.')
    if fr:st.dataframe(pd.DataFrame([{'순위':r.get('rank'),'종목':r.get('symbol'),'종목명':r.get('name'),'Finder점수':r.get('finder_score'),'방향':r.get('direction'),'등락률%':r.get('change_pct'),'RVOL':r.get('rvol'),'ATR%':r.get('atr_pct'),'위험':rko(r.get('risk'))} for r in fr]),use_container_width=True,hide_index=True)
    with st.expander('🔔 최근 의미있는 변화'):
        if ev:
            df=pd.DataFrame(ev); keep=[x for x in ['ts','symbol','event_type','state_from','state_to','power','rank_from','rank_to','message'] if x in df.columns]; st.dataframe(df[keep],use_container_width=True,hide_index=True)
        else:st.caption('아직 이벤트가 없습니다.')
    if st.button('화면 데이터 다시 읽기'):st.rerun()
with t[1]:
    st.subheader('🗞️ Briefing')
    if m=='USA':
        latest=api('/api/briefing/latest?market=USA'); report=latest.get('data') if isinstance(latest,dict) and 'data' in latest else latest
        if report:
            text=report.get('report_text') or report.get('summary') or ''
            if text:st.markdown(text)
            if report.get('rows'):st.dataframe(pd.DataFrame(report.get('rows')),use_container_width=True,hide_index=True)
        else:st.info('저장된 미국 브리핑이 없습니다.')
    else:
        latest=api('/api/korea/preopen/latest'); meta=(latest or {}).get('meta') or {}; rr=(latest or {}).get('rows') or []
        if rr:st.info(f"장전 후보 분위기 {f(meta.get('market_long_power'),50):.0f}% · 상위 관찰 후보: {', '.join([str(x.get('name') or x.get('symbol')) for x in rr[:3]])}"); st.dataframe(pd.DataFrame(rr[:10]),use_container_width=True,hide_index=True)
        else:st.info('저장된 한국장 PREOPEN 리포트가 없습니다.')
with t[2]:
    st.subheader('🧪 Validation Lab'); st.caption('V4의 숫자는 정답이 아니라 초기 가설입니다. 모든 feature snapshot을 Historical/Shadow 보정에 사용합니다.'); ss=api(f'/api/v4/validation/snapshots?market={m}&limit=500').get('data') or []
    if ss:
        df=pd.DataFrame(ss); st.metric('최근 Feature Snapshot',len(df)); keep=[x for x in ['ts','symbol','finder_rank','power','power_delta','state','risk','price'] if x in df.columns]; st.dataframe(df[keep],use_container_width=True,hide_index=True)
    else:st.info('V4 Tracker가 동작하면 분당 feature snapshot이 자동 저장됩니다.')
    st.markdown('1. Finder 조건별 15/30/60분 성과  \n2. Power 구성요소별 MFE/MAE  \n3. READY/ENTRY 이후 실제 움직임  \n4. Floor 폭별 stop-out/재상승 비율  \n5. Shadow 검증 후 통과한 값만 CURRENT 승격')
with t[3]:
    st.subheader('📚 Archive'); trades=api(f'/api/v4/trades?market={m}&limit=300').get('data') or []; events=api(f'/api/v4/events?market={m}&limit=300').get('data') or []; st.markdown('#### 실제 수동 매매 기록'); st.dataframe(pd.DataFrame(trades),use_container_width=True,hide_index=True) if trades else st.caption('등록된 실제 매매가 없습니다.'); st.markdown('#### 엔진 신호/순위 변화 기록'); st.dataframe(pd.DataFrame(events),use_container_width=True,hide_index=True) if events else st.caption('저장된 이벤트가 없습니다.')
st.divider(); st.caption('V4.0 CLEAN ENGINE ALPHA · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY · NO AUTO ORDER')
if auto_live:
    time.sleep(5)
    st.rerun()
