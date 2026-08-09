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


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _market_session_label(raw_status, market='USA'):
    s=str(raw_status or '').upper()
    if market=='USA':
        if 'PRE' in s:
            return '프리마켓'
        if 'AFTER' in s or 'POST' in s:
            return '애프터마켓'
        if 'OPEN' in s or 'REGULAR' in s or 'LIVE' in s:
            return '정규장 거래중'
        return '장 마감'
    else:
        if 'OPEN' in s:
            return '정규장 거래중'
        if 'PRE' in s:
            return '장 시작 전'
        return '장 마감'

def _bias_label(raw):
    s=str(raw or '').upper()
    if s=='BULL':
        return '상승 우세'
    if s=='BEAR':
        return '하락 우세'
    if s=='NEUTRAL':
        return '혼조'
    return str(raw or '-')

def _quality_label(raw):
    s=str(raw or '')
    return {
        'A':'일반',
        'B_EVENT':'주의',
        'C_HIGH_RISK':'고위험',
        'REJECT':'제외'
    }.get(s,s or '-')

def _risk_label(raw):
    s=str(raw or '').upper()
    return {
        'NORMAL':'보통',
        'MEDIUM':'주의',
        'HIGH':'높음',
        'EXTREME':'매우 높음'
    }.get(s, s or '-')

def render_market_summary(market_name, market_status, market_bias, universe_count, final_count, data_status, quality_counts=None):
    st.subheader(f'{market_name} · Trading')
    st.caption('시장 → 최종 추천 → 종목 상세 → 후보 → 시장 맥락')
    a,b,c,d=st.columns(4)
    a.metric('시장',market_status)
    b.metric('시장 방향',market_bias)
    c.metric('분석 후보군',str(universe_count))
    d.metric('최종 추천',str(final_count))
    qc=quality_counts or {}
    st.caption(
        f"데이터 {data_status} · 일반후보 {qc.get('A',0)} · 이벤트후보 {qc.get('B_EVENT',0)} · "
        f"고위험 {qc.get('C_HIGH_RISK',0)} · 제외 {qc.get('REJECT',0)}"
    )

def render_final_table(rows):
    st.markdown('### 🎯 최종 추천 1~5')
    if not rows:
        st.info('현재 매매 조건을 모두 만족한 종목이 없습니다. NO TRADE도 정상입니다.')
        return
    normalized=[]
    for r in rows[:5]:
        normalized.append({
            '종목':r.get('symbol','-'),
            '종목명':r.get('name') or '-',
            '판단':r.get('action','-'),
            '추천점수':r.get('final_score','-'),
            '방향':r.get('bias','-'),
            '현재가':r.get('price','-'),
            '손절/무효화':r.get('invalidation','-'),
            'T1':r.get('target1','-'),
            'T2':r.get('target2','-'),
            '핵심이유':r.get('reason','-'),
        })
    st.dataframe(pd.DataFrame(normalized),use_container_width=True,hide_index=True)

