import os, time, requests, pandas as pd
import altair as alt
import streamlit as st
from dotenv import load_dotenv
from trader.config import TradingConfig
from trader.demo import demo_candidates, demo_bars
from trader.screener import rank_candidates
from trader.signals import intraday_signal

load_dotenv(); cfg=TradingConfig()
try: API_URL=st.secrets.get('DAYTRADER_API_URL','')
except Exception: API_URL=os.getenv('DAYTRADER_API_URL','')
API_URL=str(API_URL).rstrip('/')


def api_post(path):
    try:
        r=requests.post(API_URL+path,timeout=180)
        if r.status_code>=400:
            return {'ok':False,'error':r.text,'status_code':r.status_code}
        return r.json()
    except Exception as e:
        return {'ok':False,'error':str(e)}

st.set_page_config(page_title='DAY TRADER WEB',page_icon='📈',layout='wide',initial_sidebar_state='collapsed')
st.markdown('''<style>
.block-container{padding-top:1rem;max-width:1550px}
.hero{display:flex;justify-content:space-between;align-items:center;padding:18px 22px;border:1px solid rgba(128,128,128,.2);border-radius:18px;margin-bottom:14px}
.hero h1{margin:0;font-size:1.9rem}
.badge{display:inline-block;padding:6px 10px;border-radius:999px;background:rgba(128,128,128,.13);font-weight:700;font-size:.8rem;margin-right:6px}
.signal{padding:16px 18px;border-radius:16px;border:1px solid rgba(128,128,128,.2);font-size:1.03rem;margin:8px 0 14px}
.risk{padding:10px 14px;border-radius:12px;border:1px solid rgba(255,165,0,.35);margin:6px 0}
[data-testid="stMetricValue"]{font-size:1.85rem}
</style>''',unsafe_allow_html=True)

def api(path, timeout=8):
    if not API_URL: return None
    try:
        r=requests.get(API_URL+path,timeout=timeout); r.raise_for_status(); return r.json()
    except Exception as e:
        st.sidebar.warning(f'LIVE API 연결 대기: {e}'); return None

def fmt_px(v):
    try:
        x=float(v)
        return '-' if pd.isna(x) else f'${x:,.2f}'
    except Exception:
        return '-'

def fmt_level(v):
    try:
        x=float(v)
        return '-' if pd.isna(x) else f'{x:,.2f}'
    except Exception:
        return '-'

health=api('/health') if API_URL else None
live=bool(health and health.get('ok'))
mode='LIVE DATA' if live else 'DEMO DATA'
version=(health or {}).get('version','2.5.3') if live else '2.5.3'
st.markdown(f'''<div class="hero"><div><h1>DAY TRADER WEB</h1><div>TOP10 → 1·5분봉 Signal → Position → Critical Alert</div></div><div><span class="badge">{mode}</span><span class="badge">NO AUTO ORDER</span><span class="badge">v{version}</span></div></div>''',unsafe_allow_html=True)

st.caption('V2.5.3 · KOREA GAMMA · 다중소스 + CHASE_RISK + 코드 정규화 · NO AUTO ORDER')


tab_trading, tab_brief, tab_research, tab_archive, tab_live = st.tabs([
    '📈 Trading', '🗞️ Briefing', '🧪 Research', '📚 Archive', '✅ Live Validation'
])

