from pathlib import Path
import re

API=Path('live_server/api.py')
APP=Path('app_v5.py')

API_BLOCK=r'''
# ===== V33 LONG-TERM WEEKLY HISTORY FALLBACK =====
def _v33_weekly_from_daily(rows):
    from datetime import datetime as _dt
    cleaned=[]
    for x in rows or []:
        if not isinstance(x,dict):
            continue
        dt=str(x.get('dt') or x.get('date') or x.get('stk_dt') or x.get('base_dt') or '').strip().replace('-','')
        close=_v28_num(x.get('cur_prc') if x.get('cur_prc') is not None else x.get('close'))
        if len(dt)>=8 and close>0:
            try:
                d=_dt.strptime(dt[:8],'%Y%m%d').date()
            except Exception:
                continue
            cleaned.append((d,close))
    cleaned=sorted(cleaned,key=lambda z:z[0])
    by_week={}
    for d,close in cleaned:
        iso=d.isocalendar()
        key=f'{iso.year}-W{iso.week:02d}'
        by_week[key]=(d,close)
    out=[]
    for w,(d,close) in sorted(by_week.items(),key=lambda kv:kv[1][0]):
        out.append({'week':w,'date':d.strftime('%Y%m%d'),'close':close})
    return out

@app.get('/api/v5/weekly-history/{market}/{symbol}')
def v33_weekly_history(market:str,symbol:str):
    market=str(market or '').upper().strip()
    symbol=str(symbol or '').upper().strip()
    if not symbol:
        raise HTTPException(status_code=400,detail='symbol required')
    try:
        if market=='KOREA':
            code=symbol.split('_',1)[0]
            rows=[]; next_key=''; pages=0
            while pages<3:
                hdr=k.headers('ka10081')
                if next_key:
                    hdr['cont-yn']='Y'; hdr['next-key']=next_key
                body={'stk_cd':code,'base_dt':datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'),'upd_stkpc_tp':'1'}
                r=requests.post(s.rest_base+'/api/dostk/chart',headers=hdr,json=body,timeout=25)
                d=r.json()
                if d.get('return_code') not in (None,0):
                    raise RuntimeError(f"ka10081 {code}: {d.get('return_code')} {d.get('return_msg')}")
                raw=d.get('stk_dt_pole_chart_qry') or d.get('stk_dt_chart_qry') or []
                if not raw:
                    for v in d.values():
                        if isinstance(v,list): raw=v; break
                rows.extend(raw or [])
                pages+=1
                cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
                next_key=r.headers.get('next-key') or r.headers.get('Next-Key') or ''
                if cont!='Y' or not next_key: break
            weeks=_v33_weekly_from_daily(rows)
            return {'ok':len(weeks)>=10,'market':market,'symbol':code,'source':'KIWOOM_KA10081_WEEKLY','weeks':weeks[-80:],'count':len(weeks)}

        if market=='USA':
            ex=k.active_exchange(symbol)
            from datetime import timedelta as _td
            start=(datetime.now(timezone.utc)-_td(days=550)).strftime('%Y%m%d')
            r=requests.post(s.rest_base+'/api/us/chart',headers=k.headers('usa06012'),json={
                'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'
            },timeout=25)
            d=r.json()
            if d.get('return_code') not in (None,0):
                raise RuntimeError(f"usa06012 {symbol}: {d.get('return_code')} {d.get('return_msg')}")
            weeks=_v33_weekly_from_daily(d.get('result_list') or [])
            return {'ok':len(weeks)>=10,'market':market,'symbol':symbol,'source':'KIWOOM_USA06012_WEEKLY','weeks':weeks[-80:],'count':len(weeks)}

        raise HTTPException(status_code=400,detail='market must be USA or KOREA')
    except HTTPException:
        raise
    except Exception as e:
        return {'ok':False,'market':market,'symbol':symbol,'source':'KIWOOM_WEEKLY','weeks':[],'count':0,'error':str(e)}

'''

APP_HELPER=r'''@st.cache_data(ttl=300,show_spinner=False)
def _weekly_close_history(symbol,market=None,cache_epoch='v33'):
    mk=str(market or st.session_state.get('market') or '').upper()
    if mk not in {'USA','KOREA'}:
        return {'ok':False,'reason':'시장 정보 없음'}
    x=api(f'/api/v5/weekly-history/{mk}/{symbol}',45)
    if not isinstance(x,dict):
        return {'ok':False,'reason':'주봉 응답 오류'}
    weeks=list(x.get('weeks') or [])
    rows=[]
    for z in weeks:
        try:
            w=str(z.get('week') or '')
            c=float(z.get('close'))
        except Exception:
            continue
        if w and c>0:
            rows.append((w,c))
    if len(rows)<10:
        return {'ok':False,'reason':x.get('error') or f'완료 주봉 {len(rows)}개 · 10개 필요','rows':rows,'source':x.get('source')}
    return {'ok':True,'rows':rows,'source':x.get('source') or 'KIWOOM_WEEKLY'}

'''