def render_candidate_table(rows, quality_map=None, key_prefix='candidate'):
    with st.expander('👀 후보 TOP10 · 더 깊게 볼 종목',expanded=False):
        st.caption('추천주가 아닙니다. 최종 추천 엔진이 추가 검토할 정밀분석 후보입니다.')
        if not rows:
            st.info('후보 데이터가 아직 없습니다.')
            return

        sort_mode=st.selectbox(
            '정렬 기준',
            ['엔진 기본순위','품질 우선','후보점수 높은순','등락률 높은순','거래량강도 높은순','위험 낮은순'],
            index=0,
            key=f'{key_prefix}_sort'
        )

        qmap=quality_map or {}
        prepared=[]
        for i,r in enumerate(rows[:50],1):
            q=qmap.get(str(r.get('symbol') or '').upper(),{})
            quality=r.get('quality_grade') or q.get('quality_grade') or '-'
            risk=r.get('chase_risk') or q.get('chase_risk') or 'NORMAL'
            prepared.append({
                '_engine_rank':i,
                '_quality_raw':quality,
                '_risk_raw':risk,
                '순위':i,
                '품질':_quality_label(quality),
                '종목':r.get('symbol','-'),
                '종목명':r.get('name') or q.get('name') or '-',
                '후보점수':r.get('score','-'),
                '방향':r.get('bias','-'),
                '현재가':r.get('price','-'),
                '등락률%':r.get('change_pct','-'),
                '거래량강도':r.get('rvol') if r.get('rvol') is not None else '-',
                '위험':_risk_label(risk),
            })

        def n(v, default=-999999):
            try:
                return float(v)
            except Exception:
                return default

        quality_rank={'A':0,'B_EVENT':1,'C_HIGH_RISK':2,'REJECT':3,'-':4}
        risk_rank={'NORMAL':0,'MEDIUM':1,'HIGH':2,'EXTREME':3}

        if sort_mode=='품질 우선':
            prepared.sort(key=lambda x:(quality_rank.get(x['_quality_raw'],9),-n(x['후보점수'])))
        elif sort_mode=='후보점수 높은순':
            prepared.sort(key=lambda x:-n(x['후보점수']))
        elif sort_mode=='등락률 높은순':
            prepared.sort(key=lambda x:-n(x['등락률%']))
        elif sort_mode=='거래량강도 높은순':
            prepared.sort(key=lambda x:-n(x['거래량강도']))
        elif sort_mode=='위험 낮은순':
            prepared.sort(key=lambda x:(risk_rank.get(x['_risk_raw'],9),-n(x['후보점수'])))

        for j,x in enumerate(prepared[:10],1):
            x['순위']=j

        show=pd.DataFrame([{k:v for k,v in x.items() if not k.startswith('_')} for x in prepared[:10]])
        st.dataframe(show,use_container_width=True,hide_index=True)
        st.caption('품질: 일반=기본 분석 후보 · 주의=이벤트/레버리지 등 추가 주의 · 고위험=정상 추천 제외 대상')

def render_chart_placeholder(title, message):
    st.caption(title)
    st.markdown(
        f'''<div style="height:300px;border:1px solid rgba(128,128,128,.25);border-radius:10px;
        display:flex;align-items:center;justify-content:center;text-align:center;padding:20px;">
        <div><b>{message}</b><br><small>차트 위치와 화면 구조는 미장과 동일하게 유지합니다.</small></div></div>''',
        unsafe_allow_html=True
    )

def render_intraday_chart(rows, title):
    df=pd.DataFrame(rows)
    if df.empty:
        render_chart_placeholder(title,'분봉 데이터 준비 중')
        return
    st.caption(title)
    df['time']=pd.to_datetime(df['time'],utc=True,errors='coerce')
    for c in ['open','high','low','close','volume']:
        df[c]=pd.to_numeric(df.get(c),errors='coerce')
    df=df.dropna(subset=['time','close']).sort_values('time')
    if df.empty:
        render_chart_placeholder(title,'사용 가능한 분봉 데이터 없음')
        return
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

def render_detail_shell(state, bias, signal_score, confirm_5m, volume_strength, price, chart1=None, chart5=None, pending_message=None):
    m1,m2,m3,m4,m5,m6=st.columns(6)
    m1.metric('상태',state)
    m2.metric('방향',bias)
    m3.metric('매매 신호점수',signal_score)
    m4.metric('5분 추세 확인',confirm_5m)
    m5.metric('거래 강도',volume_strength)
    m6.metric('현재가',price)
    l,r=st.columns(2)
    if pending_message:
        with l:
            render_chart_placeholder('1분봉 · EMA9 / EMA20 / VWAP',pending_message)
        with r:
            render_chart_placeholder('5분봉 · EMA9 / EMA20 / VWAP',pending_message)
    else:
        with l:
            render_intraday_chart(chart1 or [],'1분봉 · EMA9 / EMA20 / VWAP')
        with r:
            render_intraday_chart(chart5 or [],'5분봉 · EMA9 / EMA20 / VWAP')

health=api('/health') if API_URL else None
live=bool(health and health.get('ok'))
mode='LIVE DATA' if live else 'DEMO DATA'
version=(health or {}).get('version','3.4') if live else '3.4'
st.markdown(f'''<div class="hero"><div><h1>DAY TRADER WEB</h1><div>시장 → 최종추천 → 종목상세 → 후보 → 검증</div></div><div><span class="badge">{mode}</span><span class="badge">NO AUTO ORDER</span><span class="badge">v{version}</span></div></div>''',unsafe_allow_html=True)