with tab_trading:
    market_view=st.radio('시장', ['🇺🇸 USA','🇰🇷 KOREA'], horizontal=True, key='trading_market_view')

    if market_view=='🇰🇷 KOREA':
        ks=api('/api/korea/status') if live else {}
        st.subheader('🇰🇷 KOREA · 국내주식 Day Trader BASE')
        st.caption('V2.5.3은 한국장 거래대금 상위 실데이터로 Universe/TOP10 ALPHA를 계산합니다. USA 점수 공식과 분리합니다.')

        k1,k2,k3,k4=st.columns(4)
        k1.metric('국내 REST','READY' if (ks or {}).get('adapter_ready') else 'WAIT')
        k2.metric('Universe',str((ks or {}).get('universe_count') or 0))
        k3.metric('Trading Score','ALPHA' if (ks or {}).get('score_live') else 'WAIT')
        k4.metric('자동주문','OFF')

        c1,c2=st.columns([1,3])
        with c1:
            if live and st.button('🇰🇷 삼성전자 연결 테스트',use_container_width=True,key='kr_quote_probe'):
                q=api('/api/korea/quote/005930',timeout=25) or {}
                if q.get('ok'):
                    st.session_state['kr_quote_probe_result']=q
                    st.success('국내주식 ka10004 연결 성공')
                else:
                    st.error('국내주식 연결 실패')
        with c2:
            st.caption('첫 연결 검증은 공식 ka10004 주식호가요청으로 005930(삼성전자)을 조회합니다.')

        qtest=st.session_state.get('kr_quote_probe_result')
        if qtest:
            with st.expander('국내주식 연결 테스트 RAW 응답',expanded=False):
                st.json(qtest)

        st.markdown('### 🇰🇷 한국장 Universe')
        if live and st.button('🔍 한국장 시장 재검색',use_container_width=True,key='kr_market_scan'):
            with st.spinner('KOSPI/KOSDAQ 거래대금 상위 → Universe → KOREA TOP10 계산 중...'):
                rr=api_post('/api/korea/scan?limit=50') or {}
            if rr.get('ok'):
                st.success(f"한국장 Multi-Source Universe {rr.get('count',0)}개 생성 · KOSPI 원천 {((rr.get('market_breakdown') or {}).get('KOSPI',0))} · KOSDAQ 원천 {((rr.get('market_breakdown') or {}).get('KOSDAQ',0))}")
                st.rerun()
            else:
                st.error('한국장 재검색 실패: '+str(rr.get('error') or rr))

        ku=api('/api/korea/universe') if live else {}
        urows=(ku or {}).get('rows') or []
        if urows:
            udf=pd.DataFrame(urows)
            keep=['symbol','raw_symbol','name','market','price','change_pct','volume','trading_value','surge_pct','source_count','source_text','value_rank','volume_rank','surge_rank','gainer_rank','loser_rank','raw_score','risk_penalty','chase_risk','surge_risk','score','bias']
            udf=udf[[c for c in keep if c in udf.columns]].rename(columns={'value_rank':'거래대금순위','symbol':'종목','raw_symbol':'원본코드','name':'종목명','market':'시장','price':'현재가','change_pct':'등락률%','volume':'거래량','trading_value':'거래대금','score':'Korea Score','bias':'방향','source':'소스'})
            st.dataframe(udf,use_container_width=True,hide_index=True)

        st.markdown('### 🇰🇷 한국장 TOP10 · KOREA_CURRENT_V1_GAMMA')
        st.caption('Trading Score는 Raw Score에서 CHASE_RISK/급증위험 감점을 차감한 값입니다. EXTREME은 후보에서 제거하지 않고 위험하게 표시합니다.')
        kt=api('/api/korea/top10') if live else {}
        trows=(kt or {}).get('data') or []
        if trows:
            tdf=pd.DataFrame(trows); tdf.insert(0,'순위',range(1,len(tdf)+1))
            keep=['순위','symbol','name','market','score','raw_score','risk_penalty','chase_risk','surge_risk','bias','price','change_pct','source_count','source_text','value_rank','volume_rank','surge_rank','trading_value']
            tdf=tdf[[c for c in keep if c in tdf.columns]].rename(columns={'symbol':'종목','name':'종목명','market':'시장','score':'Trading Score','raw_score':'Raw Score','risk_penalty':'위험감점','chase_risk':'추격위험','surge_risk':'급증위험','bias':'방향','price':'현재가','change_pct':'등락률%','value_rank':'거래대금순위','trading_value':'거래대금'})
            st.dataframe(tdf,use_container_width=True,hide_index=True)
        else:
            st.info('아직 한국장 Universe가 없습니다. 위의 한국장 시장 재검색을 한 번 눌러주세요.')

        nexts=(ks or {}).get('next_sources') or []
        if nexts:
            with st.expander('V2.5.3 확장 예정 데이터 소스',expanded=False):
                st.dataframe(pd.DataFrame(nexts),use_container_width=True,hide_index=True)
        st.caption('현재 한국장 점수는 거래대금 순위 + 당일 등락률 기반 ALPHA입니다. V2.5.3에서 거래량 급증/등락률 순위를 병합합니다.')
        st.stop()

    qqq=api('/api/quote/QQQ') if live else {}; smh=api('/api/quote/SMH') if live else {}
    qqq_pct=float((qqq or {}).get('change_pct') or 0); smh_pct=float((smh or {}).get('change_pct') or 0)
    market_label='BULL' if qqq_pct>=.3 else ('BEAR' if qqq_pct<=-.3 else 'NEUTRAL')
    semi_label='STRONG' if smh_pct>=.5 else ('WEAK' if smh_pct<=-.5 else 'NEUTRAL')
    c1,c2,c3,c4=st.columns(4)
    c1.metric('NASDAQ Bias',f'{market_label} {qqq_pct:+.2f}%')
    c2.metric('Semiconductor',f'{semi_label} {smh_pct:+.2f}%')
    c3.metric('Market Mode','TREND' if abs(qqq_pct)>=.4 else 'MIXED')
    c4.metric('Data','LIVE' if live else 'DEMO')

    if live:
        uni=api('/api/universe') or {}
        if uni.get('count'):
            st.caption(
                f"자동 Universe {uni.get('count')}개 · AUTO {uni.get('auto_count', max(0, uni.get('count',0)-len(uni.get('core') or [])))}개 "
                f"· Core {len(uni.get('core') or [])}개 · EXTREME 제외 {uni.get('extreme_count',0)}개 · 약 10분마다 재검색"
            )
            with st.expander('오늘 자동 발굴 Universe 보기', expanded=False):
                drows=uni.get('rows') or []
                if drows:
                    udf=pd.DataFrame(drows)
                    # CORE rows are discovery placeholders; overlay current live quote values for display.
                    try:
                        qdf=pd.DataFrame(api('/api/quotes') or [])
                        if not qdf.empty and 'symbol' in qdf.columns:
                            qmap=qdf.set_index('symbol').to_dict('index')
                            for idx,row in udf.iterrows():
                                if row.get('origin')=='CORE' and row.get('symbol') in qmap:
                                    qv=qmap[row.get('symbol')]
                                    udf.at[idx,'price']=qv.get('price',row.get('price'))
                                    udf.at[idx,'change_pct']=qv.get('change_pct',row.get('change_pct'))
                    except Exception:
                        pass
                    keep=['origin','symbol','name','asset_type','exchange','price','change_pct',
                          'volume_rank','dollar_rank','gainer_rank','loser_rank','surge_rank',
                          'surge_pct','discovery_score','chase_risk','sources']
                    udf=udf[[c for c in keep if c in udf.columns]].rename(columns={
                        'origin':'구분','symbol':'종목','name':'이름','asset_type':'유형','exchange':'거래소',
                        'price':'현재가','change_pct':'당일%','volume_rank':'거래량순위',
                        'dollar_rank':'거래대금순위','gainer_rank':'상승률순위','loser_rank':'하락률순위',
                        'surge_rank':'거래량급증순위','surge_pct':'급증률','discovery_score':'Discovery Score',
                        'chase_risk':'추격위험','sources':'발굴근거'
                    })
                    st.dataframe(udf,use_container_width=True,hide_index=True)
            if uni.get('extreme_rows'):
                with st.expander(f"EXTREME 제외 종목 {uni.get('extreme_count',0)}개 보기", expanded=False):
                    ex=pd.DataFrame(uni.get('extreme_rows') or [])
                    keep=['symbol','name','asset_type','exchange','price','change_pct','volume','sources','chase_risk']
                    ex=ex[[c for c in keep if c in ex.columns]].rename(columns={
                        'symbol':'종목','name':'이름','asset_type':'유형','exchange':'거래소',
                        'price':'현재가','change_pct':'당일%','volume':'거래량','sources':'발굴근거','chase_risk':'추격위험'
                    })
                    st.dataframe(ex,use_container_width=True,hide_index=True)

        scan_status=api('/api/scan/status') or {}
        sc1,sc2,sc3=st.columns([1.1,1.2,3.7])
        with sc1:
            if st.button('↻ 점수 새로고침',use_container_width=True,key='score_refresh'):
                st.rerun()
        with sc2:
            if st.button('🔍 시장 재검색',use_container_width=True,key='market_rescan'):
                with st.spinner('시장 랭킹 재조회 → Universe 재구성 → 신규 종목 데이터 준비 중...'):
                    res=api_post('/api/scan/market') or {}
                if res.get('ok'):
                    added=res.get('added') or []
                    removed=res.get('removed') or []
                    newtop=res.get('top10_new') or []
                    st.success(
                        f"시장 재검색 완료 · Universe {res.get('before_count')} → {res.get('after_count')} "
                        f"· 신규 {len(added)} · 제외 {len(removed)} · TOP10 신규 {len(newtop)}"
                    )
                    if added:
                        st.caption('신규 후보: '+', '.join(added[:15]))
                    if newtop:
                        st.caption('새 TOP10 진입: '+', '.join(newtop))
                    st.rerun()
                elif res.get('cooldown'):
                    st.warning(f"재검색 쿨다운 중입니다. 약 {res.get('retry_after')}초 후 다시 눌러주세요.")
                else:
                    st.error('시장 재검색 실패: '+str(res.get('error') or res))
        with sc3:
            lr=(scan_status.get('last_result') or {})
            if lr:
                st.caption(
                    f"마지막 수동검색: {lr.get('finished_at','-')} · "
                    f"Universe {lr.get('before_count','-')}→{lr.get('after_count','-')} · "
                    f"Archive {lr.get('archive_label','-')}"
                )
    st.subheader('오늘의 단타 후보 TOP 10')
    if live:
        payload=api('/api/screener?top_n=10') or {'data':[]}; rows=payload.get('data',[])
        if rows:
            show=pd.DataFrame(rows); show.insert(0,'순위',range(1,len(show)+1))
            cols=['순위','symbol','score','bias','price','change_pct','ma5','ma5_slope_pct','rvol','atr_pct','dollar_volume','exchange']
            show=show[[c for c in cols if c in show.columns]].rename(columns={'symbol':'종목','score':'Trading Score','bias':'방향','price':'현재가','change_pct':'당일%','ma5':'MA5','ma5_slope_pct':'MA5기울기%','rvol':'RVOL','atr_pct':'ATR%','dollar_volume':'거래대금','exchange':'거래소'})
            for c in ['현재가','MA5','MA5기울기%','RVOL','ATR%','당일%']:
                if c in show.columns: show[c]=pd.to_numeric(show[c],errors='coerce').round(2)
            if '거래대금' in show.columns: show['거래대금']=pd.to_numeric(show['거래대금'],errors='coerce').round(0)
            st.dataframe(show,use_container_width=True,hide_index=True)

            cmp=api('/api/screener/compare?top_n=10') or {}
            with st.expander('🌓 CURRENT vs SHADOW TOP10 비교', expanded=False):
                st.caption('SHADOW는 연구용 LIVE_CANDIDATE_V1입니다. 실제 Trading Score나 주문에는 사용하지 않습니다.')
                m1,m2,m3=st.columns(3)
                m1.metric('TOP10 겹침',f"{int(cmp.get('overlap_count') or 0)}/10")
                m2.metric('CURRENT 전용',len(cmp.get('current_only') or []))
                m3.metric('SHADOW 전용',len(cmp.get('shadow_only') or []))
                crows=cmp.get('rows') or []
                if crows:
                    cdf=pd.DataFrame(crows)
                    keep=['symbol','current_rank','shadow_rank','rank_delta','current_score','shadow_score','change_pct','bias']
                    cdf=cdf[[c for c in keep if c in cdf.columns]].rename(columns={
                        'symbol':'종목','current_rank':'Current 순위','shadow_rank':'Shadow 순위',
                        'rank_delta':'Shadow 상승칸','current_score':'Current Score',
                        'shadow_score':'Shadow Score','change_pct':'당일%','bias':'방향'
                    })
                    for c in ['Current Score','Shadow Score','당일%']:
                        if c in cdf.columns: cdf[c]=pd.to_numeric(cdf[c],errors='coerce').round(2)
                    st.dataframe(cdf,use_container_width=True,hide_index=True)
                if cmp.get('shadow_only'):
                    st.caption('SHADOW 신규 후보: '+', '.join(cmp.get('shadow_only')))
            symbols=[r['symbol'] for r in rows]
            for sym in (uni.get('core') or []):
                if sym not in symbols:
                    symbols.append(sym)
            if rows[0]['score'] < cfg.watch_score:
                st.info('현재 WATCH 기준(70점)을 넘는 후보가 없습니다. NO TRADE도 유효한 판단입니다.')
        else:
            st.info('5일 일봉 지표 초기 수집 중입니다. 잠시 후 자동으로 TOP10이 계산됩니다.')
            symbols=[q['symbol'] for q in (api('/api/quotes') or [])]
    else:
        ranked=rank_candidates(demo_candidates(),cfg).copy(); ranked.insert(0,'rank',range(1,len(ranked)+1))
        st.dataframe(ranked,use_container_width=True,hide_index=True); symbols=ranked['symbol'].tolist()

    if live:
        hist=(api('/api/ranking-history') or {'data':[]}).get('data',[])
        if hist:
            st.caption('추천 유지도: 미국장 T-10분 → T-1분 → 개장 +7분')
            h=pd.DataFrame(hist); pivot=h.pivot_table(index='symbol',columns='label',values='score',aggfunc='first').reset_index()
            st.dataframe(pivot,use_container_width=True,hide_index=True)


    selected=st.selectbox('오늘 집중 감시 종목',symbols,index=0 if symbols else None)
    if selected:
        st.divider(); st.subheader(f'{selected} 집중 감시')
        if live:
            q=api(f'/api/quote/{selected}') or {}; sig=api(f'/api/signal/{selected}') or {}
            b1=api(f'/api/bars/{selected}?minutes=1&limit=200') or {'data':[]}
            b5=api(f'/api/bars/{selected}?minutes=5&limit=100') or {'data':[]}
            m1,m2,m3,m4,m5,m6=st.columns(6)
            m1.metric('상태',sig.get('state','DATA WARMUP')); m2.metric('방향',sig.get('bias','NEUTRAL'))
            m3.metric('Signal',f"{sig.get('score',0)}/100")
            m4.metric('LONG / SHORT',f"{sig.get('long_score',0)} / {sig.get('short_score',0)}")
            m5.metric('5M Confirm',sig.get('confirm_5m',0)); m6.metric('현재가',fmt_px(q.get('price')))

            state=sig.get('state','DATA WARMUP'); bias=sig.get('bias','NEUTRAL')
            st.markdown(f'''<div class="signal"><b>{selected} · {bias} · {state}</b><br>{sig.get('reason','데이터 수집 중')}<br><small>가격무효화 {fmt_level(sig.get('invalidation'))} · T1 {fmt_level(sig.get('target1'))} · T2 {fmt_level(sig.get('target2'))}</small></div>''',unsafe_allow_html=True)
            if sig.get('risks'):
                st.markdown(f'''<div class="risk"><b>리스크</b> · {sig.get('risks')}</div>''',unsafe_allow_html=True)

            ind=sig.get('indicators') or {}; ctx=sig.get('context') or {}
            if ind:
                i1,i2,i3,i4,i5,i6=st.columns(6)
                i1.metric('VWAP',fmt_level(ind.get('vwap'))); i2.metric('EMA9',fmt_level(ind.get('ema9')))
                i3.metric('EMA20',fmt_level(ind.get('ema20'))); i4.metric('RSI',f"{float(ind.get('rsi14') or 0):.1f}")
                i5.metric('Bar RVOL',f"{float(ind.get('rvol') or 0):.2f}x")
                i6.metric('QQQ / SMH',f"{float(ctx.get('qqq_pct') or 0):+.2f}% / {float(ctx.get('smh_pct') or 0):+.2f}%")

            def intraday_chart(rows, title):
                df=pd.DataFrame(rows); st.caption(title)
                if df.empty:
                    st.info('분봉 데이터 준비 중'); return
                df['time']=pd.to_datetime(df['time'],utc=True,errors='coerce')
                for c in ['open','high','low','close','volume']:
                    df[c]=pd.to_numeric(df.get(c),errors='coerce')
                df=df.dropna(subset=['time','close']).sort_values('time')
                if df.empty: return
                df['ema9']=df['close'].ewm(span=9,adjust=False).mean()
                df['ema20']=df['close'].ewm(span=20,adjust=False).mean()
                typ=(df['high'].fillna(df['close'])+df['low'].fillna(df['close'])+df['close'])/3
                vol=df['volume'].fillna(0).clip(lower=0)
                denom=vol.cumsum().replace(0,pd.NA)
                df['vwap']=(typ*vol).cumsum()/denom
                long=df.melt(id_vars=['time'],value_vars=['close','ema9','ema20','vwap'],var_name='series',value_name='price').dropna()
                chart=alt.Chart(long).mark_line().encode(
                    x=alt.X('time:T',title=None),
                    y=alt.Y('price:Q',title='Price',scale=alt.Scale(zero=False)),
                    color=alt.Color('series:N',title=None),
                    tooltip=[alt.Tooltip('time:T'),alt.Tooltip('series:N'),alt.Tooltip('price:Q',format='.4f')]
                ).properties(height=300).interactive()
                st.altair_chart(chart,use_container_width=True)

            l,r=st.columns(2)
            with l: intraday_chart(b1['data'],'1분봉 LIVE · Close / EMA9 / EMA20 / VWAP')
            with r: intraday_chart(b5['data'],'5분봉 LIVE · Close / EMA9 / EMA20 / VWAP')

            st.subheader('포지션 관리')
            pc1,pc2=st.columns([1,2])
            with pc1: side=st.selectbox('진입 방향',['LONG','SHORT'],index=0 if bias!='SHORT' else 1)
            with pc2: entry=st.number_input('실제 진입가격 (카카오페이 체결가)',min_value=0.0,value=0.0,step=0.01,format='%.4f')
            if entry>0:
                pos=api(f'/api/position/{selected}?entry={entry}&side={side}') or {}
                p1,p2,p3,p4=st.columns(4)
                p1.metric('Position',pos.get('state','-')); p2.metric('방향',pos.get('side',side))
                p3.metric('수익률',f"{float(pos.get('pnl_pct') or 0):+.2f}%"); p4.metric('현재가',fmt_px(pos.get('price')))
                st.info(pos.get('reason',''))
                if pos.get('critical'): st.error('중요 신호: EXIT 또는 강한 익절/리스크 이벤트')
        else:
            bars=demo_bars(selected); sig=intraday_signal(selected,bars,market_bias=.7,sector_bias=.6,cfg=cfg)
            st.metric('상태',sig.state); st.line_chart(bars.set_index('time')['close'],height=300)




