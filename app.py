import altair as alt
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
def stko(s):return {'WATCH':'관찰','SETUP':'준비','READY':'진입 준비','ENTRY':'진입 신호','HOLD':'보유 유지','PARTIAL_EXIT':'부분익절','EXIT_READY':'청산 준비','HARD_EXIT':'즉시 청산','DATA_INVALID':'데이터 오류'}.get(str(s),str(s or '-'))
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

def _price_volume_chart(rows, title, height=250):
    """Focused intraday price chart with tight Y scale + separate volume bars."""
    if not rows:
        st.info('차트 데이터 준비 중')
        return
    try:
        df=pd.DataFrame(rows)
        tcol='time' if 'time' in df.columns else ('ts' if 'ts' in df.columns else None)
        if not tcol or 'close' not in df.columns:
            st.info('차트 데이터 준비 중')
            return
        df[tcol]=pd.to_datetime(df[tcol],utc=True,errors='coerce')
        df=df.dropna(subset=[tcol]).sort_values(tcol)
        if df.empty:
            st.info('차트 데이터 준비 중')
            return

        close=pd.to_numeric(df['close'],errors='coerce')
        high=pd.to_numeric(df.get('high',close),errors='coerce').fillna(close)
        low=pd.to_numeric(df.get('low',close),errors='coerce').fillna(close)
        vol=pd.to_numeric(df.get('volume',0),errors='coerce').fillna(0)

        tp=(high+low+close)/3
        cv=vol.cumsum()
        df['VWAP']=(tp*vol).cumsum()/cv.replace(0,pd.NA)
        df['EMA9']=close.ewm(span=9,adjust=False).mean()
        df['EMA20']=close.ewm(span=20,adjust=False).mean()
        df['Price']=close
        df['Volume']=vol

        vals=pd.concat([df['Price'],df['VWAP'],df['EMA9'],df['EMA20']]).dropna()
        if vals.empty:
            st.info('차트 데이터 준비 중')
            return
        ymin=float(vals.min()); ymax=float(vals.max())
        span=max(ymax-ymin, max(abs(ymax),1)*0.002)
        pad=span*0.12
        domain=[ymin-pad,ymax+pad]

        long=df[[tcol,'Price','VWAP','EMA9','EMA20']].melt(
            id_vars=[tcol],var_name='Series',value_name='Value'
        ).dropna()

        line=alt.Chart(long).mark_line().encode(
            x=alt.X(
                f'{tcol}:T',
                title=None,
                axis=alt.Axis(
                    format='%H:%M',
                    labelAngle=0,
                    grid=True,
                    gridOpacity=0.08,
                    domainOpacity=0.18,
                    tickOpacity=0.18
                )
            ),
            y=alt.Y(
                'Value:Q',
                title='가격',
                scale=alt.Scale(domain=domain,zero=False),
                axis=alt.Axis(
                    grid=True,
                    gridOpacity=0.08,
                    domainOpacity=0.18,
                    tickOpacity=0.18
                )
            ),
            color=alt.Color('Series:N',title=None),
            strokeWidth=alt.StrokeWidth(
                'Series:N',
                scale=alt.Scale(
                    domain=['Price','VWAP','EMA9','EMA20'],
                    range=[3.2,2.4,0.75,0.55]
                ),
                legend=None
            ),
            opacity=alt.Opacity(
                'Series:N',
                scale=alt.Scale(domain=['Price','VWAP','EMA9','EMA20'],range=[1.0,0.95,0.48,0.36]),legend=None
            ),
            strokeDash=alt.StrokeDash(
                'Series:N',
                scale=alt.Scale(domain=['Price','VWAP','EMA9','EMA20'],range=[[1,0],[1,0],[6,4],[2,4]]),legend=None
            ),
            tooltip=[
                alt.Tooltip(f'{tcol}:T',title='시간'),
                alt.Tooltip('Series:N',title='지표'),
                alt.Tooltip('Value:Q',title='값',format='.2f'),
            ],
        ).properties(height=height)

        volume=alt.Chart(df).mark_bar(opacity=0.42).encode(
            x=alt.X(
                f'{tcol}:T',
                title=None,
                axis=alt.Axis(
                    format='%H:%M',
                    labelAngle=0,
                    grid=False,
                    domainOpacity=0.16,
                    tickOpacity=0.16
                )
            ),
            y=alt.Y(
                'Volume:Q',
                title='거래량',
                axis=alt.Axis(
                    grid=True,
                    gridOpacity=0.06,
                    domainOpacity=0.16,
                    tickOpacity=0.16
                )
            ),
            tooltip=[
                alt.Tooltip(f'{tcol}:T',title='시간'),
                alt.Tooltip('Volume:Q',title='거래량',format=',.0f'),
            ],
        ).properties(height=90)

        st.caption(title)
        st.altair_chart(alt.vconcat(line,volume).resolve_scale(x='shared'),use_container_width=True)
    except Exception as e:
        st.warning(f'차트 표시 오류: {e}')