st.caption('V3.4 · ALL TABS MARKET AWARE · 전 탭 시장선택 연동 · NO AUTO ORDER')


st.markdown('### 시장 선택')
market_view=st.radio(
    '시장',
    ['🇺🇸 USA','🇰🇷 KOREA'],
    horizontal=True,
    key='global_market_view',
    label_visibility='collapsed'
)
st.caption('선택한 시장은 Trading · Briefing · Research · Archive · Live Validation 전체에 동일하게 적용됩니다.')

tab_trading, tab_brief, tab_research, tab_archive, tab_live = st.tabs([
    '📈 Trading', '🗞️ Briefing', '🧪 Research', '📚 Archive', '✅ Live Validation'
])

with tab_trading:
    if market_view=='🇺🇸 USA':
        qqq=api('/api/quote/QQQ') if live else {}
        smh=api('/api/quote/SMH') if live else {}
        qqq_pct=_safe_float((qqq or {}).get('change_pct'))
        smh_pct=_safe_float((smh or {}).get('change_pct'))
        market_label='BULL' if qqq_pct>=.3 else ('BEAR' if qqq_pct<=-.3 else 'NEUTRAL')
        sector_label='STRONG' if smh_pct>=.5 else ('WEAK' if smh_pct<=-.5 else 'NEUTRAL')

        uni=(api('/api/universe') or {}) if live else {}
        fr=(api('/api/recommendations/final?limit=5',timeout=20) or {}) if live else {}
        payload=(api('/api/screener?top_n=10') or {'data':[]}) if live else {'data':[]}
        rows=payload.get('data') or []
        frows=fr.get('data') or []
        qmap={str(r.get('symbol') or '').upper():r for r in (uni.get('rows') or [])}
        symbols=[]
        for r in frows+rows:
            s=r.get('symbol')
            if s and s not in symbols:
                symbols.append(s)
        for s in (uni.get('core') or []):
            if s and s not in symbols:
                symbols.append(s)

        render_market_summary(
            '🇺🇸 USA',
            _market_session_label((qqq or {}).get('session') or (qqq or {}).get('market_status') or ('OPEN' if live else 'CLOSED'),'USA'),
            f"{_bias_label(market_label)} · 나스닥 {qqq_pct:+.2f}%",
            uni.get('count',0),
            len(frows),
            'LIVE' if live else 'DEMO',
            uni.get('quality_counts') or {}
        )
        if st.button('↻ 화면 새로고침',key='us_simple_refresh'):
            st.rerun()

        render_final_table(frows)

        st.markdown('### 📈 종목 상세보기')
        if symbols:
            us_label_map={}
            for rr in frows+rows+(uni.get('rows') or []):
                s=rr.get('symbol')
                if not s:
                    continue
                nm=rr.get('name') or ''
                us_label_map[s]=f"{nm} ({s})" if nm and nm!=s else s
            selected=st.selectbox(
                '종목 선택',
                symbols,
                key='us_detail_symbol',
                format_func=lambda x:us_label_map.get(x,x)
            )
            q=(api(f'/api/quote/{selected}') or {}) if live else {}
            sig=(api(f'/api/signal/{selected}') or {}) if live else {}
            b1=(api(f'/api/bars/{selected}?minutes=1&limit=200') or {'data':[]}) if live else {'data':[]}
            b5=(api(f'/api/bars/{selected}?minutes=5&limit=100') or {'data':[]}) if live else {'data':[]}
            ind=sig.get('indicators') or {}
            render_detail_shell(
                sig.get('state','WAIT'),
                sig.get('bias','NEUTRAL'),
                f"{sig.get('score',0)}/100",
                sig.get('confirm_5m',0),
                f"{_safe_float(ind.get('rvol')):.2f}x" if ind else '-',
                fmt_px(q.get('price')),
                b1.get('data') or [],
                b5.get('data') or []
            )
            st.caption(sig.get('reason') or '실시간 신호 데이터 준비 중')
            if sig.get('risks'):
                st.warning('리스크 · '+str(sig.get('risks')))
            st.caption(
                f"손절/무효화 {fmt_level(sig.get('invalidation'))} · "
                f"T1 {fmt_level(sig.get('target1'))} · T2 {fmt_level(sig.get('target2'))}"
            )
        else:
            st.info('상세보기 대상 종목이 없습니다.')

        render_candidate_table(rows,qmap,'us_candidate')

        st.markdown('### 📍 시장 상황')
        st.caption('시장 전체의 흐름과 현재 거래 가능 시간대를 간단히 보여줍니다.')
        x1,x2,x3,x4=st.columns(4)
        x1.metric('현재 거래시간',_market_session_label((qqq or {}).get('session') or (qqq or {}).get('market_status') or ('OPEN' if live else 'CLOSED'),'USA'))
        x2.metric('지수 흐름',f"{_bias_label(market_label)} · 나스닥 {qqq_pct:+.2f}%")
        x3.metric('반도체 흐름',f"{'강세' if smh_pct>=.5 else ('약세' if smh_pct<=-.5 else '보합')} · {smh_pct:+.2f}%")
        x4.metric('장세','추세장' if abs(qqq_pct)>=.4 else '혼조장')

        with st.expander('⚙️ 진단/수동 복구',expanded=False):
            st.caption('평소에는 사용하지 않습니다. 데이터 이상이 있을 때만 사용하세요.')
            st.markdown('**시장 후보 다시 찾기** · 거래량/거래대금 랭킹을 다시 조회해 분석 후보군을 재구성합니다.')
            if live and st.button('시장 후보 다시 찾기',key='us_market_rescan',use_container_width=True):
                res=api_post('/api/scan/market') or {}
                if res.get('ok'):
                    st.success('분석 후보군 재구성 완료')
                    st.rerun()
                elif res.get('cooldown'):
                    st.warning(f"재검색 대기 중 · 약 {res.get('retry_after')}초 후 가능")
                else:
                    st.error('시장 후보 재검색 실패')

    else:
        ks=api('/api/korea/status') if live else {}
        ku=api('/api/korea/universe') if live else {}
        krf=api('/api/korea/recommendations/final?limit=5') if live else {}
        kt=api('/api/korea/top10') if live else {}
        kpulse=api('/api/korea/pulse') if live else {}
        kl=api('/api/korea/preopen/latest') if live else {}

        kqc=(ks or {}).get('quality_counts') or {}
        po=bool((kpulse or {}).get('market_open'))
        klm=(kl or {}).get('meta') or {}
        kle=klm.get('extra') or {}
        krfd=(krf or {}).get('data') or []
        trows=(kt or {}).get('data') or []
        long_power=_safe_float(klm.get('market_long_power'),50.0)
        bias='BULL' if long_power>=60 else ('BEAR' if long_power<=40 else 'NEUTRAL')

        render_market_summary(
            '🇰🇷 KOREA',
            _market_session_label('OPEN' if po else 'CLOSED','KOREA'),
            _bias_label(bias),
            (ks or {}).get('universe_count') or 0,
            len(krfd),
            'LIVE' if (ks or {}).get('adapter_ready') else 'WAIT',
            kqc
        )
        st.caption(f"후보군 방향 비율 · 상승 {long_power:.0f}% / 하락 {100-long_power:.0f}%")
        if st.button('↻ 화면 새로고침',key='kr_simple_refresh'):
            st.rerun()

        render_final_table(krfd)
        if not (krf or {}).get('buy_now_enabled',False):
            st.caption('국장은 검증된 1분/5분 분봉 연결 전이라 BUY NOW는 차단하고 WATCH까지만 허용합니다.')

        detail_symbols=[]
        for r in krfd+trows:
            s=r.get('symbol')
            if s and s not in detail_symbols:
                detail_symbols.append(s)

        st.markdown('### 📈 종목 상세보기')
        if detail_symbols:
            klabel_map={}
            for rr in krfd+trows:
                s=rr.get('symbol')
                if not s:
                    continue
                nm=rr.get('name') or ''
                klabel_map[s]=f"{nm} ({s})" if nm else s
            ksel=st.selectbox(
                '종목 선택',
                detail_symbols,
                key='kr_detail_symbol',
                format_func=lambda x:klabel_map.get(x,x)
            )
            prow=next((x for x in ((kpulse or {}).get('top10') or []) if x.get('symbol')==ksel),{})
            brow=next((x for x in trows if x.get('symbol')==ksel),{})
            state='WATCH' if any(x.get('symbol')==ksel and x.get('action')=='WATCH' for x in krfd) else 'WAIT'
            render_detail_shell(
                state,
                str(prow.get('bias') or brow.get('bias') or 'NEUTRAL'),
                str(prow.get('live_score') or brow.get('score') or '-'),
                '-',
                str(prow.get('strength_composite') or '-'),
                str(brow.get('price') or '-'),
                pending_message='국장 분봉 데이터 연결 준비 중'
            )
            st.caption('차트가 연결되면 이 위치에서 미장과 동일하게 VWAP·EMA9·EMA20·1분/5분 확인을 표시합니다.')
        else:
            st.info('상세보기 대상 종목이 없습니다.')

        render_candidate_table(trows,{str(r.get('symbol') or '').upper():r for r in (ku.get('rows') or [])},'kr_candidate')

        st.markdown('### 📍 시장 상황')
        st.caption('지수/후보군 분위기와 장전·장중 데이터가 실제 판단에 사용 중인지 보여줍니다.')
        x1,x2,x3,x4=st.columns(4)
        x1.metric('현재 거래시간',_market_session_label('OPEN' if po else 'CLOSED','KOREA'))
        x2.metric('후보군 분위기',_bias_label(bias))
        x2.caption(f"상승 {long_power:.0f}% · 하락 {100-long_power:.0f}%")
        x3.metric('장전 데이터','반영 중' if _safe_float(kle.get('expected_coverage_pct'),0)>0 else '미사용')
        x4.metric('장중 체결 데이터','사용 중' if po else '미사용')
        if not po and _safe_float(kle.get('expected_coverage_pct'),0)==0:
            st.caption('현재는 장전 예상체결과 장중 체결강도 데이터를 추천 점수에 사용하지 않는 시간대입니다.')

        with st.expander('⚙️ 진단/수동 복구',expanded=False):
            st.caption('평소에는 사용하지 않습니다. 데이터 이상이 있을 때만 사용하세요.')
            st.markdown('**API 연결 확인** · 키움 국내 REST 연결 상태만 확인합니다.')
            if live and st.button('API 연결 확인',key='kr_quote_probe',use_container_width=True):
                q=api('/api/korea/quote/005930',timeout=25) or {}
                if q.get('ok'):
                    st.success('국내 REST 연결 정상')
                else:
                    st.error('국내 REST 연결 실패')

            st.markdown('**시장 후보 다시 찾기** · 거래대금/거래량/등락률을 다시 조회해 분석 후보군을 재구성합니다.')
            if live and st.button('시장 후보 다시 찾기',key='kr_market_rescan',use_container_width=True):
                rr=api_post('/api/korea/scan?limit=50') or {}
                if rr.get('ok'):
                    st.success('분석 후보군 재구성 완료')
                    st.rerun()
                else:
                    st.error('후보군 재검색 실패')

            st.markdown('**장중 신호 다시 계산** · 체결강도와 VI를 다시 불러와 장중 점수를 갱신합니다.')
            if live and st.button('장중 신호 다시 계산',key='kr_pulse_refresh',use_container_width=True):
                rr=api_post('/api/korea/pulse/refresh?force=false') or {}
                if rr.get('updated_at'):
                    st.success('장중 신호 갱신 완료')
                    st.rerun()
                else:
                    st.error('장중 신호 갱신 실패')

    with st.expander('❓ 용어 설명',expanded=False):
        st.markdown(
            '''
**현재 거래시간**  
- 정규장 거래중: 일반 주식시장이 열려 있는 시간  
- 프리마켓: 미국 정규장 시작 전 거래시간  
- 애프터마켓: 미국 정규장 종료 후 거래시간  
- 장 마감: 현재 정규 거래시간이 아님  

**시장/후보군 분위기**  
- 상승 우세: 상승 방향 신호가 더 많음  
- 하락 우세: 하락 방향 신호가 더 많음  
- 혼조: 방향이 뚜렷하지 않음  

**분석 후보군**: 전체 시장에서 기본 품질·유동성 기준을 통과한 종목입니다.  
**후보점수**: 매수확률이 아니라 더 깊게 분석할 우선순위입니다.  
**추천점수**: 최종 엔진이 차트·시장·위험조건을 반영해 계산하는 실제 매매 적합도입니다.  
**품질 일반**: 기본 분석 후보. **품질 주의**: 이벤트/레버리지 등 추가 주의가 필요한 후보.  
**BUY NOW / WATCH / WAIT / AVOID**: 실제 행동 단계입니다. 조건을 못 넘으면 추천 0개도 정상입니다.
'''
        )