with tab_brief:
    st.subheader('🗞️ Pre-Open Intelligence Briefing')
    st.caption('웹 접속 여부와 무관하게 미국장 정규개장 30분 전(09:00 ET) 서버가 자동으로 Universe 재검색 → CURRENT/SHADOW TOP10 → News/AI Intelligence Report → Archive 저장을 끝까지 수행합니다. 수동 생성도 V2.5a부터 서버 Job으로 비동기 처리합니다.')
    c1,c2,c3=st.columns([1.2,1.2,3.6])
    with c1:
        if live and st.button('지금 미국장 브리핑 생성',use_container_width=True,key='brief_generate_now'):
            res=api_post('/api/briefing/generate?market=USA') or {}
            if res.get('ok') and res.get('job_id'):
                st.session_state['brief_job_id']=res.get('job_id')
                st.session_state['brief_job_started']=time.time()
                st.rerun()
            else:
                st.error('브리핑 작업 시작 실패: '+str(res.get('error') or res))

        # Recover an already-running server job after browser refresh/reconnect.
        if live and not st.session_state.get('brief_job_id'):
            active=api('/api/briefing/job-active/USA') or {}
            if active.get('active') and (active.get('job') or {}).get('job_id'):
                st.session_state['brief_job_id']=active['job']['job_id']

        job_id=st.session_state.get('brief_job_id')
        if live and job_id:
            js=api(f'/api/briefing/job/{job_id}',timeout=10) or {}
            status=js.get('status')
            stage=js.get('stage') or status or 'UNKNOWN'
            progress=int(js.get('progress') or 0)
            if status in ('QUEUED','RUNNING'):
                st.progress(max(0,min(100,progress)),text=f"브리핑 생성 중 · {stage} · {progress}% · {js.get('detail') or ''}")
                st.caption('브라우저 요청은 이미 종료되었습니다. 서버가 뉴스 검색/AI 분석/저장을 계속 수행합니다.')
                # Poll with short GET requests; no multi-minute POST connection is held.
                time.sleep(3)
                st.rerun()
            elif status=='COMPLETE':
                st.success(f"브리핑 저장 완료 · {js.get('trade_date')} · Report #{js.get('report_id')} · {js.get('elapsed_sec') or '?'}초")
                st.session_state.pop('brief_job_id',None)
                st.session_state.pop('brief_job_started',None)
                st.rerun()
            elif status=='FAILED':
                st.error('브리핑 생성 실패: '+str(js.get('error') or 'unknown error'))
                st.session_state.pop('brief_job_id',None)
                st.session_state.pop('brief_job_started',None)
    with c2:
        st.metric('자동 시각','09:00 ET')
    with c3:
        st.caption('한국장은 시장 데이터 어댑터를 만든 뒤 같은 PREOPEN_30 스케줄 구조로 연결합니다.')

    latest=api('/api/briefing/latest?market=USA') if live else None
    if latest:
        meta=latest.get('meta') or {}
        st.markdown(f"### 🇺🇸 {meta.get('trade_date','')} PRE-OPEN")
        extra=meta.get('extra') or {}
        data_mode=extra.get('market_data_mode') or 'UNKNOWN'
        data_as_of=extra.get('market_data_as_of') or 'N/A'
        m1,m2,m3,m4=st.columns(4)
        m1.metric('Market LONG',f"{float(meta.get('market_long_power') or 0):.0f}")
        m2.metric('Market SHORT',f"{float(meta.get('market_short_power') or 0):.0f}")
        if data_mode=='PREMARKET_LIVE':
            m3.metric('QQQ Premarket',f"{float(meta.get('qqq_pct') or 0):+.2f}%")
            m4.metric('SMH Premarket',f"{float(meta.get('smh_pct') or 0):+.2f}%")
            st.success(f'PREMARKET_LIVE · market data as of {data_as_of}')
        else:
            m3.metric('QQQ Premarket','N/A')
            m4.metric('SMH Premarket','N/A')
            st.warning(f'{data_mode} · 프리마켓 가중치 제외 · latest market bar {data_as_of}')
        if meta.get('report_text'):
            st.code(meta.get('report_text'),language=None)
        sources=(extra.get('news_sources') or [])
        if extra.get('news_ai_enabled'):
            if extra.get('news_ai_error'):
                st.warning('News AI 오류: '+str(extra.get('news_ai_error')))
            elif sources:
                with st.expander('📰 News AI 참고 소스',expanded=False):
                    for s0 in sources[:20]:
                        st.markdown(f"- [{s0.get('title') or 'source'}]({s0.get('url')})")
            else:
                st.caption('News AI 활성화됨 · 이번 응답에서 별도 URL citation 없음')
        else:
            st.info('News AI 비활성 · AWS .env에 OPENAI_API_KEY를 설정하면 V2.1 뉴스 Catalyst가 활성화됩니다.')
        r=latest.get('rows') or []
        if r:
            rdf=pd.DataFrame(r)
            keep=['symbol','current_rank','shadow_rank','current_score','shadow_score',
                  'data_mode','premarket_change_pct','premarket_volume_pct_avg_daily',
                  'long_power','short_power','catalyst_strength','catalyst_type','news_bias',
                  'ai_confidence','confidence_score','source_quality','event_recency','impact_horizon',
                  'price_reaction','news_weight_pct','news_delta_long',
                  'final_long_power','final_short_power','final_signal',
                  'news_headline_ko','news_why_now_ko','news_summary_ko','news_risk_ko',
                  'evidence_check','evidence_warning','news_conflict_ko',
                  'news_symbol_status','news_elapsed_sec','news_symbol_error',
                  'source_title','source_url','rationale']
            rdf=rdf[[c for c in keep if c in rdf.columns]].rename(columns={
                'symbol':'종목','current_rank':'CURRENT','shadow_rank':'SHADOW',
                'current_score':'Current Score','shadow_score':'Shadow Score',
                'data_mode':'Data Mode','premarket_change_pct':'PM %',
                'premarket_volume_pct_avg_daily':'PM거래량/5일평균일거래량%',
                'long_power':'Tech LONG','short_power':'Tech SHORT',
                'catalyst_strength':'Catalyst','catalyst_type':'Catalyst Type','news_bias':'News Bias',
                'ai_confidence':'AI Confidence','confidence_score':'AI 신뢰점수',
                'source_quality':'Source Quality','event_recency':'뉴스시점','impact_horizon':'영향기간',
                'price_reaction':'Price Reaction','news_weight_pct':'News 가중치%','news_delta_long':'News ΔLONG',
                'final_long_power':'FINAL LONG','final_short_power':'FINAL SHORT',
                'final_signal':'최종판단','news_headline_ko':'뉴스 헤드라인',
                'news_why_now_ko':'왜 지금 중요한가','news_summary_ko':'AI 뉴스판단',
                'news_risk_ko':'뉴스 리스크','evidence_check':'Evidence','evidence_warning':'근거 경고',
                'news_conflict_ko':'상충 뉴스','news_symbol_status':'News 상태',
                'news_elapsed_sec':'News 소요초','news_symbol_error':'News 오류',
                'source_title':'대표 출처','source_url':'대표 URL','rationale':'기술근거'
            })
            st.dataframe(rdf,use_container_width=True,hide_index=True)

            # V2.5a: evidence audit summary
            audit_rows=[]
            # Use the enriched PREOPEN report rows, not the raw screener rows.
            evidence_rows = latest.get('rows') or r
            for x in evidence_rows[:5]:
                audit_rows.append({
                    '종목':x.get('symbol'),
                    '상태':x.get('news_symbol_status') or 'N/A',
                    '소요초':x.get('news_elapsed_sec'),
                    'Catalyst':x.get('catalyst_strength'),
                    'Type':x.get('catalyst_type'),
                    'Evidence':x.get('evidence_check'),
                    'Source':x.get('source_quality'),
                    'AI신뢰':x.get('confidence_score'),
                    'URL':bool(x.get('source_url')),
                    '경고':x.get('evidence_warning') or x.get('news_symbol_error') or ''
                })
            if audit_rows:
                with st.expander('🔎 TOP5 Evidence Audit · 출처/분류 일치성',expanded=False):
                    st.dataframe(pd.DataFrame(audit_rows),use_container_width=True,hide_index=True)
                    failed_syms=[x.get('종목') for x in audit_rows if x.get('상태')=='ERROR']
                    if failed_syms:
                        st.warning('News AI 실패 종목: '+', '.join(failed_syms))
                        if st.button('실패 종목 뉴스 다시 시도',key='retry_failed_news'):
                            rr=api_post('/api/briefing/retry-failed?market=USA') or {}
                            if rr.get('ok') and rr.get('job_id'):
                                st.session_state['brief_job_id']=rr.get('job_id')
                                st.rerun()
                            else:
                                st.error('재시도 시작 실패: '+str(rr))

            # V2.2: human-readable catalyst audit trail. This is research/briefing information only.
            material=[x for x in r[:5] if x.get('catalyst_strength') not in (None,'','NONE')]
            if material:
                with st.expander('🧭 TOP5 Catalyst 상세 · 유형 / 신뢰도 / 출처 / 점수 영향',expanded=True):
                    for x in material:
                        st.markdown(
                            f"**{x.get('symbol')} · {x.get('catalyst_strength','N/A')} / "
                            f"{x.get('catalyst_type','N/A')} · {x.get('news_bias','N/A')}**"
                        )
                        st.caption(
                            f"AI 신뢰 {x.get('confidence_score','N/A')}/100 ({x.get('ai_confidence','N/A')}) · "
                            f"출처 {x.get('source_quality','N/A')} · Evidence {x.get('evidence_check','N/A')} · "
                            f"시점 {x.get('event_recency','N/A')} · 영향 {x.get('impact_horizon','N/A')} · "
                            f"News 가중치 {x.get('news_weight_pct',0)}% · "
                            f"FINAL LONG 영향 {float(x.get('news_delta_long') or 0):+.1f}p"
                        )
                        if x.get('evidence_warning'):
                            st.warning('근거 검증: '+str(x.get('evidence_warning')))
                        if x.get('news_conflict_ko'):
                            st.info('상충 뉴스: '+str(x.get('news_conflict_ko')))
                        if x.get('news_headline_ko'):
                            st.write('**핵심:** '+str(x.get('news_headline_ko')))
                        if x.get('news_why_now_ko'):
                            st.write('**왜 지금:** '+str(x.get('news_why_now_ko')))
                        if x.get('news_risk_ko'):
                            st.write('**리스크:** '+str(x.get('news_risk_ko')))
                        if x.get('source_url'):
                            st.markdown(f"[대표 출처: {x.get('source_title') or 'source'}]({x.get('source_url')})")
                        st.divider()
    else:
        st.info('아직 저장된 PREOPEN 리포트가 없습니다. 수동 생성으로 먼저 테스트할 수 있습니다.')

    hist=(api('/api/briefing/history?market=USA&limit=30') or {}).get('data',[]) if live else []
    if hist:
        with st.expander('저장된 장전 브리핑 History',expanded=False):
            st.dataframe(pd.DataFrame(hist),use_container_width=True,hide_index=True)

