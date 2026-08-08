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

def api(path):
    if not API_URL: return None
    try:
        r=requests.get(API_URL+path,timeout=8); r.raise_for_status(); return r.json()
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
version=(health or {}).get('version','1.4.3') if live else '1.4.3'
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
                keep=['origin','symbol','name','asset_type','exchange','price','change_pct',
                      'volume_rank','dollar_rank','gainer_rank','loser_rank','surge_rank',
                      'surge_pct','discovery_score','chase_risk','sources']
                udf=udf[[c for c in keep if c in udf.columns]].rename(columns={
                    'origin':'구분','symbol':'종목','name':'이름','asset_type':'유형','exchange':'거래소',
                    'price':'현재가','change_pct':'당일%','volume_rank':'거래량순위',
                    'dollar_rank':'거래대금순위','gainer_rank':'상승률순위','loser_rank':'하락률순위',
                    'surge_rank':'거래량급증순위','surge_pct':'급증률','discovery_score':'발굴점수',
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
        show=show[[c for c in cols if c in show.columns]].rename(columns={'symbol':'종목','score':'점수','bias':'방향','price':'현재가','change_pct':'당일%','ma5':'MA5','ma5_slope_pct':'MA5기울기%','rvol':'RVOL','atr_pct':'ATR%','dollar_volume':'거래대금','exchange':'거래소'})
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

st.caption('V1.4.3: 자동 Universe 품질 게이트 + ±30% EXTREME 분리 + ETF/레버리지 구분 + 최종 TOP10 유동성/추격위험 강화.')