with tab_brief:
    st.subheader('🗞️ Briefing')
    st.caption(f"현재 선택 시장: {'미국' if market_view=='🇺🇸 USA' else '한국'}")

    if market_view=='🇺🇸 USA':

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
    else:
        st.markdown('### 🇰🇷 한국장 08:30 장전 브리핑')
        st.caption('한국장은 08:30 KST PREOPEN 리포트를 중심으로 표시합니다. 뉴스 AI 결합은 다음 엔진 단계에서 추가합니다.')
        kb1,kb2=st.columns([1,3])
        with kb1:
            if live and st.button('한국장 장전 브리핑 다시 생성',key='kr_brief_generate',use_container_width=True):
                rr=api_post('/api/korea/preopen/generate') or {}
                if rr.get('ok') or rr.get('report_id'):
                    st.success('한국장 PREOPEN 리포트 생성 완료')
                    st.rerun()
                else:
                    st.error('PREOPEN 리포트 생성 실패')
        with kb2:
            st.info('자동 저장 시각: 평일 08:30 KST · 장전 예상체결 유효시간에만 해당 데이터를 점수에 반영합니다.')

        latest=api('/api/korea/preopen/latest') or {} if live else {}
        meta=(latest or {}).get('meta') or {}
        extra=meta.get('extra') or {}
        b1,b2,b3,b4=st.columns(4)
        b1.metric('데이터 모드','장전 실시간' if extra.get('expected_window_live') else '기본 점수')
        b2.metric('예상체결 반영',f"{extra.get('expected_coverage_pct',0)}%")
        b3.metric('상승 후보 비율',str(meta.get('market_long_power') or '-'))
        b4.metric('TOP10 매칭',f"{extra.get('expected_matched_top10',0)}/10")
        rows=(latest or {}).get('rows') or []
        if rows:
            df=pd.DataFrame(rows[:10])
            keep=['current_rank','symbol','name','current_score','gamma_score','recommendation','expected_change_pct','chase_risk']
            df=df[[c for c in keep if c in df.columns]].rename(columns={
                'current_rank':'순위','symbol':'종목','name':'종목명','current_score':'장전점수',
                'gamma_score':'기본점수','recommendation':'방향','expected_change_pct':'예상등락%',
                'chase_risk':'추격위험'
            })
            st.dataframe(df,use_container_width=True,hide_index=True)
        else:
            st.info('저장된 한국장 PREOPEN 리포트가 없습니다.')