with tab_research:
    st.caption('과거 시뮬레이션 · Weight Study · Walk-forward · Regime · Relative Strength 연구')
    if live:
        st.divider()
        st.subheader('Historical Validation Lab · OPEN_V0')
        st.caption('과거 각 거래일의 전일까지 데이터 + 당일 시가만으로 순위를 만든 뒤 장마감 결과와 비교합니다. 당일 고가·저가·종가·거래량은 예측 점수에 사용하지 않습니다.')
        vc1,vc2,vc3=st.columns([1,1,2])
        with vc1: vdays=st.selectbox('검증 거래일',[20,40,60,90,120,180,240,250],index=4)
        with vc2: vsymbols=st.selectbox('검증 종목 수',[12,16,20,24,28,32],index=2)
        with vc3:
            st.write(''); st.write('')
            run_v=st.button('과거 시뮬레이션 실행',type='primary')

        if run_v:
            with st.spinner('키움 과거 일봉 조회 및 OPEN_V0 순위 검증 중...'):
                vr=api(f'/api/validation/run?days={vdays}&max_symbols={vsymbols}',timeout=240) or {}
            if vr.get('summary'): st.session_state['validation_result']=vr

        vr=st.session_state.get('validation_result')
        if not vr:
            runs=(api('/api/validation/runs?limit=1') or {}).get('data',[])
            if runs: vr=api(f"/api/validation/result/{runs[0]['id']}",timeout=20)

        if vr and vr.get('summary'):
            sm=vr['summary']; rho=sm.get('mean_spearman')
            c1,c2,c3,c4=st.columns(4)
            c1.metric('검증일수',sm.get('days_validated','-'))
            c2.metric('평균 Rank Corr','-' if rho is None else f"{float(rho):+.3f}")
            c3.metric('TOP5 시장초과',f"{float(sm.get('top5_excess_avg') or 0):+.2f}%")
            c4.metric('TOP5 초과수익 적중',f"{float(sm.get('top5_positive_excess_rate') or 0):.1f}%")
            q1,q2,q3,q4=st.columns(4)
            q1.metric('과거데이터 성공',sm.get('symbols_loaded','-'))
            q2.metric('과거데이터 실패',sm.get('symbols_failed','-'))
            q3.metric('평균 Universe',f"{float(sm.get('avg_universe') or 0):.1f}")
            q4.metric('Precision@5',f"{float(sm.get('precision_at_5') or 0):.1f}%")

            d1,d2,d3,d4=st.columns(4)
            d1.metric('요청 거래일',sm.get('requested_sessions',sm.get('days_requested','-')))
            d2.metric('후보 거래일 확보',sm.get('candidate_sessions_loaded','-'))
            d3.metric('실제 검증 거래일',sm.get('validated_sessions',sm.get('days_validated','-')))
            d4.metric('UNKNOWN Regime',sm.get('unknown_regime_days','-'))
            if sm.get('history_start') and sm.get('history_end'):
                st.caption(f"Historical Range: {sm.get('history_start')} → {sm.get('history_end')}")
            if sm.get('failed_symbols'):
                with st.expander('과거데이터 조회 실패 종목/원인',expanded=False):
                    st.json(sm.get('failed_symbols'))
            if sm.get('load_meta'):
                with st.expander('Historical API 페이지/행수 확인',expanded=False):
                    lm=[]
                    for sym,x in (sm.get('load_meta') or {}).items():
                        lm.append({'종목':sym,'거래소':x.get('exchange'),'페이지':x.get('pages'),
                                   '원시행':x.get('raw_rows'),'사용가능 일봉':x.get('usable_rows'),
                                   '첫 일자':x.get('first_date'),'마지막 일자':x.get('last_date')})
                    st.dataframe(pd.DataFrame(lm),use_container_width=True,hide_index=True)

            if sm.get('group_summary'):
                st.caption('자산군별 검증 성과')
                gs=[]
                for g,x in (sm.get('group_summary') or {}).items():
                    gs.append({'그룹':g,'표본수':x.get('n'),'평균 Score':x.get('avg_score'),
                               '평균 시장초과%':x.get('avg_excess'),'초과수익 적중%':x.get('positive_excess_rate')})
                st.dataframe(pd.DataFrame(gs),use_container_width=True,hide_index=True)

            cd=sm.get('component_diagnostics') or {}
            if cd:
                st.caption('TOP5 성공군 vs False Positive · 평균 점수 구성 비교')
                keys=sorted(set((cd.get('true_positive_avg_parts') or {}).keys()) | set((cd.get('false_positive_avg_parts') or {}).keys()))
                comp=[]
                for k in keys:
                    comp.append({
                        '지표':k,
                        '성공군 평균점수':(cd.get('true_positive_avg_parts') or {}).get(k,0),
                        'False Positive 평균점수':(cd.get('false_positive_avg_parts') or {}).get(k,0)
                    })
                if comp:
                    st.dataframe(pd.DataFrame(comp),use_container_width=True,hide_index=True)
                st.caption(f"성공 TOP5 표본 {cd.get('true_positive_count',0)}건 · False Positive 표본 {cd.get('false_positive_count',0)}건")

            if sm.get('model_study'):
                st.subheader('V1.6 Weight Study · 운영 가중치는 아직 변경하지 않음')
                ms=[]
                for name,x in (sm.get('model_study') or {}).items():
                    wf=x.get('walk_forward') or {}
                    test=(wf.get('test') or {}) if wf.get('available') else {}
                    ms.append({
                        '모델':name,'전체 Rank Corr':x.get('rank_corr'),
                        '전체 Precision@5':x.get('precision_at_5'),
                        '전체 TOP5 초과%':x.get('top5_excess_avg'),
                        'WF Test Rank Corr':test.get('rank_corr'),
                        'WF Test Precision@5':test.get('precision_at_5'),
                        'WF Test TOP5 초과%':test.get('top5_excess_avg')
                    })
                st.dataframe(pd.DataFrame(ms),use_container_width=True,hide_index=True)
                st.caption('WF = 앞 80거래일과 분리된 뒤 40거래일 테스트. 후보모델이 전체기간뿐 아니라 미사용 테스트구간에서도 개선되는지 확인합니다.')

            if sm.get('paired_fold_study'):
                st.subheader('Paired Fold Comparison · 같은 OOS 구간에서 직접 대결')
                pf=[]
                for name,x in (sm.get('paired_fold_study') or {}).items():
                    pf.append({
                        '후보모델':name,'Fold':x.get('folds'),
                        '승':x.get('wins'),'패':x.get('losses'),'무':x.get('ties'),
                        '승률%':x.get('win_rate'),
                        '평균 개선 TOP5%':x.get('avg_delta_top5_excess'),
                        '중앙 개선 TOP5%':x.get('median_delta_top5_excess'),
                        '최악 상대차%':x.get('worst_delta_top5_excess'),
                        '95% CI Low':x.get('bootstrap_ci95_low'),
                        '95% CI High':x.get('bootstrap_ci95_high'),
                        '평균 Rank Corr 개선':x.get('avg_delta_rank_corr'),
                        '평균 Precision@5 개선':x.get('avg_delta_precision_at_5'),
                        'Evidence':x.get('evidence')
                    })
                st.dataframe(pd.DataFrame(pf),use_container_width=True,hide_index=True)
                st.caption('개선값 = 후보모델 - GLOBAL_CURRENT. Bootstrap 95% CI가 0 위에 있으면 Fold 평균 개선에 대한 근거가 더 강합니다.')
                with st.expander('Paired Fold 상세',expanded=False):
                    choice=st.selectbox('후보모델 선택',list((sm.get('paired_fold_study') or {}).keys()),key='paired_model')
                    pairs=((sm.get('paired_fold_study') or {}).get(choice) or {}).get('pairs') or []
                    if pairs:
                        st.dataframe(pd.DataFrame(pairs),use_container_width=True,hide_index=True)

            if sm.get('rs_paired_study'):
                st.subheader('RS Paired Study · RS_OFF와 같은 Fold에서 비교')
                rp=[]
                for name,x in (sm.get('rs_paired_study') or {}).items():
                    rp.append({
                        'RS 모델':name,'Fold':x.get('folds'),'승률%':x.get('win_rate'),
                        '평균 개선 TOP5%':x.get('avg_delta_top5_excess'),
                        '중앙 개선 TOP5%':x.get('median_delta_top5_excess'),
                        '최악 상대차%':x.get('worst_delta_top5_excess'),
                        '95% CI Low':x.get('bootstrap_ci95_low'),
                        '95% CI High':x.get('bootstrap_ci95_high'),
                        'Rank Corr 개선':x.get('avg_delta_rank_corr'),
                        'Precision@5 개선':x.get('avg_delta_precision_at_5'),
                        'Evidence':x.get('evidence')
                    })
                st.dataframe(pd.DataFrame(rp),use_container_width=True,hide_index=True)

            if sm.get('stability_ranking'):
                st.subheader('Robustness Ranking · 평균만 좋고 흔들리는 모델은 감점')
                stab=pd.DataFrame(sm.get('stability_ranking') or [])
                if not stab.empty:
                    stab=stab.rename(columns={
                        'model':'모델','stability_score':'안정성 Score',
                        'avg_oos_top5_excess':'평균 OOS TOP5%',
                        'std_oos_top5_excess':'TOP5 표준편차',
                        'worst_oos_top5_excess':'최악 Fold TOP5%',
                        'positive_fold_rate':'플러스 Fold%',
                        'avg_oos_rank_corr':'평균 OOS Rank Corr',
                        'avg_oos_precision_at_5':'평균 OOS Precision@5'
                    })
                    st.dataframe(stab,use_container_width=True,hide_index=True)
                st.caption('안정성 Score는 연구용 비교지표이며 운영 가중치를 자동 선택하거나 변경하지 않습니다.')

            if sm.get('rolling_walk_forward'):
                st.subheader('Rolling Walk-forward · 40일 → 다음 20일')
                rw=[]
                for name,x in (sm.get('rolling_walk_forward') or {}).items():
                    rw.append({
                        '모델':name,'Fold 수':x.get('fold_count'),
                        '평균 OOS Rank Corr':x.get('avg_test_rank_corr'),
                        '평균 OOS Precision@5':x.get('avg_test_precision_at_5'),
                        '평균 OOS TOP5 초과%':x.get('avg_test_top5_excess_avg'),
                        '최악 Fold TOP5%':x.get('worst_top5_excess'),
                        'TOP5 표준편차':x.get('std_top5_excess'),
                        'TOP5 중앙값':x.get('median_top5_excess'),
                        'TOP5 플러스 Fold%':x.get('positive_fold_rate')
                    })
                st.dataframe(pd.DataFrame(rw),use_container_width=True,hide_index=True)
                with st.expander('Rolling Fold 상세',expanded=False):
                    choice=st.selectbox('모델 선택',list((sm.get('rolling_walk_forward') or {}).keys()),key='rw_model')
                    folds=((sm.get('rolling_walk_forward') or {}).get(choice) or {}).get('folds') or []
                    if folds:
                        st.dataframe(pd.DataFrame(folds),use_container_width=True,hide_index=True)

            if sm.get('rs_sensitivity'):
                st.subheader('Relative Strength Sensitivity')
                rs_rows=[]
                for name,x in (sm.get('rs_sensitivity') or {}).items():
                    rs_rows.append({
                        'RS 강도':name,
                        '전체 Rank Corr':x.get('rank_corr'),
                        '전체 Precision@5':x.get('precision_at_5'),
                        '전체 TOP5 초과%':x.get('top5_excess_avg'),
                        'Rolling OOS Rank Corr':x.get('rolling_avg_test_rank_corr'),
                        'Rolling OOS Precision@5':x.get('rolling_avg_test_precision_at_5'),
                        'Rolling OOS TOP5 초과%':x.get('rolling_avg_test_top5_excess_avg'),
                        'OOS 플러스 Fold%':x.get('positive_top5_fold_rate')
                    })
                st.dataframe(pd.DataFrame(rs_rows),use_container_width=True,hide_index=True)

            if sm.get('regime_oos'):
                st.subheader('Regime별 Out-of-sample')
                ro=[]
                for rg,models in (sm.get('regime_oos') or {}).items():
                    for name,x in (models or {}).items():
                        ro.append({
                            'Regime':rg,'모델':name,'유효 Fold':x.get('fold_count'),
                            'OOS Rank Corr':x.get('avg_test_rank_corr'),
                            'OOS Precision@5':x.get('avg_test_precision_at_5'),
                            'OOS TOP5 초과%':x.get('avg_test_top5_excess_avg'),
                            '최악 Fold TOP5%':x.get('worst_top5_excess'),
                            'TOP5 표준편차':x.get('std_top5_excess'),
                            '플러스 Fold%':x.get('positive_fold_rate')
                        })
                st.dataframe(pd.DataFrame(ro),use_container_width=True,hide_index=True)

            if sm.get('regime_model_study'):
                st.caption('Regime별 Current vs Candidate 비교')
                rms=[]
                for rg,models in (sm.get('regime_model_study') or {}).items():
                    for name,x in (models or {}).items():
                        rms.append({'Regime':rg,'모델':name,'거래일':x.get('days'),
                                    'Rank Corr':x.get('rank_corr'),'Precision@5':x.get('precision_at_5'),
                                    'TOP5 초과%':x.get('top5_excess_avg')})
                st.dataframe(pd.DataFrame(rms),use_container_width=True,hide_index=True)

            if sm.get('time_window_summary'):
                st.caption('시간 구간별 안정성 · 같은 가중치가 최근/과거에도 유지되는가')
                tw=pd.DataFrame(sm.get('time_window_summary') or [])
                if not tw.empty:
                    tw=tw.rename(columns={
                        'window':'구간','start_date':'시작','end_date':'종료','days':'거래일',
                        'rank_corr':'Rank Corr','precision_at_5':'Precision@5',
                        'top5_excess_avg':'TOP5 시장초과%'
                    })
                    st.dataframe(tw,use_container_width=True,hide_index=True)

            if sm.get('regime_summary'):
                st.caption('시장 Regime별 검증 · 현재 가중치를 그대로 적용')
                rg=[]
                for name,x in (sm.get('regime_summary') or {}).items():
                    rg.append({'시장상태':name,'거래일':x.get('days'),'표본':x.get('n'),
                               'Rank Corr':x.get('rank_corr'),'Precision@5':x.get('precision_at_5'),
                               'TOP5 시장초과%':x.get('top5_excess_avg')})
                st.dataframe(pd.DataFrame(rg),use_container_width=True,hide_index=True)

            if sm.get('semi_regime_summary'):
                st.caption('반도체 Regime별 검증')
                sr=[]
                for name,x in (sm.get('semi_regime_summary') or {}).items():
                    sr.append({'반도체상태':name,'거래일':x.get('days'),'표본':x.get('n'),
                               'Rank Corr':x.get('rank_corr'),'Precision@5':x.get('precision_at_5'),
                               'TOP5 시장초과%':x.get('top5_excess_avg')})
                st.dataframe(pd.DataFrame(sr),use_container_width=True,hide_index=True)

            rcd=sm.get('regime_component_diagnostics') or {}
            if rcd:
                with st.expander('Regime별 성공군 vs False Positive 지표 비교',expanded=False):
                    pick=st.selectbox('시장 상태',['BULL','BEAR','MIXED'],key='regime_diag')
                    x=rcd.get(pick) or {}
                    keys=sorted(set((x.get('true_positive_avg_parts') or {}).keys()) | set((x.get('false_positive_avg_parts') or {}).keys()))
                    rows_reg=[]
                    for k in keys:
                        rows_reg.append({
                            '지표':k,
                            '성공군 평균점수':(x.get('true_positive_avg_parts') or {}).get(k,0),
                            'False Positive 평균점수':(x.get('false_positive_avg_parts') or {}).get(k,0)
                        })
                    if rows_reg:
                        st.dataframe(pd.DataFrame(rows_reg),use_container_width=True,hide_index=True)
                    st.caption(f"성공군 {x.get('true_positive_count',0)}건 · False Positive {x.get('false_positive_count',0)}건")

            st.caption(sm.get('note',''))

            daily=pd.DataFrame(vr.get('daily') or [])
            if not daily.empty:
                st.caption('최근 20 거래일 검증 결과')
                dshow=daily.tail(20).copy()
                if 'group_stats' in dshow.columns:
                    dshow=dshow.drop(columns=['group_stats'])
                st.dataframe(dshow,use_container_width=True,hide_index=True)

            rowsv=pd.DataFrame(vr.get('rows') or [])
            if not rowsv.empty:
                last=sorted(rowsv['trade_date'].dropna().unique())[-1]
                st.caption(f'{last} 예상순위 vs 실제 시장초과수익 순위')
                rr=rowsv[rowsv['trade_date']==last]
                cols=['pred_rank','actual_rank','symbol','asset_group','market_regime','semi_regime',
                      'validation_tag','score','relative_strength_pct','relative_strength_points',
                      'gap_pct','effective_gap_pct','open_to_close_pct','mfe_pct','mae_pct','excess_pct']
                st.dataframe(rr[[c for c in cols if c in rr.columns]].head(20),use_container_width=True,hide_index=True)



