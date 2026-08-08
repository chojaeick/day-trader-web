import os, requests, pandas as pd
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
version=(health or {}).get('version','1.5B.2') if live else '1.5B.2'
st.markdown(f'''<div class="hero"><div><h1>DAY TRADER WEB</h1><div>TOP10 → 1·5분봉 Signal → Position → Critical Alert</div></div><div><span class="badge">{mode}</span><span class="badge">NO AUTO ORDER</span><span class="badge">v{version}</span></div></div>''',unsafe_allow_html=True)

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


if live:
    st.divider()
    st.subheader('Historical Validation Lab · OPEN_V0')
    st.caption('과거 각 거래일의 전일까지 데이터 + 당일 시가만으로 순위를 만든 뒤 장마감 결과와 비교합니다. 당일 고가·저가·종가·거래량은 예측 점수에 사용하지 않습니다.')
    vc1,vc2,vc3=st.columns([1,1,2])
    with vc1: vdays=st.selectbox('검증 거래일',[20,40,60,90,120],index=2)
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
                  'validation_tag','score','gap_pct','effective_gap_pct',
                  'open_to_close_pct','mfe_pct','mae_pct','excess_pct']
            st.dataframe(rr[[c for c in cols if c in rr.columns]].head(20),use_container_width=True,hide_index=True)


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

st.caption('V1.5B.2: usa06012 최신일자 anchor + 연속조회 pagination. 최근 120거래일을 정확히 확보한 뒤 Regime/가중치 연구에 사용합니다.')