with tab_research:
    st.subheader('🧪 Research')
    st.caption(f"현재 선택 시장: {'미국' if market_view=='🇺🇸 USA' else '한국'} · 엔진 내부 데이터와 후보 선정 근거를 확인합니다.")

    if market_view=='🇺🇸 USA':

        st.subheader('🔬 엔진 내부 확인')
        st.caption('Trading에서 숨긴 후보군·Final Engine 판정 근거·모델 비교·검증 자료를 확인합니다.')

        if live:
            if market_view=='🇺🇸 USA':
                rfr=api('/api/recommendations/final?limit=5',timeout=20) or {}
                with st.expander('Final Engine 판정 근거',expanded=False):
                    erows=(rfr or {}).get('evaluated') or []
                    if erows:
                        edf=pd.DataFrame(erows)
                        keep=['symbol','action','final_score','quality_grade','candidate_score','signal_score',
                              'confirm_5m','bias','state','blocks']
                        edf=edf[[c for c in keep if c in edf.columns]].rename(columns={
                            'symbol':'종목','action':'판단','final_score':'추천점수','quality_grade':'품질',
                            'candidate_score':'후보점수','signal_score':'신호점수','confirm_5m':'5분확인',
                            'bias':'방향','state':'상태','blocks':'차단이유'
                        })
                        st.dataframe(edf,use_container_width=True,hide_index=True)
            else:
                rfr=api('/api/korea/recommendations/final?limit=5') or {}
                with st.expander('Final Engine 판정 근거',expanded=False):
                    erows=(rfr or {}).get('evaluated') or []
                    if erows:
                        st.dataframe(pd.DataFrame(erows),use_container_width=True,hide_index=True)

        st.subheader('🔬 Research · 엔진 내부 확인')
        st.caption('Trading에서 숨긴 분석 후보군, 품질 필터, 모델 비교와 검증 자료를 여기서 확인합니다.')

        with st.expander('📘 항목 설명',expanded=False):
            st.markdown(
                '''
    **분석 후보군**: 전체 시장에서 가격·유동성·위험 조건을 통과해 더 깊게 분석할 종목 집합입니다.  
    **일반 후보(A)**: 기본 품질조건을 통과한 종목입니다.  
    **이벤트 후보(B_EVENT)**: 레버리지 ETF, 강한 이벤트 종목 등 별도 주의가 필요한 후보입니다.  
    **고위험(C_HIGH_RISK)**: 관찰은 가능하지만 정상 추천 대상에서는 제외합니다.  
    **제외(REJECT)**: 가격·유동성·시총·종목상태 등 기준 때문에 정밀분석하지 않습니다.  
    **선정근거 수**: 거래대금·거래량·급증·등락률 등 몇 개의 탐색 조건에서 동시에 잡혔는지 뜻합니다.
    '''
            )

        if live:
            if market_view=='🇰🇷 KOREA':
                ru=api('/api/korea/universe') or {}
                rrows=ru.get('rows') or []
                st.markdown('### 🇰🇷 분석 후보군')
                if rrows:
                    rdf=pd.DataFrame(rrows)
                    keep=['quality_grade','quality_reasons','instrument_type','market_cap_rank','symbol','name','market',
                          'price','change_pct','trading_value','source_count','score','bias']
                    rdf=rdf[[c for c in keep if c in rdf.columns]].rename(columns={
                        'quality_grade':'등급','quality_reasons':'선정/제외 이유','instrument_type':'유형',
                        'market_cap_rank':'시총순위','symbol':'종목','name':'종목명','market':'시장',
                        'price':'현재가','change_pct':'등락률%','trading_value':'거래대금',
                        'source_count':'선정근거 수','score':'후보점수','bias':'방향'
                    })
                    st.dataframe(rdf,use_container_width=True,hide_index=True)
            else:
                ru=api('/api/universe') or {}
                rrows=ru.get('rows') or []
                st.markdown('### 🇺🇸 분석 후보군')
                if rrows:
                    rdf=pd.DataFrame(rrows)
                    keep=['quality_grade','quality_reasons','origin','symbol','name','asset_type','exchange',
                          'price','change_pct','volume_rank','dollar_rank','surge_pct','chase_risk','sources']
                    rdf=rdf[[c for c in keep if c in rdf.columns]].rename(columns={
                        'quality_grade':'등급','quality_reasons':'선정/제외 이유','origin':'구분','symbol':'종목',
                        'name':'종목명','asset_type':'유형','exchange':'거래소','price':'현재가',
                        'change_pct':'등락률%','volume_rank':'거래량순위','dollar_rank':'거래대금순위',
                        'surge_pct':'거래량급증%','chase_risk':'추격위험','sources':'선정근거'
                    })
                    rdf=rdf.replace(9999,'-')
                    st.dataframe(rdf,use_container_width=True,hide_index=True)

        st.divider()
        st.caption('아래는 과거 시뮬레이션 · Weight Study · Walk-forward · Regime · Relative Strength 검증 영역입니다.')
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


    else:
        st.markdown('### 🇰🇷 한국장 엔진 내부 확인')
        if live:
            ru=api('/api/korea/universe') or {}
            rrows=ru.get('rows') or []
            with st.expander('분석 후보군',expanded=True):
                if rrows:
                    rdf=pd.DataFrame(rrows)
                    keep=['quality_grade','quality_reasons','instrument_type','market_cap_rank','symbol','name','market',
                          'price','change_pct','trading_value','source_count','score','bias']
                    rdf=rdf[[c for c in keep if c in rdf.columns]].rename(columns={
                        'quality_grade':'등급','quality_reasons':'선정이유','instrument_type':'유형',
                        'market_cap_rank':'시총순위','symbol':'종목','name':'종목명','market':'시장',
                        'price':'현재가','change_pct':'등락률%','trading_value':'거래대금',
                        'source_count':'선정근거 수','score':'후보점수','bias':'방향'
                    })
                    st.dataframe(rdf,use_container_width=True,hide_index=True)
                else:
                    st.info('한국장 후보군 데이터가 없습니다.')

            rfr=api('/api/korea/recommendations/final?limit=5') or {}
            with st.expander('Final Engine 판정 근거',expanded=False):
                erows=(rfr or {}).get('evaluated') or []
                if erows:
                    st.dataframe(pd.DataFrame(erows),use_container_width=True,hide_index=True)
                else:
                    st.info('Final Engine 평가 데이터가 없습니다.')

            pulse=api('/api/korea/pulse') or {}
            with st.expander('체결강도 / VI 연구 데이터',expanded=False):
                prows=(pulse or {}).get('top10') or []
                if prows:
                    st.dataframe(pd.DataFrame(prows),use_container_width=True,hide_index=True)
                else:
                    st.info('장중 Pulse 데이터가 없습니다.')
        else:
            st.info('백엔드 연결 후 한국장 Research 데이터를 표시합니다.')