with tab_archive:
    st.subheader('Daily Ranking Archive')
    st.caption('CURRENT / SHADOW별 TOP10과 T-10 / T-1 / T+7 / T+30 / T+60 / CLOSE / MANUAL_SCAN 스냅샷을 조회합니다.')
    if live:
        with st.expander('📚 저장된 일자별 순위 Archive', expanded=False):
            adates=(api('/api/archive/dates?limit=120') or {}).get('data',[])
            if not adates:
                st.info('아직 Archive에 저장된 순위가 없습니다. 다음 미국장부터 T-10 / T-1 / T+7 / T+30 / T+60 / CLOSE 순위가 자동 저장됩니다.')
                if st.button('현재 TOP10을 MANUAL로 저장',key='archive_manual_empty'):
                    x=api('/api/archive/save-now?label=MANUAL') or {}
                    if x.get('ok'): st.success(f"{x.get('trade_date')} MANUAL 저장 완료")
            else:
                adf=pd.DataFrame(adates)
                st.dataframe(adf.rename(columns={
                    'trade_date':'일자','snapshots':'스냅샷 수','first_capture':'첫 저장','last_capture':'마지막 저장'
                }),use_container_width=True,hide_index=True)

                date_options=[x.get('trade_date') for x in adates if x.get('trade_date')]
                ac1,ac2=st.columns([2,1])
                with ac1:
                    archive_date=st.selectbox('조회 일자',date_options,key='archive_date')
                with ac2:
                    st.write(''); st.write('')
                    if st.button('현재 TOP10 MANUAL 저장',key='archive_manual'):
                        x=api('/api/archive/save-now?label=MANUAL') or {}
                        if x.get('ok'): st.success(f"{x.get('trade_date')} MANUAL 저장 완료")

                snaps=(api(f'/api/archive/snapshots?trade_date={archive_date}') or {}).get('data',[])
                if snaps:
                    sdf=pd.DataFrame(snaps)
                    st.caption('해당 일자의 저장 시점')
                    st.dataframe(sdf.rename(columns={
                        'trade_date':'일자','label':'시점','model':'모델','captured_at':'저장시각',
                        'row_count':'종목수','qqq_pct':'QQQ%','smh_pct':'SMH%'
                    }),use_container_width=True,hide_index=True)

                    labels=[]
                    for x in snaps:
                        key=f"{x.get('label')} | {x.get('model')}"
                        if key not in labels: labels.append(key)
                    pick=st.selectbox('순위 스냅샷',labels,key='archive_snapshot')
                    plabel,pmodel=[x.strip() for x in pick.split('|',1)]
                    ar=api(f'/api/archive/ranking?trade_date={archive_date}&label={plabel}&model={pmodel}') or {}
                    rows_archive=ar.get('rows') or []
                    if rows_archive:
                        rdf=pd.DataFrame(rows_archive)
                        keep=['rank','symbol','score','bias','price','change_pct','ma5','ma5_slope_pct',
                              'rvol','atr_pct','dollar_volume','exchange']
                        rdf=rdf[[c for c in keep if c in rdf.columns]].rename(columns={
                            'rank':'순위','symbol':'종목','score':'점수','bias':'방향','price':'현재가',
                            'change_pct':'당일%','ma5':'MA5','ma5_slope_pct':'MA5기울기%',
                            'rvol':'RVOL','atr_pct':'ATR%','dollar_volume':'거래대금','exchange':'거래소'
                        })
                        st.caption(f"{archive_date} · {plabel} · {pmodel} 저장 순위")
                        st.dataframe(rdf,use_container_width=True,hide_index=True)