NEW_EVAL=r'''def longterm_ma10_eval(symbol,current_price,avg_price,market=None):
    cur=f(current_price,None);avg=f(avg_price,None)
    if cur is None or cur<=0:
        return {'ok':False,'judgment':'중장기 데이터대기','reason':'현재가 없음'}

    # Primary rule: monthly MA10. If the security has fewer than 10 completed
    # monthly bars, automatically fall back to weekly MA10 using the same rules.
    hist=_monthly_close_history(symbol,market)
    rows=list(hist.get('rows') or []) if isinstance(hist,dict) else []
    now_month=str(pd.Timestamp.now().to_period('M'))
    completed=[(m,c) for m,c in rows if m<now_month]
    basis='MONTHLY'
    basis_label='월봉10MA'
    source=(hist or {}).get('source') if isinstance(hist,dict) else None

    if len(completed)<10:
        wh=_weekly_close_history(symbol,market)
        wrows=list(wh.get('rows') or []) if isinstance(wh,dict) else []
        now=pd.Timestamp.now()
        iso=now.isocalendar()
        now_week=f'{int(iso.year)}-W{int(iso.week):02d}'
        completed=[(w,c) for w,c in wrows if w<now_week]
        basis='WEEKLY'
        basis_label='주봉10MA(월봉부족 대체)'
        source=(wh or {}).get('source') if isinstance(wh,dict) else None
        if len(completed)<10:
            mr=(hist or {}).get('reason') if isinstance(hist,dict) else ''
            wr=(wh or {}).get('reason') if isinstance(wh,dict) else ''
            return {'ok':False,'judgment':'중장기 데이터대기','reason':wr or mr or f'완료 주봉 {len(completed)}개 · 10개 필요'}

    prev9=[c for _,c in completed[-9:]]
    ma10=(sum(prev9)+cur)/10.0
    gap=(cur/ma10-1.0)*100.0 if ma10 else 0.0

    prev10=completed[-10:]
    prev_ma10=sum(c for _,c in prev10)/10.0
    prev_close=prev10[-1][1]
    prev_gap=(prev_close/prev_ma10-1.0)*100.0 if prev_ma10 else 0.0
    gap_delta=gap-prev_gap
    avg_pct=(cur/avg-1.0)*100.0 if avg not in (None,0) else None

    if cur < ma10:
        judgment='탈출'
        reason=f'{basis_label} 하향 이탈 · 최우선 탈출 규칙'
        risk='HIGH'
    elif avg not in (None,0) and cur < avg:
        judgment='손절준비'
        reason=f'{basis_label} 위지만 현재가가 매수평단 아래'
        risk='WARN'
    elif gap_delta < -LONGTERM_GAP_DELTA_EPS and avg not in (None,0) and cur <= avg*(1.0+LONGTERM_NEAR_AVG_PCT/100.0):
        judgment='탈출준비'
        reason=f'{basis_label} 괴리 축소 중 + 평단 {LONGTERM_NEAR_AVG_PCT:.0f}% 이내 접근'
        risk='WARN'
    elif gap_delta > LONGTERM_GAP_DELTA_EPS:
        judgment='상승보유'
        reason=f'{basis_label} 위 · 괴리 확대 중 · 추가상승 우위'
        risk='LOW'
    else:
        judgment='보유우위'
        reason=f'{basis_label} 위 · 추세 유지'
        risk='NORMAL'

    trend='확대' if gap_delta>LONGTERM_GAP_DELTA_EPS else ('축소' if gap_delta<-LONGTERM_GAP_DELTA_EPS else '보합')
    return {
        'ok':True,'judgment':judgment,'risk':risk,'reason':reason,
        'ma10':ma10,'gap_pct':gap,'prev_gap_pct':prev_gap,'gap_delta':gap_delta,
        'gap_trend':trend,'avg_pct':avg_pct,'near_avg_pct':LONGTERM_NEAR_AVG_PCT,
        'basis':basis,'basis_label':basis_label,'source':source
    }

'''

NEW_TABLE=r'''def longterm_eval_table(x,market):
    if not x.get('ok'):
        return pd.DataFrame([{'엔진':'중장기 MA10','현재가':'-','평단':'-','기준MA10':'-','MA괴리율':'-','괴리변화':'-','평단대비':'-','판단':x.get('judgment','데이터대기')}])
    return pd.DataFrame([{
        '엔진':x.get('basis_label') or '월봉10MA',
        '현재가':money(x.get('current'),market,'-'),
        '평단':money(x.get('avg'),market,'-'),
        '기준MA10':money(x.get('ma10'),market,'-'),
        'MA괴리율':f"{x.get('gap_pct',0):+.2f}%",
        '괴리변화':f"{x.get('gap_trend')} ({x.get('gap_delta',0):+.2f}%p)",
        '평단대비':'-' if x.get('avg_pct') is None else f"{x.get('avg_pct'):+.2f}%",
        '판단':x.get('judgment')
    }])

'''

def main():
    a=API.read_text()
    if 'V33 LONG-TERM WEEKLY HISTORY FALLBACK' not in a:
        anchor="@app.get('/api/v4/runtime-mode')"
        if anchor not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: runtime route anchor')
        pos=a.index(anchor)
        a=a[:pos]+API_BLOCK+'\n'+a[pos:]
        API.write_text(a)

    s=APP.read_text()
    if 'def _weekly_close_history(' not in s:
        anchor='def longterm_ma10_eval('
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: longterm eval anchor')
        pos=s.index(anchor)
        s=s[:pos]+APP_HELPER+s[pos:]

    pat=r'def longterm_ma10_eval\(symbol,current_price,avg_price,market=None\):\n.*?(?=\ndef longterm_eval_table\()'
    m=re.search(pat,s,re.S)
    if not m:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: longterm_ma10_eval')
    s=s[:m.start()]+NEW_EVAL+s[m.end():]

    pat2=r'def longterm_eval_table\(x,market\):\n.*?(?=\ndef [A-Za-z_][A-Za-z0-9_]*\()'
    m2=re.search(pat2,s,re.S)
    if not m2:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: longterm_eval_table')
    s=s[:m2.start()]+NEW_TABLE+s[m2.end():]

    s=s.replace('class="v24-ver">v32</span>','class="v24-ver">v33</span>',1)
    APP.write_text(s)
    print('LONGTERM_WEEKLY_FALLBACK_V33_OK')

if __name__=='__main__':
    main()
