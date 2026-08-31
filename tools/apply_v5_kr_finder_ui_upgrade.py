from __future__ import annotations

"""Patch the currently deployed/local app_v5.py without replacing unrelated local edits.

KR UI goals
- Show Finder as the candidate-discovery layer, not Tracker Power.
- Display up to 20 KR Finder candidates.
- Show Finder score / rise / trading value / risk in the candidate table.
- Keep Tracker/V22 detail available for the selected symbol when present.
- Preserve all backend V22 order authority and allocator behavior.
"""

from pathlib import Path
import py_compile
import re
import shutil
import subprocess
import time
import urllib.request

APP = Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
BACKUP = Path('/home/ubuntu/day-trader-api-repo/app_v5.py.pre_kr_finder_ui_upgrade')
LOG = Path('/tmp/daytrader-v5.log')
PORT = 8503


def replace_function(text: str, name: str, new_body: str) -> str:
    pat = re.compile(rf'^def {re.escape(name)}\(.*?(?=^def |^@st\.|\Z)', re.M | re.S)
    m = pat.search(text)
    if not m:
        raise SystemExit(f'ABORT function not found: {name}')
    return text[:m.start()] + new_body.rstrip() + '\n\n' + text[m.end():]


RECOMMENDATION_TABLE = r'''def recommendation_table(rows,market,limit=None):
    rows=enrich_display_names(rows,market) if 'enrich_display_names' in globals() else rows
    if limit is None:
        limit=20 if market=='KOREA' else 5
    out=[]
    for r in rows[:limit]:
        gate=r.get('entry_gate') or {}
        sym=str(r.get('symbol') or '-')
        name=resolve_display_name(market,sym,r.get('name') or '') if 'resolve_display_name' in globals() else (r.get('name') or sym)
        if market=='KOREA':
            score=r.get('finder_score')
            chg=r.get('change_pct')
            value=r.get('dollar_volume') or r.get('trading_value')
            risk=str(r.get('risk') or r.get('risk_level') or '-')
            reason=str(r.get('finder_reason') or '')
            signal='거래량' if 'volume' in reason.lower() and f(r.get('rvol'))>=1.5 else '급등' if f(chg)>=4 else '이벤트' if str(r.get('quality') or '')=='B_EVENT' else '상승신호'
            out.append({
                '순위':r.get('rank') or '-',
                '종목':name,
                '코드':sym,
                '현재가':money(r.get('price') or r.get('current_price'),market),
                'Finder':('-' if score is None else round(f(score),1)),
                '등락률':('-' if chg is None else f'{f(chg):+.2f}%'),
                '거래대금':('-' if value is None else f'{f(value)/100_000_000:,.0f}억'),
                '신호':signal,
                '위험':risk,
            })
        else:
            out.append({
                '종목':f'{name}  ·  {sym}',
                '판단':action_ko(action_of(r)),
                '현재가':money(r.get('price') or r.get('current_price'),market),
                'Power':('-' if r.get('power') is None else round(f(r.get('power')),1)),
                '상태':r.get('state') or gate.get('signal_grade') or '-',
                '위험':r.get('risk') or r.get('risk_level') or '-'
            })
    return pd.DataFrame(out)'''


SELECTED_DETAIL = r'''def render_selected_detail(r,market):
    symbol=r.get('symbol') or '-';name=resolve_display_name(market,symbol,r.get('name') or '');reason=r.get('prototype_reason') or r.get('reason') or r.get('finder_reason') or r.get('core_reason') or '엔진 판단 근거 대기'
    st.markdown('<div class="v5-section-title">🎯 선택 종목 상세</div>',unsafe_allow_html=True)
    if market=='KOREA':
        a,b,c,d=st.columns(4)
        a.metric('종목',name)
        b.metric('현재가',money(r.get('price') or r.get('current_price'),market))
        fs=r.get('finder_score')
        c.metric('Finder 점수','-' if fs is None else f'{f(fs):.1f}')
        v22=(r.get('engine5_v22_decision') or {})
        if v22:
            d.metric('V22',f"{f(v22.get('effective_score') or v22.get('score')):.1f}")
        elif r.get('power') is not None:
            d.metric('Tracker Power',f"{f(r.get('power')):+.1f}")
        else:
            d.metric('V22','평가 대기')
    else:
        a,b,c,d=st.columns(4)
        a.metric('종목',name);b.metric('현재가',money(r.get('price') or r.get('current_price'),market));c.metric('Power',f"{f(r.get('power')):+.1f}");d.metric('판단',action_ko(action_of(r)))
    st.markdown(f'<div class="v5-section"><b>{name}</b><div class="v5-section-sub">{reason}</div></div>',unsafe_allow_html=True)
    with st.expander('엔진 평가 요약',expanded=False):
        st.dataframe(engine_matrix(r),use_container_width=True,hide_index=True,height=245)
    with st.expander('매수 계산 / 보유등록',expanded=False): render_buy_box(r,market)'''


