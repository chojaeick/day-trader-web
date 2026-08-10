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

        st.markdown('#### 🔎 Scanner / Coverage Audit')
        st.caption('Broad Discovery → Light Tracker → Finder → Heavy5의 현재 파이프라인을 점검합니다. 추천식은 바꾸지 않고 “어디에서 놓쳤는지”만 보여줍니다.')
        cov=api(f'/api/v4/coverage-audit?market={m}') or {}
        if cov.get('supported'):
            cnt=cov.get('counts') or {}
            c1,c2,c3,c4,c5,c6=st.columns(6)
            c1.metric('Quotes',cnt.get('quotes',0))
            c2.metric('Screener40',cnt.get('screener40',0))
            c3.metric('Discovery',cnt.get('discovery',0))
            c4.metric('Light',cnt.get('light',0))
            c5.metric('Finder',cnt.get('finder',0))
            c6.metric('Heavy5',cnt.get('heavy',0))

            q1,q2,q3=st.columns(3)
            q1.metric('Extreme',cnt.get('extreme',0))
            q2.metric('Quality Risk',cnt.get('quality_risk',0))
            q3.metric('Quality Reject',cnt.get('quality_reject',0))

            src=cov.get('source_counts') or {}
            if src:
                st.caption('Discovery source coverage · '+ ' · '.join(f'{k}:{v}' for k,v in sorted(src.items())))

            st.markdown('##### ↕️ 현재 강한 변동 종목 · 어느 단계까지 왔나')
            movers=pd.DataFrame(cov.get('top_abs_movers') or [])
            if len(movers):
                cols=[c for c in ['symbol','name','change_pct','price','stage','reason','quality','origin','fresh','finder_score','power','data_age_sec'] if c in movers.columns]
                st.dataframe(movers[cols],use_container_width=True,hide_index=True)
            else:
                st.caption('현재 mover 데이터가 없습니다.')

            st.markdown('##### ✂️ Light → Finder 컷라인')
            la=pd.DataFrame(cov.get('light_audit') or [])
            if len(la):
                cut=cov.get('finder_cut')
                st.caption(f'현재 Finder 5위 컷 · {cut}' if cut is not None else '현재 Finder 컷 계산 불가')
                cols=[c for c in ['light_rank','symbol','finder_score','finder_cut','gap_to_cut','selected','quality','fresh','fresh_score','ret_1m','ret_3m','ret_5m','ret_15m','volume_accel','break_3m_high','fade_penalty','extreme_continue','reason'] if c in la.columns]
                st.dataframe(la[cols],use_container_width=True,hide_index=True)

            st.markdown('##### 🕳️ Discovery Miss Audit')
            dm=pd.DataFrame(cov.get('discovery_miss') or [])
            if len(dm):
                st.warning(f'Screener에는 있으나 현재 Discovery/Extreme/Risk snapshot에는 없는 종목 {len(dm)}개')
                st.dataframe(dm,use_container_width=True,hide_index=True)
                st.caption('이 표는 “누락 위치”만 확정합니다. upstream Kiwoom ranking/source에서 왜 빠졌는지는 현재 저장 snapshot만으로 추정하지 않습니다.')
            else:
                st.success('현재 Screener 종목 중 Discovery 계층 완전 누락은 감지되지 않았습니다.')

            st.markdown('##### 🌉 Discovery Bridge Shadow Audit')
            st.caption('Discovery 누락 종목을 실제 Finder에 넣지 않고, 동일 Finder 점수식으로 가상 재평가합니다. 품질정보가 없는 Screener eligible 종목은 SHADOW_UNKNOWN으로 두고 Quality 보너스 0점으로 계산합니다.')
            bridge=api(f'/api/v4/discovery-bridge-shadow?market={m}') or {}
            if bridge.get('supported'):
                b1,b2,b3,b4=st.columns(4)
                b1.metric('가상 Finder 신규',len(bridge.get('new_shadow_entrants') or []))
                b2.metric('가상으로 밀려난 기존',len(bridge.get('displaced_live') or []))
                b3.metric('데이터 준비 Miss',bridge.get('data_ready_misses',0))
                b4.metric('준비 부족 Miss',bridge.get('insufficient_data_misses',0))

                comp=pd.DataFrame(bridge.get('comparison') or [])
                if len(comp):
                    st.markdown('###### Live Finder vs Bridge Shadow Finder')
                    st.dataframe(comp,use_container_width=True,hide_index=True)

                miss_shadow=pd.DataFrame(bridge.get('miss_rows') or [])
                if len(miss_shadow):
                    st.markdown('###### Discovery Miss를 가상으로 넣었을 때')
                    cols=[c for c in [
                        'symbol','screener_score','change_pct','eligible',
                        'shadow_light_rank','shadow_finder_rank','shadow_finder_score',
                        'shadow_quality','price','recent_bars','data_ready','fair_status','fresh','fresh_score',
                        'ret_1m','ret_3m','ret_5m','ret_15m','volume_accel',
                        'break_3m_high','would_reach_light','would_reach_finder','note'
                    ] if c in miss_shadow.columns]
                    st.dataframe(miss_shadow[cols],use_container_width=True,hide_index=True)

                entrants=bridge.get('new_shadow_entrants') or []
                displaced=bridge.get('displaced_live') or []
                if entrants:
                    st.warning('Bridge Shadow에서 Finder TOP5에 새로 들어오는 누락 종목 · '+', '.join(entrants))
                    if displaced:
                        st.write('가상으로 밀려나는 기존 Finder · '+', '.join(displaced))
                else:
                    st.success('현재 Discovery 누락 종목을 보수적으로 가상 연결해도 Finder TOP5 변화는 없습니다.')

                etf=pd.DataFrame(bridge.get('core_etf_readiness') or [])
                if len(etf):
                    st.markdown('###### 핵심 Leveraged / Inverse ETF 데이터 준비')
                    st.dataframe(etf,use_container_width=True,hide_index=True)
                st.caption('Fair Guard: SHADOW_UNKNOWN은 recent_bars≥6 + price>0일 때만 Shadow Finder에 들어갈 수 있습니다. 품질정보 누락 종목에는 Quality 보너스를 주지 않습니다.')
            else:
                st.info(bridge.get('note') or 'Bridge Shadow는 현재 USA만 지원합니다.')

            st.markdown('##### 🔄 Inverse / Leveraged ETF 파이프라인')
            inv=pd.DataFrame(cov.get('inverse') or [])
            if len(inv):
                st.dataframe(inv,use_container_width=True,hide_index=True)
                for _,rr in inv.iterrows():
                    if rr.get('symbol') in ('SOXS','SQQQ') and rr.get('stage') not in ('FINDER','HEAVY5'):
                        st.write(f"• {rr.get('symbol')} · {rr.get('stage')} · {rr.get('reason')}")

            stale=pd.DataFrame(cov.get('stale_rows') or [])
            if len(stale):
                st.warning(f"3분 초과 stale quote {len(stale)}개가 감지되었습니다. 아래 표는 최대 30개입니다.")
                st.dataframe(stale,use_container_width=True,hide_index=True)
            else:
                st.success('현재 저장 quote 기준 3분 초과 stale 데이터가 감지되지 않았습니다.')

            with st.expander('현재 파이프라인 심볼'):
                st.write('Light · '+', '.join(cov.get('light_symbols') or []))
                st.write('Finder · '+', '.join(cov.get('finder_symbols') or []))
                st.write('Heavy · '+', '.join(cov.get('heavy_symbols') or []))
        else:
            st.info(cov.get('note') or 'Coverage Audit은 현재 USA만 지원합니다.')


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

            # V4.5.2: count only states that were actually observed in marks.
            # Do not infer ENTRY from downstream position/exit states.
            reach_anchors=api(f'/api/v4/validation/stage-anchors?market={m}&limit=5000&bridge_minutes=5').get('data') or []
            reach_df=pd.DataFrame(reach_anchors) if reach_anchors else pd.DataFrame()
            if len(reach_df):
                reach_df['stage_dt']=pd.to_datetime(reach_df['stage_ts'],utc=True,errors='coerce')
                if m=='USA':
                    try:reach_df['reach_date']=reach_df['stage_dt'].dt.tz_convert('America/New_York').dt.date.astype(str)
                    except Exception:reach_df['reach_date']=reach_df['stage_dt'].dt.date.astype(str)
                else:
                    try:reach_df['reach_date']=reach_df['stage_dt'].dt.tz_convert('Asia/Seoul').dt.date.astype(str)
                    except Exception:reach_df['reach_date']=reach_df['stage_dt'].dt.date.astype(str)
                reach_day=reach_df[reach_df['reach_date']==report_date].copy() if report_date else reach_df.copy()
            else:
                reach_day=pd.DataFrame()

            observed_ready=int((reach_day['stage']=='READY').sum()) if len(reach_day) and 'stage' in reach_day.columns else 0
            observed_entry=int((reach_day['stage']=='ENTRY').sum()) if len(reach_day) and 'stage' in reach_day.columns else 0

            ec1,ec2,ec3,ec4=st.columns(4)
            ec1.metric('Episode 수',len(e))
            ec2.metric('60분 완료',int(e['ret_60m'].notna().sum()) if 'ret_60m' in e.columns else 0)
            ec3.metric('READY 실제 관측',observed_ready)
            ec4.metric('ENTRY 실제 관측',observed_entry)

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

        st.markdown('#### ⚓ Stage-Anchor Validation')
        st.caption('각 Episode의 첫 SETUP / 첫 READY / 첫 ENTRY 시점을 별도 Anchor로 평가합니다. 이제 Episode 시작 Power와 실제 READY/ENTRY 순간 Power를 구분해서 볼 수 있습니다.')
        anchors=api(f'/api/v4/validation/stage-anchors?market={m}&limit=5000&bridge_minutes=5').get('data') or []
        if anchors:
            adf=pd.DataFrame(anchors)
            adf['stage_dt']=pd.to_datetime(adf['stage_ts'],utc=True,errors='coerce')
            if m=='USA':
                try:adf['anchor_date']=adf['stage_dt'].dt.tz_convert('America/New_York').dt.date.astype(str)
                except Exception:adf['anchor_date']=adf['stage_dt'].dt.date.astype(str)
            else:
                try:adf['anchor_date']=adf['stage_dt'].dt.tz_convert('Asia/Seoul').dt.date.astype(str)
                except Exception:adf['anchor_date']=adf['stage_dt'].dt.date.astype(str)
            a=adf[adf['anchor_date']==report_date].copy() if report_date else adf.copy()
            for col in ['minutes_from_episode_start','power','power_delta','setup_count','trigger_count','ret_5m','ret_15m','ret_30m','ret_60m','mfe_pct','mae_pct']:
                if col in a.columns:a[col]=pd.to_numeric(a[col],errors='coerce')

            if len(a):
                summary=[]
                for stage in ['SETUP','READY','ENTRY']:
                    g=a[a['stage']==stage]
                    if not len(g):continue
                    r={'Anchor':stko(stage),'건수':len(g)}
                    r['평균Power']=round(g['power'].mean(),1) if g['power'].notna().any() else None
                    r['평균ΔPower']=round(g['power_delta'].mean(),1) if g['power_delta'].notna().any() else None
                    r['평균Trigger']=round(g['trigger_count'].mean(),2) if g['trigger_count'].notna().any() else None
                    r['Episode후분']=round(g['minutes_from_episode_start'].mean(),1) if g['minutes_from_episode_start'].notna().any() else None
                    for col,label in [('ret_5m','5분%'),('ret_15m','15분%'),('ret_30m','30분%'),('ret_60m','60분%')]:
                        vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                        r[label]=round(vals.mean(),3) if len(vals) else None
                    r['60분상승%']=round((g['ret_60m'].dropna()>0).mean()*100,1) if 'ret_60m' in g.columns and g['ret_60m'].notna().any() else None
                    r['MFE%']=round(g['mfe_pct'].mean(),3) if 'mfe_pct' in g.columns and g['mfe_pct'].notna().any() else None
                    r['MAE%']=round(g['mae_pct'].mean(),3) if 'mae_pct' in g.columns and g['mae_pct'].notna().any() else None
                    summary.append(r)
                st.markdown('##### SETUP vs READY vs ENTRY')
                st.dataframe(pd.DataFrame(summary),use_container_width=True,hide_index=True)

                # V4.5.2 Stage Funnel: actual observed reach semantics.
                setup_ids=set(a.loc[a['stage']=='SETUP','episode_id'].dropna().astype(int).tolist()) if 'episode_id' in a.columns else set()
                ready_ids=set(a.loc[a['stage']=='READY','episode_id'].dropna().astype(int).tolist()) if 'episode_id' in a.columns else set()
                entry_ids=set(a.loc[a['stage']=='ENTRY','episode_id'].dropna().astype(int).tolist()) if 'episode_id' in a.columns else set()

                total_eps=len(e) if 'e' in locals() else len(setup_ids | ready_ids | entry_ids)
                setup_reach=len(setup_ids)
                ready_reach=len(ready_ids)
                entry_reach=len(entry_ids)

                st.markdown('##### 🪜 실제 관측 Stage Funnel')
                f1,f2,f3,f4=st.columns(4)
                f1.metric('Episode',total_eps)
                f2.metric('SETUP 관측',setup_reach)
                f3.metric('READY 관측',ready_reach)
                f4.metric('ENTRY 관측',entry_reach)

                ready_rate=(ready_reach/setup_reach*100) if setup_reach else 0.0
                entry_from_ready=(entry_reach/ready_reach*100) if ready_reach else 0.0
                entry_from_setup=(entry_reach/setup_reach*100) if setup_reach else 0.0

                q1,q2,q3=st.columns(3)
                q1.metric('SETUP → READY',f'{ready_rate:.1f}%')
                q2.metric('READY → ENTRY',f'{entry_from_ready:.1f}%')
                q3.metric('SETUP → ENTRY',f'{entry_from_setup:.1f}%')

                # Average delay is calculated only from actually observed anchors.
                ready_delay=a.loc[a['stage']=='READY','minutes_from_episode_start'].dropna()
                entry_delay=a.loc[a['stage']=='ENTRY','minutes_from_episode_start'].dropna()
                d1,d2=st.columns(2)
                d1.metric('READY 평균 도달시간',f'{ready_delay.mean():.1f}분' if len(ready_delay) else '-')
                d2.metric('ENTRY 평균 도달시간',f'{entry_delay.mean():.1f}분' if len(entry_delay) else '-')

                if entry_reach==0:
                    st.info('오늘 데이터에는 실제 ENTRY 상태가 관측되지 않았습니다. HOLD/EXIT 계열 상태가 있더라도 ENTRY 도달로 추정하지 않습니다.')
                elif entry_reach<5:
                    st.warning(f'ENTRY 실제 관측은 {entry_reach}건뿐입니다. 진입 임계값 보정에는 아직 표본이 부족합니다.')

                st.markdown('##### 🥷 Entry Threshold Shadow Test')
                st.caption('실제 ENTRY 기준은 변경하지 않습니다. 각 조합은 Episode당 최초 1개 가상 진입만 사용합니다. Trigger는 해당 조합의 Power/ΔPower 가속 조건을 포함해 5개 체크로 다시 계산합니다.')
                shadow=api(f'/api/v4/validation/entry-shadow?market={m}&limit=5000&bridge_minutes=5') or {}
                grid=shadow.get('grid') or []
                current_ready=shadow.get('current_ready') or {}
                current_core=shadow.get('current_core') or {}

                s1,s2,s3,s4=st.columns(4)
                s1.metric('현재 READY Episode',current_ready.get('episodes',0))
                s2.metric('현재 ENTRY Episode',current_core.get('episodes',0))
                s3.metric('READY 30분%',f"{current_ready.get('ret_30m'):+.3f}%" if current_ready.get('ret_30m') is not None else '-')
                s4.metric('ENTRY 30분%',f"{current_core.get('ret_30m'):+.3f}%" if current_core.get('ret_30m') is not None else '-')

                if grid:
                    gdf=pd.DataFrame(grid)
                    # Avoid ranking tiny samples above mature profiles.
                    gdf['신뢰표본']=pd.to_numeric(gdf['complete_60'],errors='coerce').fillna(0)
                    gdf['30분점수']=pd.to_numeric(gdf['ret_30m'],errors='coerce')
                    view=gdf.rename(columns={
                        'profile':'Shadow','episodes':'Episode','complete_60':'60분완료',
                        'power_min':'Power≥','trigger_min':'Trigger≥','delta_min':'ΔPower≥',
                        'ret_5m':'5분%','ret_15m':'15분%','ret_30m':'30분%','ret_60m':'60분%',
                        'hit_60_pct':'60분상승%','mfe_pct':'MFE%','mae_pct':'MAE%',
                        'core_pass_pct':'Core통과%'
                    })
                    cols=[c for c in ['Shadow','Power≥','Trigger≥','ΔPower≥','Episode','60분완료','5분%','15분%','30분%','60분%','60분상승%','MFE%','MAE%','Core통과%'] if c in view.columns]
                    st.dataframe(view[cols],use_container_width=True,hide_index=True)

                    # V4.5.4 confidence ranking.
                    # Diagnostic comparison score only -- never a probability.
                    def _n(v,default=0.0):
                        try:
                            x=float(v)
                            return default if pd.isna(x) else x
                        except Exception:
                            return default

                    ranked=gdf.copy()
                    ranked['sample_score']=ranked.apply(
                        lambda r:min(25.0,_n(r.get('complete_60'))*3.0+_n(r.get('episodes'))*0.5),axis=1
                    )
                    ranked['expectancy_score']=ranked.apply(
                        lambda r:max(-30.0,min(30.0,
                            _n(r.get('ret_15m'))*7.0+
                            _n(r.get('ret_30m'))*9.0+
                            _n(r.get('ret_60m'))*5.0
                        )),axis=1
                    )
                    ranked['risk_score']=ranked.apply(
                        lambda r:max(-15.0,min(15.0,
                            _n(r.get('mfe_pct'))*4.0+
                            _n(r.get('mae_pct'))*6.0
                        )),axis=1
                    )
                    ranked['core_score']=ranked.apply(
                        lambda r:max(-10.0,min(10.0,(_n(r.get('core_pass_pct'),50.0)-50.0)*0.20)),axis=1
                    )
                    ranked['raw_confidence']=(
                        ranked['sample_score']+ranked['expectancy_score']+ranked['risk_score']+ranked['core_score']
                    )
                    def _sample_factor(r):
                        n=int(_n(r.get('complete_60')))
                        return 0.25 if n<=0 else 0.50 if n<=2 else 0.75 if n<=4 else 1.00
                    ranked['sample_factor']=ranked.apply(_sample_factor,axis=1)
                    ranked['confidence_score']=(ranked['raw_confidence']*ranked['sample_factor']).round(1)

                    def _stability(r):
                        stats=r.get('session_stats')
                        if not isinstance(stats,list) or not stats:return '표본부족'
                        usable=[x for x in stats if x.get('ret_30m') is not None or x.get('ret_60m') is not None]
                        if not usable:return '표본부족'
                        days=len(usable); pos30=sum(1 for x in usable if x.get('ret_30m') is not None and _n(x.get('ret_30m'))>0)
                        r60=[x for x in usable if x.get('ret_60m') is not None]; pos60=sum(1 for x in r60 if _n(x.get('ret_60m'))>0)
                        if days>=3 and pos30/days>=0.67 and r60 and pos60/len(r60)>=0.60:return '반복 우수'
                        if days==1 and pos30==1:return '1일 우수'
                        if days>=2 and pos30/days<0.60:return '불안정'
                        return '관찰'
                    ranked['세션안정성']=ranked.apply(_stability,axis=1)

                    def _grade(r):
                        n60=int(_n(r.get('complete_60')))
                        r15=_n(r.get('ret_15m')); r30=_n(r.get('ret_30m')); r60=_n(r.get('ret_60m'))
                        mae=_n(r.get('mae_pct'),-99); core=_n(r.get('core_pass_pct'))
                        stability=str(r.get('세션안정성') or '')
                        if n60<5:return '표본부족'
                        if r15>0 and r30>0 and r60>0 and mae>-0.50 and core>=60 and stability=='반복 우수':
                            return '추천 후보'
                        return '관찰'

                    ranked['판정']=ranked.apply(_grade,axis=1)

                    # Profiles with exactly the same observed outcomes are effectively
                    # duplicates for this session. Keep one representative and show all
                    # equivalent thresholds together.
                    sig_cols=['episodes','complete_60','ret_5m','ret_15m','ret_30m','ret_60m',
                              'hit_60_pct','mfe_pct','mae_pct','core_pass_pct']
                    for c in sig_cols:
                        if c not in ranked.columns: ranked[c]=None
                    ranked['_sig']=ranked[sig_cols].apply(
                        lambda r:tuple(None if pd.isna(x) else round(float(x),6) for x in r),axis=1
                    )

                    compact=[]
                    for sig,g in ranked.groupby('_sig',dropna=False):
                        # Prefer the least restrictive representative; equivalent stricter
                        # rows are displayed as aliases rather than pretending to be new evidence.
                        gg=g.sort_values(['power_min','trigger_min','delta_min'])
                        rep=gg.iloc[0].copy()
                        rep['동일결과 조합']=' / '.join(gg['profile'].astype(str).tolist())
                        rep['중복수']=len(gg)
                        stats=rep.get('session_stats'); rep['세션수']=len(stats) if isinstance(stats,list) else int(_n(rep.get('session_count')))
                        compact.append(rep)
                    cdf=pd.DataFrame(compact)
                    if len(cdf):
                        order={'추천 후보':0,'관찰':1,'표본부족':2}
                        cdf['_grade_order']=cdf['판정'].map(order).fillna(9)
                        cdf=cdf.sort_values(
                            ['_grade_order','confidence_score','complete_60','ret_30m'],
                            ascending=[True,False,False,False],
                            na_position='last'
                        )

                        st.markdown('##### 🏅 Shadow Confidence Ranking')
                        st.caption('Confidence는 확률이 아닌 조합 비교용 진단 점수이며 60분 완료 표본수에 따라 25/50/75/100%로 할인됩니다. 실제 기준 변경 후보는 여러 거래일에서 반복 우수가 확인되어야 합니다.')
                        cv=cdf.rename(columns={
                            'profile':'대표 Shadow','confidence_score':'Confidence',
                            'episodes':'Episode','complete_60':'60분완료',
                            'ret_15m':'15분%','ret_30m':'30분%','ret_60m':'60분%',
                            'hit_60_pct':'60분상승%','mfe_pct':'MFE%','mae_pct':'MAE%',
                            'core_pass_pct':'Core통과%'
                        })
                        ccols=[c for c in ['판정','세션안정성','대표 Shadow','동일결과 조합','중복수','Confidence','세션수',
                                           'Episode','60분완료','15분%','30분%','60분%',
                                           '60분상승%','MFE%','MAE%','Core통과%'] if c in cv.columns]
                        st.dataframe(cv[ccols],use_container_width=True,hide_index=True)

                        rec=cdf[cdf['판정']=='추천 후보']
                        watch=cdf[cdf['판정']=='관찰']
                        scarce=cdf[cdf['판정']=='표본부족']
                        k1,k2,k3=st.columns(3)
                        k1.metric('추천 후보',len(rec))
                        k2.metric('관찰',len(watch))
                        k3.metric('표본부족',len(scarce))

                        if len(rec):
                            best=rec.iloc[0]
                            st.success(
                                f"현재 Shadow 추천 후보 · {best['profile']} · Confidence {best['confidence_score']:.1f} · "
                                f"60분완료 {int(best['complete_60'])} · 15분 {best['ret_15m']:+.3f}% · "
                                f"30분 {best['ret_30m']:+.3f}% · 60분 {best['ret_60m']:+.3f}%"
                            )
                        elif len(watch):
                            best=watch.iloc[0]
                            st.warning(
                                f"아직 실제 기준 변경 후보는 없음 · 최상위 관찰 {best['profile']} · "
                                f"Confidence {best['confidence_score']:.1f} · 60분완료 {int(best['complete_60'])}. "
                                "성과/표본 조건이 모두 충족될 때까지 Shadow 유지"
                            )
                        else:
                            st.info('모든 Shadow 조합이 아직 표본부족입니다. 실제 ENTRY 기준은 유지합니다.')

                        # Delta-Power redundancy check: same P/T profiles with D0/D2/D4
                        # that produce the exact same observed signature.
                        redundant=[]
                        for (p,tg),g in ranked.groupby(['power_min','trigger_min']):
                            if len(g)<2:continue
                            if int(pd.to_numeric(g['episodes'],errors='coerce').fillna(0).max())<3:continue
                            sigs=g['_sig'].nunique(dropna=False)
                            if sigs==1:redundant.append(f"P{int(p)}/T{int(tg)}: D0·D2·D4 동일 (Episode≥3)")
                        if redundant:
                            st.info('ΔPower 중복 관측 · '+' | '.join(redundant[:8])+' · Episode가 실제 존재하는 조합만 표시합니다.')

                        st.markdown('##### 📆 Multi-session Shadow Stability')
                        stability_rows=[]
                        for _,rr in ranked.iterrows():
                            stats=rr.get('session_stats')
                            if not isinstance(stats,list):continue
                            for ss in stats:
                                stability_rows.append({'Shadow':rr.get('profile'),'거래일':ss.get('session_date'),'Episode':ss.get('episodes'),
                                                       '60분완료':ss.get('complete_60'),'15분%':ss.get('ret_15m'),'30분%':ss.get('ret_30m'),
                                                       '60분%':ss.get('ret_60m'),'MFE%':ss.get('mfe_pct'),'MAE%':ss.get('mae_pct')})
                        if stability_rows:
                            sdf=pd.DataFrame(stability_rows); focus=['P55/T3/D0','P55/T4/D0','P60/T4/D0','P60/T4/D2','P60/T4/D4']
                            sf=sdf[sdf['Shadow'].isin(focus)].copy()
                            st.dataframe((sf if len(sf) else sdf).sort_values(['Shadow','거래일']),use_container_width=True,hide_index=True)
                            days=sdf['거래일'].nunique()
                            if days<3:st.info(f'현재 Shadow 데이터 거래일은 {days}일입니다. 최소 3개 거래일 반복 전에는 실제 ENTRY 기준을 변경하지 않습니다.')
                        else:
                            st.caption('거래일별 Shadow 통계가 아직 없습니다.')

                    with st.expander('Shadow 해석 기준'):
                        st.write('• CURRENT_READY / CURRENT_CORE는 저장 당시 실제 live gate 판정입니다.')
                        st.write('• Pxx/Ty/Dz는 Power≥xx, 동적 Trigger≥y/5, ΔPower≥z를 만족한 첫 시점을 가상 Anchor로 잡습니다.')
                        st.write('• Shadow는 chase guard와 5분 Setup을 통과한 mark만 사용합니다.')
                        st.write('• Core통과%는 해당 Shadow Anchor에서 양봉+직전고가돌파+거래량확장이 동시에 성립한 비율입니다.')
                        st.write('• 단일 세션 또는 소표본 상위 조합을 실제 ENTRY 기준으로 자동 승격하지 않습니다.')

                ready=a[a['stage']=='READY'].copy()
                if len(ready):
                    ready['Power구간']=pd.cut(ready['power'],bins=[-1e9,20,30,40,50,60,1e9],labels=['<20','20~30','30~40','40~50','50~60','60+'],right=False)
                    rows=[]
                    for bucket,g in ready.groupby('Power구간',observed=True):
                        r={'READY Power':str(bucket),'건수':len(g)}
                        for col,label in [('ret_5m','5분%'),('ret_15m','15분%'),('ret_30m','30분%'),('ret_60m','60분%')]:
                            vals=g[col].dropna()
                            r[label]=round(vals.mean(),3) if len(vals) else None
                        r['MFE%']=round(g['mfe_pct'].mean(),3) if g['mfe_pct'].notna().any() else None
                        r['MAE%']=round(g['mae_pct'].mean(),3) if g['mae_pct'].notna().any() else None
                        rows.append(r)
                    st.markdown('##### READY 순간 Power별 성과')
                    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

                entry=a[a['stage']=='ENTRY'].copy()
                if len(entry):
                    st.markdown('##### ENTRY Anchor 개별 성과')
                    cols=[c for c in ['stage_ts','symbol','power','power_delta','setup_count','trigger_count','minutes_from_episode_start','ret_5m','ret_15m','ret_30m','ret_60m','mfe_pct','mae_pct'] if c in entry.columns]
                    st.dataframe(entry[cols].sort_values('stage_ts',ascending=False),use_container_width=True,hide_index=True)

                with st.expander('Stage Anchor 전체 목록'):
                    cols=[c for c in ['episode_id','stage_ts','symbol','stage','minutes_from_episode_start','anchor_price','power','power_delta','finder_rank','setup_count','trigger_count','ret_5m','ret_15m','ret_30m','ret_60m','mfe_pct','mae_pct'] if c in a.columns]
                    st.dataframe(a[cols].sort_values('stage_ts',ascending=False),use_container_width=True,hide_index=True)
        else:
            st.caption('Stage Anchor를 만들 수 있는 Episode 데이터가 아직 없습니다.')

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
st.divider(); st.caption('V4.6.2.1 · CANDIDATE DATA WARM + FAIR BRIDGE AUDIT · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY')
if auto_live:
    time.sleep(5)
    st.rerun()