with tab_live:
    st.caption('실제 장중에 저장된 TOP10 스냅샷의 +30분 / +60분 / 현재·마감 성과를 추적합니다.')
    if live:
        st.divider()
        st.subheader('Live TOP10 Validation')
        st.caption('메인 TOP10 스냅샷(T-10 / T-1 / T+7 / T+30 / T+60)을 저장하고 이후 +30분, +60분, 현재/마감까지의 성과를 비교합니다.')
        lv=api('/api/validation/live',timeout=20) or {}
        if lv.get('snapshots'):
            summary_rows=[]
            for x in lv['snapshots']:
                summary_rows.append({
                    '시점':x.get('label'),'저장종목':x.get('captured'),'평가종목':x.get('evaluated'),
                    'TOP5 +30m%':x.get('top5_30m'),'TOP5 +60m%':x.get('top5_60m'),
                    'TOP5 현재/마감%':x.get('top5_to_last')
                })
            sdf=pd.DataFrame(summary_rows)
            for c in ['TOP5 +30m%','TOP5 +60m%','TOP5 현재/마감%']:
                if c in sdf.columns: sdf[c]=pd.to_numeric(sdf[c],errors='coerce').round(3)
            st.dataframe(sdf,use_container_width=True,hide_index=True)
            with st.expander('Live TOP10 종목별 검증 상세',expanded=False):
                detail=[]
                for x in lv['snapshots']:
                    for r in x.get('rows') or []:
                        detail.append({'시점':x.get('label'),**r})
                if detail:
                    st.dataframe(pd.DataFrame(detail),use_container_width=True,hide_index=True)
        else:
            st.info('아직 저장된 TOP10 스냅샷이 없습니다. 다음 미국장부터 자동으로 누적됩니다.')


st.caption('V2.2: 프로젝트 .env가 systemd 환경보다 우선하며 /health에 News AI 설정 여부만 안전하게 표시합니다. NO AUTO ORDER.')
