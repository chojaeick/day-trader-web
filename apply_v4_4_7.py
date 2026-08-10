from pathlib import Path
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")
text = path.read_text(encoding="utf-8")

old = """    st.markdown('### 🎯 Finder TOP5 · 오늘 볼 종목'); st.caption('Finder는 종목 선정용입니다. 위 Power 순위는 진입 준비도를 실시간으로 다시 정렬합니다. TOP5 진입 = 즉시 매수가 아닙니다.')
    if fr:st.dataframe(pd.DataFrame([{'순위':r.get('rank'),'종목':r.get('symbol'),'종목명':r.get('name'),'Finder점수':r.get('finder_score'),'방향':r.get('direction'),'등락률%':r.get('change_pct'),'RVOL':r.get('rvol'),'ATR%':r.get('atr_pct'),'위험':rko(r.get('risk'))} for r in fr]),use_container_width=True,hide_index=True)
"""

new = """    st.markdown('### 🎯 Finder TOP5 · 오늘 볼 종목')
    st.caption('Finder는 종목 선정용입니다. 위 Power 순위는 진입 준비도를 실시간으로 다시 정렬합니다. TOP5 진입 = 즉시 매수가 아닙니다.')

    if m=='USA':
        regime=fi.get('market_regime') or 'UNKNOWN'
        pref=fi.get('preferred_direction') or '-'
        light_rows=fi.get('light_rows') or []
        e1,e2,e3,e4=st.columns(4)
        e1.metric('시장 레짐',regime)
        e2.metric('우선 방향',pref)
        e3.metric('Light Tracker',fi.get('light_count',len(light_rows)))
        e4.metric('Finder 회전',f"{fi.get('rotation_seconds',30)}초")
        st.caption('Finder점수는 확률이 아니라 후보 우선순위 점수입니다. 실제 진입은 위 Power / 5분 Setup / 1분 Trigger / 추격방지를 별도로 통과해야 합니다.')

    if fr:
        st.dataframe(pd.DataFrame([{
            '순위':r.get('rank'),
            '종목':r.get('symbol'),
            '종목명':r.get('name'),
            'Finder점수':r.get('finder_score'),
            '방향':r.get('direction'),
            '등락률%':r.get('change_pct'),
            '1m%':r.get('ret_1m'),
            '3m%':r.get('ret_3m'),
            '5m%':r.get('ret_5m'),
            'Fresh':r.get('fresh_mode') or '-',
            'Fresh점수':r.get('fresh_score'),
            'RVOL':r.get('rvol'),
            'Vol가속':r.get('volume_accel'),
            'Power참조':r.get('observed_power'),
            'Fade감점':r.get('fade_penalty'),
            '위험':rko(r.get('risk'))
        } for r in fr]),use_container_width=True,hide_index=True)

    if m=='USA':
        light_rows=fi.get('light_rows') or []
        with st.expander(f"🔎 Light Tracker {len(light_rows)} · 점수 근거 보기"):
            if light_rows:
                explain=[]
                for x in light_rows:
                    mode=x.get('fresh_mode') or 'WATCH'
                    if x.get('extreme_watch'):
                        tag='EXTREME'
                    elif x.get('fresh_mover'):
                        tag=mode
                    elif f(x.get('fade_penalty'))>0:
                        tag='FADING'
                    else:
                        tag='WATCH'
                    explain.append({
                        'Light순위':x.get('light_rank'),
                        '종목':x.get('symbol'),
                        '점수':x.get('finder_score'),
                        '상태':tag,
                        '당일%':x.get('change_pct'),
                        '1m%':x.get('ret_1m'),
                        '3m%':x.get('ret_3m'),
                        '5m%':x.get('ret_5m'),
                        '15m%':x.get('ret_15m'),
                        'Vol가속':x.get('volume_accel'),
                        'Vol커버':x.get('volume_coverage_10m'),
                        '3분고점돌파':'Y' if x.get('break_3m_high') else '',
                        'Fresh점수':x.get('fresh_score'),
                        'Power참조':x.get('observed_power'),
                        'Fade감점':x.get('fade_penalty'),
                        '품질':x.get('quality'),
                        '위험':rko(x.get('risk')),
                        '선정근거':x.get('finder_reason')
                    })
                st.dataframe(pd.DataFrame(explain),use_container_width=True,hide_index=True)
                fresh=[x for x in light_rows if x.get('fresh_mover')]
                fading=[x for x in light_rows if f(x.get('fade_penalty'))>0]
                extreme=[x for x in light_rows if x.get('extreme_watch')]
                c1,c2,c3=st.columns(3)
                c1.metric('Fresh 감지',len(fresh))
                c2.metric('Fade 감지',len(fading))
                c3.metric('Extreme 관찰',len(extreme))
                if fresh:
                    st.success('지금 가속 감지 · '+', '.join(
                        f"{x.get('symbol')}({x.get('fresh_mode')})" for x in fresh[:6]
                    ))
                else:
                    st.caption('현재 Light Tracker에는 CONTINUATION/BREAKOUT 조건을 모두 충족한 Fresh 종목이 없습니다.')
            else:
                st.caption('Light Tracker 데이터 준비 중')
"""

if old not in text:
    raise SystemExit(
        "PATCH ABORTED: expected Finder TOP5 block not found. "
        "app.py was not changed."
    )

text2 = text.replace(old, new, 1)

# UI version label only; do not alter engine/version semantics.
old_footer = "st.divider(); st.caption('V4.0 CLEAN ENGINE ALPHA · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY · NO AUTO ORDER')"
new_footer = "st.divider(); st.caption('V4.4.7 UI · BROAD FINDER + LIGHT20 + FRESH EXPLAINABILITY · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY')"
if old_footer in text2:
    text2 = text2.replace(old_footer, new_footer, 1)

path.write_text(text2, encoding="utf-8")
print(f"PATCHED: {path}")
