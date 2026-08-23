from pathlib import Path
import re

APP=Path('app_v5.py')

def replace_once(s,pat,repl,label,flags=re.S):
    m=re.search(pat,s,flags)
    if not m: raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return s[:m.start()]+repl+s[m.end():]

def main():
    s=APP.read_text()

    if 'def quote_snapshot(' not in s:
        anchor="def position_rows():\n"
        helper=r'''def quote_snapshot(symbol):
    x=api(f'/api/quote/{str(symbol).upper()}',5)
    return x if isinstance(x,dict) and not x.get('error') else {}

def standby_candidates(status,market,limit=5):
    out=[]; seen=set()
    for e in (status.get('events') or []):
        if str(e.get('event_type') or '').upper()!='TOP5_IN': continue
        sym=str(e.get('symbol') or '').upper()
        if not sym or sym in seen: continue
        seen.add(sym)
        q=quote_snapshot(sym)
        out.append({
            'symbol':sym,
            'name':q.get('name') or sym,
            'price':q.get('price') or q.get('last') or q.get('close'),
            'power':None,
            'state':'STANDBY',
            'risk':'-',
            'prototype_action':'WATCH',
            'reason':'최근 TOP5 기록 · NORMAL 대기모드에서는 실시간 Tracker 계산을 중지합니다.',
            '_standby':True,
        })
        if len(out)>=limit: break
    return out

'''
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: quote helper anchor')
        s=s.replace(anchor,helper+anchor,1)

    rec=r'''def recommendation_table(rows,market,limit=5):
    out=[]
    for r in rows[:limit]:
        gate=r.get('entry_gate') or {}
        pv=r.get('power')
        ptxt='-' if pv is None else round(f(pv),1)
        out.append({
            '종목':r.get('symbol') or '-',
            '종목명':r.get('name') or r.get('symbol') or '-',
            '판단':action_ko(action_of(r)),
            '현재가':money(r.get('price') or r.get('current_price'),market),
            'Power':ptxt,
            '상태':r.get('state') or gate.get('signal_grade') or '-',
            '위험':r.get('risk') or r.get('risk_level') or '-'
        })
    return pd.DataFrame(out)

'''
    s=replace_once(s,r'def recommendation_table\(.*?\n(?=def normalize_position)',rec,'recommendation table')

    positions=r'''def render_positions(market,tracker):
    st.markdown('<div class="v5-section-title">🛡 보유주식 관리</div><div class="v5-section-sub">전체 폭 관리 · 단타/중장기 즉시 전환 · 검증된 종목만 등록</div>',unsafe_allow_html=True)
    render_manual_holding(market,'holdings')
    pos_rows,_=position_rows(); shown=0
    for raw in pos_rows:
        if str(raw.get('market') or '').upper() not in {'',market}: continue
        sym=raw.get('symbol') or (raw.get('position') or {}).get('symbol') or '-'
        live=next((r for r in tracker if str(r.get('symbol')).upper()==str(sym).upper()),None)
        quote=quote_snapshot(sym) if not live else {}
        p=normalize_position(raw,live or quote);shown+=1
        current_type=holding_profile(market,sym); pct='-' if p['pct'] is None else f"{p['pct']:+.2f}%"
        pnl_cls='v5-good' if (p['pnl'] or 0)>=0 else 'v5-bad'
        if live:
            judgment=action_ko(action_of(live))
        elif current_type=='LONG_TERM':
            judgment='중장기 평가대기'
        else:
            judgment='단타 대기'
        c0,c1,c2,c3,c4,c5,c6,c7=st.columns([1.1,.9,.9,.75,.82,1.05,1.05,.58])
        c0.markdown(f'**{sym}**  \n<span style="color:#8190a7;font-size:.7rem">{p["qty"]:,.0f}주</span>',unsafe_allow_html=True)
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
            if live:
                st.dataframe(engine_matrix(live),hide_index=True,use_container_width=True,height=220)
            else:
                msg='중장기 평가엔진 연결 대기' if current_type=='LONG_TERM' else 'DAYTRADE 활성화 시 단타 엔진 평가 재개'
                st.info(msg)
                st.dataframe(engine_matrix(None),hide_index=True,use_container_width=True,height=220)
        st.divider()
    if shown==0: st.info('등록된 실제 보유종목이 없습니다.')

'''
    s=replace_once(s,r'def render_positions\(.*?\n(?=def render_trading)',positions,'positions')

    trading=r'''def render_trading(market):
    status=get_market_status(market);rows=tracker_rows(status);finders=finder_rows(status);active=rows if rows else finders;session=status.get('session') or status.get('market_session') or '-'
    standby=standby_candidates(status,market,5) if not active else []
    source=active or standby
    a,b,c,d=st.columns(4)
    a.metric('시장','미국장' if market=='USA' else '국장');b.metric('세션',session)
    c.metric('후보',len(finders) if active else ('대기' if standby else 0));d.metric('관리',len(rows))
    left,right=st.columns([1.05,1.35],gap='large')
    with left:
        title='⚡ 지금 단타 후보 TOP 5' if active else '🕘 최근 단타 후보 TOP 5'
        sub='후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.' if active else 'NORMAL/CLOSED 상태 · 마지막 TOP5 기록을 참고용으로 표시합니다. 실시간 추천이 아닙니다.'
        st.markdown(f'<div class="v5-section-title">{title}</div><div class="v5-section-sub">{sub}</div>',unsafe_allow_html=True)
        if source:
            st.dataframe(recommendation_table(source,market),use_container_width=True,hide_index=True,height=225)
            labels=[];lookup={}
            for r in source[:5]:
                pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'
                label=f"{r.get('symbol') or '-'} · {action_ko(action_of(r))} · Power {ptxt}";labels.append(label);lookup[label]=r
            sel=st.selectbox('후보 선택',labels,key=f'sel_{market}',label_visibility='collapsed')
            selected=lookup[sel]
        else:
            st.info('현재 추천/Tracker 데이터가 없습니다. DAYTRADE를 켜거나 장 시작 후 갱신을 기다려주세요.')
            selected=None
    with right:
        if selected:
            render_selected_detail(selected,market)
        else:
            st.markdown('<div class="v5-section"><b>선택 종목 없음</b><div class="v5-section-sub">후보가 생성되면 상세 평가가 이 영역에 표시됩니다.</div></div>',unsafe_allow_html=True)
    st.divider()
    render_positions(market,rows)

'''
    s=replace_once(s,r'def render_trading\(.*?\n(?=def render_portfolio)',trading,'trading')

    APP.write_text(s)
    print('PREOPEN_UI_STANDBY_FIX_V05_OK')

if __name__=='__main__':
    main()
