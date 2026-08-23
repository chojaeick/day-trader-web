from pathlib import Path
import re

APP=Path('app_v5.py')


def replace_between(src,start_pat,end_pat,repl,label):
    m=re.search(start_pat+r'.*?(?='+end_pat+r')',src,re.S)
    if not m:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return src[:m.start()]+repl+src[m.end():]


def main():
    s=APP.read_text()

    if not re.search(r'^import sqlite3\s*$',s,re.M):
        s=s.replace('import os\n','import os\nimport sqlite3\n',1)

    anchor='def engine_matrix(live):\n'
    if anchor not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: engine_matrix')

    helpers=r'''LONGTERM_DB_PATH=os.getenv('DAYTRADER_DB_PATH','/home/ubuntu/day-trader-api/daytrader.db')
LONGTERM_NEAR_AVG_PCT=3.0
LONGTERM_GAP_DELTA_EPS=0.20

@st.cache_data(ttl=3600,show_spinner=False)
def _monthly_close_history(symbol):
    try:
        con=sqlite3.connect(LONGTERM_DB_PATH,timeout=5)
        cols=[r[1] for r in con.execute('PRAGMA table_info(daily_history)').fetchall()]
        def pick(options):
            return next((x for x in options if x in cols),None)
        sym_col=pick(['symbol','ticker','code'])
        date_col=pick(['trade_date','date','day','ts','datetime'])
        close_col=pick(['close','close_price','price','last_price'])
        if not (sym_col and date_col and close_col):
            con.close();return {'ok':False,'reason':'daily_history schema unsupported'}
        q=f'SELECT "{date_col}","{close_col}" FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}"'
        rows=con.execute(q,(str(symbol).upper(),)).fetchall();con.close()
        if not rows:return {'ok':False,'reason':'월봉 데이터 없음'}
        df=pd.DataFrame(rows,columns=['date','close'])
        df['date']=pd.to_datetime(df['date'],errors='coerce')
        df['close']=pd.to_numeric(df['close'],errors='coerce')
        df=df.dropna().sort_values('date')
        if df.empty:return {'ok':False,'reason':'월봉 데이터 파싱 실패'}
        df['month']=df['date'].dt.to_period('M')
        mon=df.groupby('month',as_index=False).tail(1)[['month','close']].reset_index(drop=True)
        return {'ok':True,'rows':[(str(r.month),float(r.close)) for r in mon.itertuples()]}
    except Exception as e:
        return {'ok':False,'reason':str(e)}

def longterm_ma10_eval(symbol,current_price,avg_price):
    hist=_monthly_close_history(symbol)
    if not hist.get('ok'):
        return {'ok':False,'judgment':'중장기 데이터대기','reason':hist.get('reason') or '월봉 데이터 부족'}
    rows=list(hist.get('rows') or [])
    cur=f(current_price,None);avg=f(avg_price,None)
    if cur is None or cur<=0:
        return {'ok':False,'judgment':'중장기 데이터대기','reason':'현재가 없음'}

    now_month=str(pd.Timestamp.now().to_period('M'))
    completed=[(m,c) for m,c in rows if m<now_month]
    if len(completed)<10:
        return {'ok':False,'judgment':'중장기 데이터대기','reason':f'완료 월봉 {len(completed)}개 · 10개 필요'}

    # Current MA10 = previous 9 completed monthly closes + live/current month price.
    prev9=[c for _,c in completed[-9:]]
    ma10=(sum(prev9)+cur)/10.0
    gap=(cur/ma10-1.0)*100.0 if ma10 else 0.0

    # Previous completed-month gap uses its own 10 completed monthly closes.
    prev10=completed[-10:]
    prev_ma10=sum(c for _,c in prev10)/10.0
    prev_close=prev10[-1][1]
    prev_gap=(prev_close/prev_ma10-1.0)*100.0 if prev_ma10 else 0.0
    gap_delta=gap-prev_gap
    avg_pct=(cur/avg-1.0)*100.0 if avg not in (None,0) else None

    # Priority rules requested by user.
    if cur < ma10:
        judgment='탈출'
        reason='월봉 10MA 하향 이탈 · 최우선 탈출 규칙'
        risk='HIGH'
    elif avg not in (None,0) and cur < avg:
        judgment='손절준비'
        reason='월봉 10MA 위지만 현재가가 매수평단 아래'
        risk='WARN'
    elif gap_delta < -LONGTERM_GAP_DELTA_EPS and avg not in (None,0) and cur <= avg*(1.0+LONGTERM_NEAR_AVG_PCT/100.0):
        judgment='탈출준비'
        reason=f'10MA 괴리 축소 중 + 평단 {LONGTERM_NEAR_AVG_PCT:.0f}% 이내 접근'
        risk='WARN'
    elif gap_delta > LONGTERM_GAP_DELTA_EPS:
        judgment='상승보유'
        reason='월봉 10MA 위 · 괴리 확대 중 · 추가상승 우위'
        risk='LOW'
    else:
        judgment='보유우위'
        reason='월봉 10MA 위 · 추세 유지'
        risk='NORMAL'

    trend='확대' if gap_delta>LONGTERM_GAP_DELTA_EPS else ('축소' if gap_delta<-LONGTERM_GAP_DELTA_EPS else '보합')
    return {
        'ok':True,'judgment':judgment,'risk':risk,'reason':reason,
        'ma10':ma10,'gap_pct':gap,'prev_gap_pct':prev_gap,'gap_delta':gap_delta,
        'gap_trend':trend,'avg_pct':avg_pct,'near_avg_pct':LONGTERM_NEAR_AVG_PCT
    }

def longterm_eval_table(x,market):
    if not x.get('ok'):
        return pd.DataFrame([{'엔진':'월봉10MA v0.1','현재가':'-','평단':'-','월봉10MA':'-','MA괴리율':'-','괴리변화':'-','평단대비':'-','판단':x.get('judgment','데이터대기')}])
    return pd.DataFrame([{
        '엔진':'월봉10MA v0.1',
        '현재가':money(x.get('current'),market,'-'),
        '평단':money(x.get('avg'),market,'-'),
        '월봉10MA':money(x.get('ma10'),market,'-'),
        'MA괴리율':f"{x.get('gap_pct',0):+.2f}%",
        '괴리변화':f"{x.get('gap_trend')} ({x.get('gap_delta',0):+.2f}%p)",
        '평단대비':'-' if x.get('avg_pct') is None else f"{x.get('avg_pct'):+.2f}%",
        '판단':x.get('judgment')
    }])

'''
    s=s.replace(anchor,helpers+anchor,1)

    new_render=r'''def render_positions(market,tracker):
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

        lt=None
        if current_type=='LONG_TERM':
            lt=longterm_ma10_eval(sym,p['cur'],p['avg'])
            if lt.get('ok'):
                lt['current']=p['cur'];lt['avg']=p['avg']
            judgment=lt.get('judgment') or '중장기 데이터대기'
        elif live:
            judgment=action_ko(action_of(live))
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
            if current_type=='LONG_TERM':
                if lt and lt.get('ok'):
                    st.dataframe(longterm_eval_table(lt,market),hide_index=True,use_container_width=True,height=82)
                    st.caption(lt.get('reason') or '')
                else:
                    st.info((lt or {}).get('reason') or '월봉10MA 데이터 대기')
            elif live:
                st.dataframe(engine_matrix(live),hide_index=True,use_container_width=True,height=220)
            else:
                st.info('DAYTRADE 활성화 시 단타 엔진 평가 재개')
                st.dataframe(engine_matrix(None),hide_index=True,use_container_width=True,height=220)
        st.divider()
    if shown==0: st.info('등록된 실제 보유종목이 없습니다.')

'''
    s=replace_between(s,r'def render_positions\(market,tracker\):\n',r'def render_trading\(market\):',new_render,'render_positions')

    # Build marker only; leave visual layout otherwise frozen.
    s=s.replace('class="v24-ver">v25</span>','class="v24-ver">v27</span>',1)

    APP.write_text(s)
    print('LONGTERM_MONTHLY_MA10_V27_OK')


if __name__=='__main__':
    main()