RENDER_TRADING = r'''def render_trading(market):
    status=get_market_status(market);rows=tracker_rows(status);finders=finder_rows(status);session=status.get('session') or status.get('market_session') or '-'
    finder_limit=20 if market=='KOREA' else 5
    standby=standby_candidates(status,market,finder_limit) if not finders and not rows else []
    # Finder is the discovery panel. Tracker is the downstream management/evaluation layer.
    source=(finders[:finder_limit] if finders else (rows[:finder_limit] if rows else standby[:finder_limit]))
    a,b,c,d=st.columns(4)
    a.metric('시장','미국장' if market=='USA' else '국장');b.metric('세션',session)
    c.metric('Finder 후보',len(finders) if finders else ('대기' if standby else 0));d.metric('Tracker 관리',len(rows))
    left,right=st.columns([1.28,1.12],gap='large')
    with left:
        if market=='KOREA':
            title='🚀 Finder 상승 후보 TOP 20' if source else '🚀 Finder 상승 후보'
            sub='좋은 신호 · 급등 · 거래대금/거래량 · 이벤트 품질로 발굴한 롱 후보입니다. 실제 주문은 V22가 결정합니다.'
        else:
            title='⚡ 지금 단타 후보 TOP 5' if source else '🕘 최근 단타 후보 TOP 5'
            sub='후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.'
        status_badge='● LIVE' if (finders or rows) else '● STANDBY'
        st.caption(status_badge)
        st.markdown(f'<div class="v5-section-title">{title}</div><div class="v5-section-sub">{sub}</div>',unsafe_allow_html=True)
        if source:
            st.dataframe(recommendation_table(source,market,finder_limit),use_container_width=True,hide_index=True,height=430 if market=='KOREA' else 205)
            labels=[];lookup={}
            for r in source[:finder_limit]:
                if market=='KOREA':
                    fs=r.get('finder_score'); score='-' if fs is None else f'{f(fs):.1f}'
                    chg=r.get('change_pct'); chgtxt='-' if chg is None else f'{f(chg):+.2f}%'
                    label=f"#{r.get('rank') or '-'} {r.get('name') or r.get('symbol') or '-'} · Finder {score} · {chgtxt}"
                else:
                    pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'
                    label=f"{r.get('symbol') or '-'} · {action_ko(action_of(r))} · Power {ptxt}"
                labels.append(label);lookup[label]=r
            sel=st.selectbox('후보 선택',labels,key=f'sel_{market}',label_visibility='collapsed')
            selected=lookup[sel]
            # Merge downstream Tracker/V22 telemetry for the selected Finder symbol.
            live=next((x for x in rows if str(x.get('symbol') or '').upper()==str(selected.get('symbol') or '').upper()),None)
            if live:
                merged=dict(selected);merged.update(live)
                for k in ('finder_score','finder_reason','rank','change_pct','dollar_volume','quality'):
                    if selected.get(k) is not None: merged[k]=selected.get(k)
                selected=merged
        else:
            st.info('현재 Finder/Tracker 데이터가 없습니다. DAYTRADE를 켜거나 장 시작 후 갱신을 기다려주세요.')
            selected=None
    with right:
        if selected:
            render_selected_detail(selected,market)
        else:
            st.markdown('<div class="v5-section"><b>선택 종목 없음</b><div class="v5-section-sub">Finder 후보가 생성되면 상세 평가가 이 영역에 표시됩니다.</div></div>',unsafe_allow_html=True)
    st.divider()
    render_positions(market,rows)'''


def main():
    if not APP.exists():
        raise SystemExit(f'ABORT missing {APP}')
    text=APP.read_text(encoding='utf-8')
    if not BACKUP.exists():
        shutil.copy2(APP,BACKUP)
        print('BACKUP',BACKUP,flush=True)

    text=replace_function(text,'recommendation_table',RECOMMENDATION_TABLE)
    text=replace_function(text,'render_selected_detail',SELECTED_DETAIL)
    text=replace_function(text,'render_trading',RENDER_TRADING)

    marker='# V22_KR_FINDER_UI_TOP20'
    if marker not in text:
        text=marker+'\n'+text
    APP.write_text(text,encoding='utf-8')
    py_compile.compile(str(APP),doraise=True)
    print('APP_V5_PATCH=PASS',flush=True)

    # Restart only the Streamlit V5 process. API/day-trader order engine is untouched.
    subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False)
    time.sleep(1)
    cmd=(f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 '
         f'nohup /home/ubuntu/day-trader-api/venv/bin/python -m streamlit run app_v5.py '
         f'--server.address=0.0.0.0 --server.port={PORT} --server.headless=true '
         f'> {LOG} 2>&1 &')
    subprocess.Popen(['bash','-lc',cmd],start_new_session=True)

    deadline=time.time()+45
    last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=2) as r:
                if r.status==200:
                    print('V5_HTTP=PASS',flush=True)
                    break
        except Exception as e:
            last=e
        time.sleep(2)
    else:
        raise SystemExit(f'ABORT V5 did not come up: {last}; log={LOG}')

    print('KR_FINDER_UI=TOP20',flush=True)
    print('KR_FINDER_COLUMNS=SCORE_CHANGE_TRADING_VALUE_SIGNAL_RISK',flush=True)
    print('NEGATIVE_POWER_REMOVED_FROM_FINDER_PANEL=YES',flush=True)
    print('TRACKER_V22_DETAIL=MERGED_ON_SELECTION',flush=True)
    print('BACKEND_ORDER_ENGINE=UNTOUCHED',flush=True)
    print('DEPLOY=PASS',flush=True)


if __name__=='__main__':
    main()