with tab_archive:
    st.subheader('📚 Archive')
    st.caption(f"현재 선택 시장: {'미국' if market_view=='🇺🇸 USA' else '한국'}")

    if market_view=='🇺🇸 USA':

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

    else:
        st.markdown('### 🇰🇷 한국장 저장 기록')
        st.caption('현재 한국장은 PREOPEN 저장 기록을 우선 조회합니다. 장중/종가 추천 스냅샷 저장은 검증 모듈 확장 시 추가합니다.')
        if live:
            hist=api('/api/korea/preopen/history?limit=60') or {}
            rows=hist.get('data') or hist.get('rows') or []
            if rows:
                hdf=pd.DataFrame(rows)
                st.dataframe(hdf,use_container_width=True,hide_index=True)
            else:
                st.info('저장된 한국장 PREOPEN 기록이 없습니다.')
        else:
            st.info('백엔드 연결 후 한국장 Archive를 표시합니다.')


with tab_live:
    st.subheader('✅ Live Validation')
    st.caption(f"현재 선택 시장: {'미국' if market_view=='🇺🇸 USA' else '한국'}")

    if market_view=='🇺🇸 USA':

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


        st.caption('미국장 Live Validation · 저장된 추천 스냅샷과 실제 결과를 검증합니다.')

    else:
        st.markdown('### 🇰🇷 한국장 실전 검증 준비상태')
        st.info('한국장은 아직 검증된 1분/5분 분봉 Gate가 연결되지 않아 BUY NOW 검증을 시작하지 않습니다.')
        if live:
            status=api('/api/korea/status') or {}
            pulse=api('/api/korea/pulse') or {}
            final=api('/api/korea/recommendations/final?limit=5') or {}
            v1,v2,v3,v4=st.columns(4)
            v1.metric('분석 후보군',status.get('universe_count',0))
            v2.metric('최종 추천',final.get('count',0))
            v3.metric('분봉 Gate','준비 중')
            v4.metric('장중 Pulse','사용 가능' if pulse.get('updated_at') else '대기')
            st.caption('분봉 연결 후 추천 시점 가격을 자동 저장하고 5/15/30/60분 및 종가 성과를 추적하는 검증을 시작합니다.')
        else:
            st.info('백엔드 연결 후 한국장 검증 준비상태를 표시합니다.')
