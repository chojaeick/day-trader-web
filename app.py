import os, requests, pandas as pd
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
st.markdown('''<style>.block-container{padding-top:1rem;max-width:1550px}.hero{display:flex;justify-content:space-between;align-items:center;padding:18px 22px;border:1px solid rgba(128,128,128,.2);border-radius:18px;margin-bottom:14px}.hero h1{margin:0;font-size:1.9rem}.badge{display:inline-block;padding:6px 10px;border-radius:999px;background:rgba(128,128,128,.13);font-weight:700;font-size:.8rem;margin-right:6px}.signal{padding:16px 18px;border-radius:16px;border:1px solid rgba(128,128,128,.2);font-size:1.03rem;margin:8px 0 14px}</style>''',unsafe_allow_html=True)

def api(path):
    if not API_URL: return None
    try:
        r=requests.get(API_URL+path,timeout=8); r.raise_for_status(); return r.json()
    except Exception as e:
        st.sidebar.warning(f'LIVE API 연결 대기: {e}'); return None

health=api('/health') if API_URL else None; live=bool(health and health.get('ok')); mode='LIVE DATA' if live else 'DEMO DATA'
st.markdown(f'''<div class="hero"><div><h1>DAY TRADER WEB</h1><div>TOP10 → 1·5분봉 Signal → Position → Critical Alert</div></div><div><span class="badge">{mode}</span><span class="badge">NO AUTO ORDER</span></div></div>''',unsafe_allow_html=True)

c1,c2,c3,c4=st.columns(4); c1.metric('NASDAQ Bias','BULL 74'); c2.metric('Semiconductor','STRONG'); c3.metric('Market Mode','TREND'); c4.metric('Data','LIVE' if live else 'DEMO')

st.subheader('오늘의 단타 후보 TOP 10')
if live:
    payload=api('/api/screener?top_n=10') or {'data':[]}; rows=payload.get('data',[])
    if rows:
        show=pd.DataFrame(rows)
        show.insert(0,'순위',range(1,len(show)+1))
        cols=['순위','symbol','score','bias','price','change_pct','ma5','ma5_slope_pct','rvol','atr_pct','dollar_volume','exchange']
        show=show[[c for c in cols if c in show.columns]].rename(columns={'symbol':'종목','score':'점수','bias':'방향','price':'현재가','change_pct':'당일%','ma5':'MA5','ma5_slope_pct':'MA5기울기%','rvol':'RVOL','atr_pct':'ATR%','dollar_volume':'거래대금','exchange':'거래소'})
        st.dataframe(show,use_container_width=True,hide_index=True); symbols=[r['symbol'] for r in rows]
    else:
        st.info('5일 일봉 지표 초기 수집 중입니다. 잠시 후 자동으로 TOP10이 계산됩니다.'); symbols=[q['symbol'] for q in (api('/api/quotes') or [])]
else:
    ranked=rank_candidates(demo_candidates(),cfg).copy(); ranked.insert(0,'rank',range(1,len(ranked)+1)); st.dataframe(ranked,use_container_width=True,hide_index=True); symbols=ranked['symbol'].tolist()

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
        q=api(f'/api/quote/{selected}') or {}; sig=api(f'/api/signal/{selected}') or {}; b1=api(f'/api/bars/{selected}?minutes=1&limit=200') or {'data':[]}; b5=api(f'/api/bars/{selected}?minutes=5&limit=100') or {'data':[]}
        m1,m2,m3,m4,m5=st.columns(5); m1.metric('상태',sig.get('state','WARMING')); m2.metric('Signal',f"{sig.get('score',0)}/100"); m3.metric('1M',f"{sig.get('score_1m',0)}/100"); m4.metric('5M Confirm',sig.get('confirm_5m',0)); m5.metric('현재가',f"${float(q.get('price') or 0):,.2f}")
        st.markdown(f'''<div class="signal"><b>{selected} · {sig.get('state','WARMING')}</b><br>{sig.get('reason','데이터 수집 중')}<br><small>무효화 {sig.get('invalidation','-')} · T1 {sig.get('target1','-')} · T2 {sig.get('target2','-')}</small></div>''',unsafe_allow_html=True)
        l,r=st.columns(2)
        with l:
            df=pd.DataFrame(b1['data']); st.caption('1분봉 LIVE');
            if not df.empty: st.line_chart(df.set_index('time')['close'],height=280)
        with r:
            df5=pd.DataFrame(b5['data']); st.caption('5분봉 LIVE');
            if not df5.empty: st.line_chart(df5.set_index('time')['close'],height=280)
        st.subheader('포지션 관리')
        entry=st.number_input('실제 진입가격 (카카오페이 체결가)',min_value=0.0,value=0.0,step=0.01,format='%.4f')
        if entry>0:
            pos=api(f'/api/position/{selected}?entry={entry}') or {}
            p1,p2,p3=st.columns(3); p1.metric('Position',pos.get('state','-')); p2.metric('수익률',f"{float(pos.get('pnl_pct') or 0):+.2f}%"); p3.metric('현재가',f"${float(pos.get('price') or 0):,.2f}")
            st.info(pos.get('reason',''))
            if pos.get('critical'): st.error('중요 신호: EXIT/리스크 이벤트')
    else:
        bars=demo_bars(selected); sig=intraday_signal(selected,bars,market_bias=.7,cfg=cfg); st.metric('상태',sig.state); st.line_chart(bars.set_index('time')['close'],height=300)

st.caption('V1.2: 5일 일봉 평균/기울기 + 거래대금 + 시간보정 RVOL + ATR + 1M/5M 결합 Signal. 현재는 고유동성 후보 유니버스에서 TOP10을 뽑으며, 전 종목 조건검색 확장은 다음 단계입니다.')