def px(v,m):
    if v in (None,''):return '-'
    try:return f'${float(v):,.2f}' if m=='USA' else f'{float(v):,.0f}원'
    except Exception:return '-'
def controls(m,row):
    sym=row['symbol']; pos=bool(row.get('position_open')); held=f(row.get('qty'))
    with st.expander(f"수동 매매 입력 · {row.get('name') or sym} ({sym})"):
        if pos:
            st.caption(f'현재 보유 {held:g}주 · 평균단가 {px(row.get("avg_entry"),m)}')
        else:
            st.caption('현재 보유 없음 · 매수 등록 후 매도/부분매도 입력이 활성화됩니다.')

        buy_col,sell_col=st.columns(2)

        with buy_col:
            st.markdown('**매수 / 추가매수**')
            b1,b2=st.columns(2)
            bq=b1.number_input('매수 수량',min_value=0.0,step=1.0,key=f'bq{m}{sym}')
            bp=b2.number_input('실제 매수가',min_value=0.0,step=.01 if m=='USA' else 10.0,key=f'bp{m}{sym}')
            if st.button('매수 등록',key=f'buy{m}{sym}',use_container_width=True):
                rr=post('/api/v4/position/buy',{'market':m,'symbol':sym,'qty':bq,'price':bp})
                st.success('매수 등록 완료') if rr.get('ok') else st.error(rr.get('error'))
                st.rerun() if rr.get('ok') else None

        with sell_col:
            st.markdown('**매도 / 부분매도**')
            s1,s2=st.columns(2)
            sq=s1.number_input(
                '매도 수량',min_value=0.0,max_value=max(held,0.0),step=1.0,
                key=f'sq{m}{sym}',disabled=not pos
            )
            sp=s2.number_input(
                '실제 매도가',min_value=0.0,step=.01 if m=='USA' else 10.0,
                key=f'sp{m}{sym}',disabled=not pos
            )
            if st.button('매도 등록',key=f'sell{m}{sym}',use_container_width=True,disabled=not pos):
                rr=post('/api/v4/position/sell',{'market':m,'symbol':sym,'qty':sq,'price':sp})
                st.success('매도 등록 완료') if rr.get('ok') else st.error(rr.get('error'))
                st.rerun() if rr.get('ok') else None
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
        pri={'HARD_EXIT':0,'EXIT_READY':1,'PARTIAL_EXIT':2,'ENTRY':3,'READY':4,'SETUP':5,'HOLD':6,'WATCH':7,'DATA_INVALID':99}; lead=sorted(rows,key=lambda r:(pri.get(r.get('state'),99),-abs(f(r.get('power')))))[0]; st.markdown('### 🚨 지금 가장 중요한 행동' if live_now else '### 📌 마지막 상태 요약'); st.info(f"{'장 마감 참고 · ' if not live_now else ''}{lead.get('name')} ({lead.get('symbol')}) · **{stko(lead.get('state'))}** · {lead.get('power_label') or ''} Power {f(lead.get('power')):+.0f} ({f(lead.get('power_delta')):+.0f}) · {lead.get('reason')}")
        sel=st.selectbox('종목 상세',[r['symbol'] for r in rows],format_func=lambda x:next((f"{r.get('name')} ({x})" for r in rows if r['symbol']==x),x)); r=next(x for x in rows if x['symbol']==sel); q1,q2,q3,q4,q5=st.columns(5); q1.metric('상태',stko(r.get('state'))); q2.metric(r.get('power_label') or 'Power',f"{f(r.get('power')):+.0f}",delta=f"{f(r.get('power_delta')):+.0f}"); q3.metric('현재가',px(r.get('price'),m)); q4.metric('Floor 모드',r.get('floor_mode') or '-'); q5.metric('위험',rko(r.get('risk')))
        pg=r.get('position_gate') or {}
        if r.get('position_open') and pg:
            st.markdown('#### 🛡️ Position Manager')
            p1,p2,p3,p4,p5=st.columns(5); p1.metric('포지션 상태',stko(pg.get('state'))); p2.metric('현재 R',f"{f(pg.get('profit_r')):.2f}R"); p3.metric('초기 Floor',px(pg.get('initial_floor'),m)); p4.metric('현재 Hard Floor',px(pg.get('hard_floor'),m)); p5.metric('Floor 단계',pg.get('floor_mode') or '-')
            if pg.get('suggested_exit_pct'):st.warning(f"수동 대응 제안: {int(f(pg.get('suggested_exit_pct')))}% 정리 검토 · {pg.get('reason')}")
            else:st.caption(pg.get('reason') or '보유 관리 중')
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
                _price_volume_chart(b1f,'1분봉 · Trigger · 최근 60분',250)
            with c2:
                _price_volume_chart(b5f,'5분봉 · Setup · 최근 3시간',250)
        controls(m,r)
    else:st.warning('Tracker 데이터가 아직 없습니다. 서버 시작 직후라면 수 초 후 자동 생성됩니다.')
    st.markdown('### 🎯 Finder TOP5 · 오늘 볼 종목')
    st.caption('Finder는 종목 선정용입니다. 위 Power 순위는 진입 준비도를 실시간으로 다시 정렬합니다. TOP5 진입 = 즉시 매수가 아닙니다.')

    if m=='USA':
        regime=fi.get('market_regime') or 'UNKNOWN'
        pref=fi.get('preferred_direction') or '-'
        light_rows=fi.get('light_rows') or []
        e1,e2,e3,e4=st.columns(4)
        e1.metric('시장 레짐',regime)
        e2.metric('우선 방향',pref)
        e3.metric('Light Tracker',fi.get('light_count',len(light_rows)))
        e4.metric('Finder 회전',f"{fi.get('rotation_seconds',30)}초")
        st.caption('Finder점수는 확률이 아니라 후보 우선순위 점수입니다. 실제 진입은 위 Power / 5분 Setup / 1분 Trigger / 추격방지를 별도로 통과해야 합니다.')

    if fr:
        st.dataframe(pd.DataFrame([{
            '순위':r.get('rank'),
            '종목':r.get('symbol'),
            '종목명':r.get('name'),
            'Finder점수':r.get('finder_score'),
            '방향':r.get('direction'),
            '등락률%':r.get('change_pct'),
            '1m%':r.get('ret_1m'),
            '3m%':r.get('ret_3m'),
            '5m%':r.get('ret_5m'),
            'Fresh':r.get('fresh_mode') or '-',
            'Fresh점수':r.get('fresh_score'),
            'RVOL':r.get('rvol'),
            'Vol가속':r.get('volume_accel'),
            'Power참조':r.get('observed_power'),
            'Fade감점':r.get('fade_penalty'),
            '위험':rko(r.get('risk'))
        } for r in fr]),use_container_width=True,hide_index=True)

    if m=='USA':
        light_rows=fi.get('light_rows') or []
        with st.expander(f"🔎 Light Tracker {len(light_rows)} · 점수 근거 보기"):
            if light_rows:
                explain=[]
                for x in light_rows:
                    mode=x.get('fresh_mode') or 'WATCH'
                    if x.get('extreme_watch'):
                        tag='EXTREME'
                    elif x.get('fresh_mover'):
                        tag=mode
                    elif f(x.get('fade_penalty'))>0:
                        tag='FADING'
                    else:
                        tag='WATCH'
                    explain.append({
                        'Light순위':x.get('light_rank'),
                        '종목':x.get('symbol'),
                        '점수':x.get('finder_score'),
                        '상태':tag,
                        '당일%':x.get('change_pct'),
                        '1m%':x.get('ret_1m'),
                        '3m%':x.get('ret_3m'),
                        '5m%':x.get('ret_5m'),
                        '15m%':x.get('ret_15m'),
                        'Vol가속':x.get('volume_accel'),
                        'Vol커버':x.get('volume_coverage_10m'),
                        '3분고점돌파':'Y' if x.get('break_3m_high') else '',
                        'Fresh점수':x.get('fresh_score'),
                        'Power참조':x.get('observed_power'),
                        'Fade감점':x.get('fade_penalty'),
                        '품질':x.get('quality'),
                        '위험':rko(x.get('risk')),
                        '선정근거':x.get('finder_reason')
                    })
                st.dataframe(pd.DataFrame(explain),use_container_width=True,hide_index=True)
                fresh=[x for x in light_rows if x.get('fresh_mover')]
                fading=[x for x in light_rows if f(x.get('fade_penalty'))>0]
                extreme=[x for x in light_rows if x.get('extreme_watch')]
                c1,c2,c3=st.columns(3)
                c1.metric('Fresh 감지',len(fresh))
                c2.metric('Fade 감지',len(fading))
                c3.metric('Extreme 관찰',len(extreme))
                if fresh:
                    st.success('지금 가속 감지 · '+', '.join(
                        f"{x.get('symbol')}({x.get('fresh_mode')})" for x in fresh[:6]
                    ))
                else:
                    st.caption('현재 Light Tracker에는 CONTINUATION/BREAKOUT 조건을 모두 충족한 Fresh 종목이 없습니다.')
            else:
                st.caption('Light Tracker 데이터 준비 중')
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
    st.subheader('🧪 Validation / Daily Report')
    st.caption('추천 점수는 확률이 아닙니다. 저장된 실제 Tracker 표본의 +5/+15/+30/+60분, MFE/MAE를 이용해 가설을 검증합니다.')

    marks=api(f'/api/v4/validation/marks?market={m}&limit=3000').get('data') or []

    if marks:
        df=pd.DataFrame(marks).copy()

        # Normalize numeric fields safely.
        numeric_cols=[
            'anchor_price','power','power_delta','finder_rank',
            'setup_count','trigger_count','rvol','volume_ratio',
            'ret_5m','ret_15m','ret_30m','ret_60m','mfe_pct','mae_pct'
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col]=pd.to_numeric(df[col],errors='coerce')

        if 'ts' in df.columns:
            df['ts_dt']=pd.to_datetime(df['ts'],utc=True,errors='coerce')
            if m=='USA':
                try:
                    df['session_date']=df['ts_dt'].dt.tz_convert('America/New_York').dt.date.astype(str)
                except Exception:
                    df['session_date']=df['ts_dt'].dt.date.astype(str)
            else:
                try:
                    df['session_date']=df['ts_dt'].dt.tz_convert('Asia/Seoul').dt.date.astype(str)
                except Exception:
                    df['session_date']=df['ts_dt'].dt.date.astype(str)
        else:
            df['session_date']='-'

        dates=[x for x in sorted(df['session_date'].dropna().unique(),reverse=True) if x!='NaT']
        report_date=st.selectbox('리포트 거래일',dates,index=0 if dates else None,key=f'validation_date_{m}') if dates else None
        day=df[df['session_date']==report_date].copy() if report_date else df.copy()

        # Completed observations at each horizon.
        done60=day[day['ret_60m'].notna()].copy() if 'ret_60m' in day.columns else pd.DataFrame()
        done30=day[day['ret_30m'].notna()].copy() if 'ret_30m' in day.columns else pd.DataFrame()
        done15=day[day['ret_15m'].notna()].copy() if 'ret_15m' in day.columns else pd.DataFrame()
        done5=day[day['ret_5m'].notna()].copy() if 'ret_5m' in day.columns else pd.DataFrame()

        c1,c2,c3,c4,c5=st.columns(5)
        c1.metric('당일 표본',len(day))
        c2.metric('60분 완료',len(done60))
        c3.metric('추적 종목',day['symbol'].nunique() if 'symbol' in day.columns else 0)
        if len(done60):
            c4.metric('60분 평균',f"{done60['ret_60m'].mean():+.2f}%")
            c5.metric('60분 상승 비율',f"{(done60['ret_60m']>0).mean()*100:.1f}%")
        elif len(done30):
            c4.metric('30분 평균',f"{done30['ret_30m'].mean():+.2f}%")
            c5.metric('30분 상승 비율',f"{(done30['ret_30m']>0).mean()*100:.1f}%")
        else:
            c4.metric('완료 평균','-')
            c5.metric('상승 비율','-')

        st.markdown('#### 📊 시간대별 성과')
        perf=[]
        for mins,col in [(5,'ret_5m'),(15,'ret_15m'),(30,'ret_30m'),(60,'ret_60m')]:
            if col not in day.columns: continue
            x=day[day[col].notna()]
            if len(x):
                perf.append({
                    '구간':f'+{mins}분',
                    '표본':len(x),
                    '평균%':round(x[col].mean(),3),
                    '중앙값%':round(x[col].median(),3),
                    '상승비율%':round((x[col]>0).mean()*100,1),
                    '평균 MFE%':round(x['mfe_pct'].mean(),3) if 'mfe_pct' in x.columns else None,
                    '평균 MAE%':round(x['mae_pct'].mean(),3) if 'mae_pct' in x.columns else None,
                })
        if perf:
            st.dataframe(pd.DataFrame(perf),use_container_width=True,hide_index=True)

        st.markdown('#### 🏆 종목별 실제 추적 성과')
        # Snapshot table is intentionally not treated as independent trades.
        # Aggregate per symbol so repeated minute snapshots do not look like many trades.
        agg={}
        for sym,g in day.groupby('symbol'):
            row={'종목':sym,'표본':len(g)}
            for col,label in [('ret_5m','5분%'),('ret_15m','15분%'),('ret_30m','30분%'),('ret_60m','60분%')]:
                vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                row[label]=round(vals.mean(),3) if len(vals) else None
            row['MFE%']=round(g['mfe_pct'].max(),3) if 'mfe_pct' in g.columns and g['mfe_pct'].notna().any() else None
            row['MAE%']=round(g['mae_pct'].min(),3) if 'mae_pct' in g.columns and g['mae_pct'].notna().any() else None
            row['평균Power']=round(g['power'].mean(),1) if 'power' in g.columns and g['power'].notna().any() else None
            row['최대Power']=round(g['power'].max(),1) if 'power' in g.columns and g['power'].notna().any() else None
            row['평균Setup']=round(g['setup_count'].mean(),2) if 'setup_count' in g.columns and g['setup_count'].notna().any() else None
            row['평균Trigger']=round(g['trigger_count'].mean(),2) if 'trigger_count' in g.columns and g['trigger_count'].notna().any() else None
            agg[sym]=row

        symdf=pd.DataFrame(list(agg.values()))
        horizon='60분%' if '60분%' in symdf.columns and symdf['60분%'].notna().any() else \
                '30분%' if '30분%' in symdf.columns and symdf['30분%'].notna().any() else \
                '15분%' if '15분%' in symdf.columns and symdf['15분%'].notna().any() else '5분%'
        if len(symdf):
            symdf=symdf.sort_values(horizon,ascending=False,na_position='last')
            st.dataframe(symdf,use_container_width=True,hide_index=True)

            valid_rank=symdf[symdf[horizon].notna()]
            if len(valid_rank):
                best=valid_rank.iloc[0]
                worst=valid_rank.iloc[-1]
                a,b=st.columns(2)
                a.success(f"잘 잡은 종목 · {best['종목']} · {horizon} {best[horizon]:+.2f}% · MFE {f(best.get('MFE%')):+.2f}%")
                b.warning(f"부진 종목 · {worst['종목']} · {horizon} {worst[horizon]:+.2f}% · MAE {f(worst.get('MAE%')):+.2f}%")

        st.markdown('#### 🎯 엔진 상태별 성과')
        if 'state' in day.columns:
            state_rows=[]
            for state,g in day.groupby('state',dropna=False):
                r={'상태':stko(state),'표본':len(g)}
                for col,label in [('ret_5m','5분평균%'),('ret_15m','15분평균%'),('ret_30m','30분평균%'),('ret_60m','60분평균%')]:
                    vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                    r[label]=round(vals.mean(),3) if len(vals) else None
                r['평균Power']=round(g['power'].mean(),1) if 'power' in g.columns and g['power'].notna().any() else None
                r['평균Setup']=round(g['setup_count'].mean(),2) if 'setup_count' in g.columns and g['setup_count'].notna().any() else None
                r['평균Trigger']=round(g['trigger_count'].mean(),2) if 'trigger_count' in g.columns and g['trigger_count'].notna().any() else None
                state_rows.append(r)
            st.dataframe(pd.DataFrame(state_rows),use_container_width=True,hide_index=True)

        st.markdown('#### ⚡ Power 구간별 성과')
        if 'power' in day.columns:
            p=day[day['power'].notna()].copy()
            if len(p):
                p['Power구간']=pd.cut(
                    p['power'],
                    bins=[-1e9,0,20,40,60,1e9],
                    labels=['≤0','0~20','20~40','40~60','60+'],
                    right=False
                )
                power_rows=[]
                for bucket,g in p.groupby('Power구간',observed=True):
                    r={'Power구간':str(bucket),'표본':len(g)}
                    for col,label in [('ret_5m','5분%'),('ret_15m','15분%'),('ret_30m','30분%'),('ret_60m','60분%')]:
                        vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                        r[label]=round(vals.mean(),3) if len(vals) else None
                    r['상승비율60%']=round((g['ret_60m'].dropna()>0).mean()*100,1) if 'ret_60m' in g.columns and g['ret_60m'].notna().any() else None
                    power_rows.append(r)
                st.dataframe(pd.DataFrame(power_rows),use_container_width=True,hide_index=True)

        st.markdown('#### 🧩 Episode Validation')
        st.caption('분당 스냅샷을 그대로 세지 않고 SETUP→READY→ENTRY→EXIT 계열의 연속 신호를 하나의 Episode로 묶어 봅니다. 짧은 WATCH 흔들림은 5분까지 같은 Episode로 합칩니다.')
        eps=api(f'/api/v4/validation/episodes?market={m}&limit=5000&bridge_minutes=5').get('data') or []
        if eps:
            edf=pd.DataFrame(eps)
            edf['start_dt']=pd.to_datetime(edf['start_ts'],utc=True,errors='coerce')
            if m=='USA':
                try:edf['episode_date']=edf['start_dt'].dt.tz_convert('America/New_York').dt.date.astype(str)
                except Exception:edf['episode_date']=edf['start_dt'].dt.date.astype(str)
            else:
                try:edf['episode_date']=edf['start_dt'].dt.tz_convert('Asia/Seoul').dt.date.astype(str)
                except Exception:edf['episode_date']=edf['start_dt'].dt.date.astype(str)
            e=edf[edf['episode_date']==report_date].copy() if report_date else edf.copy()
            for col in ['start_power','max_power','start_setup','max_setup','start_trigger','max_trigger','ret_5m','ret_15m','ret_30m','ret_60m','mfe_pct','mae_pct','duration_min']:
                if col in e.columns:e[col]=pd.to_numeric(e[col],errors='coerce')

            ec1,ec2,ec3,ec4=st.columns(4)
            ec1.metric('Episode 수',len(e))
            ec2.metric('60분 완료',int(e['ret_60m'].notna().sum()) if 'ret_60m' in e.columns else 0)
            ec3.metric('READY 이상',int(e['max_state'].isin(['READY','ENTRY','HOLD','PARTIAL_EXIT','EXIT_READY','HARD_EXIT']).sum()) if 'max_state' in e.columns else 0)
            ec4.metric('ENTRY 도달',int(e['max_state'].isin(['ENTRY','HOLD','PARTIAL_EXIT','EXIT_READY','HARD_EXIT']).sum()) if 'max_state' in e.columns else 0)

            if len(e):
                state_perf=[]
                for state,g in e.groupby('max_state',dropna=False):
                    r={'최고상태':stko(state),'Episode':len(g)}
                    for col,label in [('ret_5m','5분%'),('ret_15m','15분%'),('ret_30m','30분%'),('ret_60m','60분%')]:
                        vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                        r[label]=round(vals.mean(),3) if len(vals) else None
                    r['60분상승%']=round((g['ret_60m'].dropna()>0).mean()*100,1) if 'ret_60m' in g.columns and g['ret_60m'].notna().any() else None
                    r['MFE%']=round(g['mfe_pct'].mean(),3) if 'mfe_pct' in g.columns and g['mfe_pct'].notna().any() else None
                    r['MAE%']=round(g['mae_pct'].mean(),3) if 'mae_pct' in g.columns and g['mae_pct'].notna().any() else None
                    state_perf.append(r)
                st.markdown('##### Episode 최고 상태별 성과')
                st.dataframe(pd.DataFrame(state_perf),use_container_width=True,hide_index=True)

                ep=e[e['start_power'].notna()].copy() if 'start_power' in e.columns else pd.DataFrame()
                if len(ep):
                    ep['Power구간']=pd.cut(ep['start_power'],bins=[-1e9,0,20,40,60,1e9],labels=['≤0','0~20','20~40','40~60','60+'],right=False)
                    buckets=[]
                    for bucket,g in ep.groupby('Power구간',observed=True):
                        r={'시작 Power':str(bucket),'Episode':len(g)}
                        for col,label in [('ret_15m','15분%'),('ret_30m','30분%'),('ret_60m','60분%')]:
                            vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                            r[label]=round(vals.mean(),3) if len(vals) else None
                        r['60분상승%']=round((g['ret_60m'].dropna()>0).mean()*100,1) if 'ret_60m' in g.columns and g['ret_60m'].notna().any() else None
                        buckets.append(r)
                    st.markdown('##### Episode 시작 Power별 성과')
                    st.dataframe(pd.DataFrame(buckets),use_container_width=True,hide_index=True)

                st.markdown('##### Episode 목록')
                cols=[c for c in ['start_ts','symbol','start_state','max_state','state_path','duration_min','anchor_price','start_power','max_power','start_setup','max_setup','start_trigger','max_trigger','ret_5m','ret_15m','ret_30m','ret_60m','mfe_pct','mae_pct'] if c in e.columns]
                st.dataframe(e[cols].sort_values('start_ts',ascending=False),use_container_width=True,hide_index=True)
        else:
            st.caption('Episode를 만들 수 있는 Validation 데이터가 아직 없습니다.')

        st.markdown('#### 🧭 오늘 엔진 판정')
        notes=[]
        completed=done60 if len(done60) else done30 if len(done30) else done15 if len(done15) else done5
        retcol='ret_60m' if len(done60) else 'ret_30m' if len(done30) else 'ret_15m' if len(done15) else 'ret_5m'
        if len(completed):
            avg=completed[retcol].mean()
            hit=(completed[retcol]>0).mean()*100
            notes.append(f"현재 완료 표본 기준 {retcol.replace('ret_','').replace('m','분')} 평균 {avg:+.2f}%, 상승 비율 {hit:.1f}%")
            if avg>0.30 and hit>=55:
                notes.append('현재 표본에서는 후보 추적 방향이 우호적입니다.')
            elif avg<-0.20 or hit<45:
                notes.append('현재 표본에서는 후보 선정/진입 기준 재검토가 필요합니다.')
            else:
                notes.append('현재 표본은 우위가 아직 뚜렷하지 않습니다.')
        if 'mfe_pct' in day.columns and 'mae_pct' in day.columns and len(day):
            mfe=day['mfe_pct'].mean()
            mae=day['mae_pct'].mean()
            notes.append(f"평균 MFE {mfe:+.2f}% / 평균 MAE {mae:+.2f}%")
        for n in notes:
            st.write('• '+n)

        st.caption('주의: Validation 표본은 분당 Tracker 스냅샷입니다. 동일 종목의 여러 시점이 포함되므로 “거래 횟수”나 독립 표본으로 해석하면 안 됩니다. 종목별 표는 중복 스냅샷을 묶어 참고용으로 보여줍니다.')

        with st.expander('원본 Validation 표본'):
            show=[c for c in [
                'ts','symbol','state','anchor_price','power','power_delta','finder_rank',
                'setup_count','trigger_count','rvol','volume_ratio',
                'ret_5m','ret_15m','ret_30m','ret_60m',
                'mfe_pct','mae_pct','floor_mode'
            ] if c in day.columns]
            st.dataframe(day[show],use_container_width=True,hide_index=True)
    else:
        st.info('정상 데이터로 Tracker가 동작하면 분당 검증 표본이 자동 저장됩니다.')

    st.markdown(
        '다음 보정 원칙  \n'
        '1. 단일 하루 결과로 임계값을 바꾸지 않기  \n'
        '2. Power/Setup/Trigger별 +15/+30/+60분 기대값 비교  \n'
        '3. MFE/MAE로 Floor와 부분익절 폭 검증  \n'
        '4. 여러 세션에서 반복되는 패턴만 CURRENT 기준으로 승격'
    )

with t[3]:
    st.subheader('📚 Archive'); trades=api(f'/api/v4/trades?market={m}&limit=300').get('data') or []; events=api(f'/api/v4/events?market={m}&limit=300').get('data') or []; st.markdown('#### 실제 수동 매매 기록'); st.dataframe(pd.DataFrame(trades),use_container_width=True,hide_index=True) if trades else st.caption('등록된 실제 매매가 없습니다.'); st.markdown('#### 엔진 신호/순위 변화 기록'); st.dataframe(pd.DataFrame(events),use_container_width=True,hide_index=True) if events else st.caption('저장된 이벤트가 없습니다.')
st.divider(); st.caption('V4.5.0 · EPISODE VALIDATION + DAILY REPORT · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY')
if auto_live:
    time.sleep(5)
    st.rerun()
