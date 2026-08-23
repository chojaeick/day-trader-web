from pathlib import Path

APP = Path('app_v5.py')


def must_replace(s, old, new, label):
    if old not in s:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return s.replace(old, new, 1)


def main():
    s = APP.read_text()

    # Avoid StreamlitDuplicateElementKey because all tab bodies execute even when hidden.
    s = s.replace("    render_manual_holding(market)\n\ndef render_briefing", "    render_manual_holding(market,'portfolio')\n\ndef render_briefing")

    # Lightweight runtime helpers. Connection/streaming remain always on; only heavy analysis mode changes.
    anchor = "def get_market_status(market):return api(f'/api/v4/{market}/status',15)\n"
    if 'def get_runtime_mode()' not in s:
        helper = """def get_runtime_mode():
    return api('/api/v4/runtime-mode',5)

def set_runtime_mode(mode):
    return post(f'/api/v4/runtime-mode/{str(mode).upper()}',{},5)

"""
        s = must_replace(s, anchor, helper + anchor, 'runtime helpers')

    old_header = """st.markdown('<div class=\"v5-title\">📈 DAY TRADER V5</div><div class=\"v5-sub\">DECISION TERMINAL · 무엇을 살지 → 얼마를 살지 → 어떻게 관리할지 · MANUAL ORDER</div>',unsafe_allow_html=True)
if 'v5_market' not in st.session_state:st.session_state['v5_market']='USA'
mc1,mc2,mc3=st.columns([.72,.72,4.5]);usa=mc1.button('🇺🇸 미국장',use_container_width=True,type='primary' if st.session_state['v5_market']=='USA' else 'secondary');kor=mc2.button('🇰🇷 국장',use_container_width=True,type='primary' if st.session_state['v5_market']=='KOREA' else 'secondary')
if usa:st.session_state['v5_market']='USA';st.rerun()
if kor:st.session_state['v5_market']='KOREA';st.rerun()
market=st.session_state['v5_market']
"""

    new_header = """st.title('DAY TRADER V5')
st.caption('DECISION TERMINAL · MANUAL ORDER · 실시간 연결은 항상 유지, 단타 분석만 필요할 때 가속')
if 'v5_market' not in st.session_state:
    st.session_state['v5_market']='USA'

# Market selector + runtime mode are deliberately always visible above tabs.
mc1,mc2,mc3,mc4,mc5=st.columns([.8,.8,1.25,1.25,2.6])
usa=mc1.button('🇺🇸 미국장',use_container_width=True,type='primary' if st.session_state['v5_market']=='USA' else 'secondary')
kor=mc2.button('🇰🇷 국장',use_container_width=True,type='primary' if st.session_state['v5_market']=='KOREA' else 'secondary')
if usa:
    st.session_state['v5_market']='USA'
    st.rerun()
if kor:
    st.session_state['v5_market']='KOREA'
    st.rerun()
market=st.session_state['v5_market']

rt=get_runtime_mode()
rt_mode=str(rt.get('mode') or 'UNKNOWN').upper()
normal=mc3.button('NORMAL 대기',use_container_width=True,type='primary' if rt_mode=='NORMAL' else 'secondary')
daytrade=mc4.button('⚡ DAYTRADE',use_container_width=True,type='primary' if rt_mode=='DAYTRADE' else 'secondary')
if normal and rt_mode!='NORMAL':
    rr=set_runtime_mode('NORMAL')
    if rr.get('ok'): st.rerun()
    else: st.error(f'NORMAL 전환 실패: {rr}')
if daytrade and rt_mode!='DAYTRADE':
    rr=set_runtime_mode('DAYTRADE')
    if rr.get('ok'): st.rerun()
    else: st.error(f'DAYTRADE 전환 실패: {rr}')

streaming=rt.get('streaming') or '-'
tracker_sec=rt.get('tracker_seconds') or '-'
finder_sec=rt.get('finder_seconds') or '-'
mc5.markdown(f'**MODE {rt_mode}**  \\nStreaming `{streaming}` · Tracker `{tracker_sec}s` · Finder `{finder_sec}s`')
if rt_mode=='NORMAL':
    st.caption('🟢 NORMAL: 키움/WS/시세 연결 유지 · 무거운 단타 Finder/Tracker 계산 대기')
elif rt_mode=='DAYTRADE':
    st.warning('⚡ DAYTRADE ON: 고속 단타 분석 활성화. 실제 단타가 끝나면 NORMAL로 복귀하세요.')
else:
    st.warning('런타임 모드 확인 실패. API 상태를 확인하세요.')
"""

    if old_header in s:
        s = s.replace(old_header, new_header, 1)
    elif "st.title('DAY TRADER V5')" not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: app header')

    APP.write_text(s)
    print('PREOPEN_UI_FINALIZE_PATCH_V03_OK')


if __name__ == '__main__':
    main()
