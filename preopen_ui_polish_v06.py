from pathlib import Path

APP=Path('app_v5.py')

def main():
    s=APP.read_text()

    # Remove the older duplicate runtime bar. The newer top header already owns mode controls.
    old="market=st.session_state['v5_market']\nruntime_mode_bar()\n"
    new="market=st.session_state['v5_market']\n"
    if old in s:
        s=s.replace(old,new,1)

    # Keep selected-engine detail compact by default. Users can expand when needed.
    s=s.replace("with st.expander('엔진 평가 요약',expanded=True):","with st.expander('엔진 평가 요약',expanded=False):",1)

    # Slightly tighter candidate table so the first fold shows more of holdings.
    s=s.replace("hide_index=True,height=225)","hide_index=True,height=205)",1)

    # Add a clear standby/live badge in the candidate panel without changing logic.
    live_marker="sub='후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.' if active else 'NORMAL/CLOSED 상태 · 마지막 TOP5 기록을 참고용으로 표시합니다. 실시간 추천이 아닙니다.'"
    if live_marker in s and "status_badge=" not in s:
        repl=live_marker+"\n        status_badge='● LIVE' if active else '● STANDBY'\n        st.caption(status_badge)"
        s=s.replace(live_marker,repl,1)

    APP.write_text(s)
    print('PREOPEN_UI_POLISH_V06_OK')

if __name__=='__main__':
    main()
