import os, requests, pandas as pd
import streamlit as st
from dotenv import load_dotenv
from trader.config import TradingConfig
from trader.demo import demo_candidates, demo_bars
from trader.screener import rank_candidates
from trader.signals import intraday_signal, position_signal

load_dotenv(); cfg=TradingConfig()
try:
    API_URL=st.secrets.get('DAYTRADER_API_URL','')
except Exception:
    API_URL=os.getenv('DAYTRADER_API_URL','')
API_URL=str(API_URL).rstrip('/')

st.set_page_config(page_title='DAY TRADER WEB',page_icon='📈',layout='wide',initial_sidebar_state='collapsed')
st.markdown('''<style>.block-container{padding-top:1.2rem;max-width:1500px}.hero{display:flex;justify-content:space-between;align-items:center;padding:18px 22px;border:1px solid rgba(128,128,128,.2);border-radius:18px;margin-bottom:14px}.hero h1{margin:0;font-size:1.9rem}.badge{display:inline-block;padding:6px 10px;border-radius:999px;background:rgba(128,128,128,.13);font-weight:700;font-size:.8rem;margin-right:6px}.signal{padding:16px 18px;border-radius:16px;border:1px solid rgba(128,128,128,.2);font-size:1.05rem;margin:8px 0 14px}</style>''',unsafe_allow_html=True)

def api(path):
    if not API_URL: return None
    try:
        r=requests.get(API_URL+path,timeout=5); r.raise_for_status(); return r.json()
    except Exception as e:
        st.sidebar.warning(f'LIVE API 연결 대기: {e}'); return None

health=api('/health') if API_URL else None
live=bool(health and health.get('ok'))
mode='LIVE DATA' if live else 'DEMO DATA'
st.markdown(f'''<div class="hero"><div><h1>DAY TRADER WEB</h1><div>TOP10 → 1·5분봉 Signal → Position → Critical Alert</div></div><div><span class="badge">{mode}</span><span class="badge">NO AUTO ORDER</span></div></div>''',unsafe_allow_html=True)

c1,c2,c3,c4=st.columns(4); c1.metric('NASDAQ Bias','BULL 74'); c2.metric('Semiconductor','STRONG'); c3.metric('Market Mode','TREND'); c4.metric('Data','LIVE' if live else 'DEMO')

st.subheader('오늘의 단타 후보 TOP 10')
if live:
    quotes=api('/api/quotes') or []
    rows=[]
    for q in quotes:
        # V1.1 live shortlist score; full-market TOP10 engine comes next.
        sc=50 + (15 if (q.get('change_pct') or 0)>0 else 0) + (10 if (q.get('volume') or 0)>1_000_000 else 0)
        rows.append({'종목':q['symbol'],'점수':min(sc,100),'방향':'LONG' if (q.get('change_pct') or 0)>0 else 'NEUTRAL','현재가':q.get('price'),'당일%':q.get('change_pct'),'거래량':q.get('volume'),'거래소':q.get('exchange')})
    show=pd.DataFrame(rows).sort_values('점수',ascending=False).head(10) if rows else pd.DataFrame()
    if show.empty: st.info('LIVE 데이터 초기 수집 중입니다.')
    else:
        show.insert(0,'순위',range(1,len(show)+1)); st.dataframe(show,use_container_width=True,hide_index=True)
        symbols=show['종목'].tolist()
else:
    ranked=rank_candidates(demo_candidates(),cfg).copy(); ranked.insert(0,'rank',range(1,len(ranked)+1))
    show=ranked[['rank','symbol','score','bias','price','day_pct','premarket_pct','rvol','dollar_volume','atr_pct']]
    st.dataframe(show,use_container_width=True,hide_index=True); symbols=ranked['symbol'].tolist()

selected=st.selectbox('오늘 집중 감시 종목',symbols,index=0 if symbols else None)
if selected:
    st.divider(); st.subheader(f'{selected} 집중 감시')
    if live:
        q=api(f'/api/quote/{selected}') or {}; sig=api(f'/api/signal/{selected}') or {}; b1=api(f'/api/bars/{selected}?minutes=1&limit=200') or {'data':[]}; b5=api(f'/api/bars/{selected}?minutes=5&limit=100') or {'data':[]}
        m1,m2,m3,m4=st.columns(4); m1.metric('상태',sig.get('state','WARMING')); m2.metric('Signal',f"{sig.get('score',0)}/100"); m3.metric('현재가',f"${float(q.get('price') or 0):,.2f}"); m4.metric('Bias',sig.get('bias','-'))
        st.markdown(f'''<div class="signal"><b>{selected} · {sig.get('state','WARMING')}</b><br>{sig.get('reason','데이터 수집 중')}</div>''',unsafe_allow_html=True)
        l,r=st.columns(2)
        with l:
            df=pd.DataFrame(b1['data']); st.caption('1분봉 LIVE');
            if not df.empty: st.line_chart(df.set_index('time')['close'],height=280)
        with r:
            df5=pd.DataFrame(b5['data']); st.caption('5분봉 LIVE');
            if not df5.empty: st.line_chart(df5.set_index('time')['close'],height=280)
    else:
        bars=demo_bars(selected); sig=intraday_signal(selected,bars,market_bias=.7,cfg=cfg)
        st.metric('상태',sig.state); st.line_chart(bars.set_index('time')['close'],height=300)

st.caption('V1.1: Kiwoom 인증/WebSocket + AWS LIVE API 연결. 전체시장 자동 TOP10 스캐너와 카카오 긴급알림은 다음 단계에서 확장합니다.')
