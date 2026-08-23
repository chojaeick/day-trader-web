from pathlib import Path
import re

APP=Path('app_v5.py')

NEW=r'''# FUJIMOTO_LIVE_V56: current Fujimoto tracker output is authoritative for the UI.
@st.cache_data(ttl=5,show_spinner=False)
def fujimoto_live_rows_korea():
    x=api('/api/v5/fujimoto-auto-v4/KOREA',5)
    return x.get('rows') or [] if isinstance(x,dict) else []

def fujimoto_live_for_symbol(symbol):
    sym=str(symbol or '').upper().strip()
    if not sym:return {}
    for r in fujimoto_live_rows_korea():
        if str(r.get('symbol') or '').upper().strip()==sym:
            return r
    return {}

def _fujimoto_judgment(row):
    state=str((row or {}).get('engine_state') or '').upper()
    signal=str((row or {}).get('signal') or '').upper()
    if signal=='ENTRY': return '진입'
    if signal=='ENTRY_CANDIDATE': return '진입 후보'
    if state=='ENTRY_READY' or signal=='READY': return '진입 준비'
    if state=='PREPARE': return '준비'
    if state=='HOLD': return '보유'
    if state=='PARTIAL_EXIT' or signal=='PARTIAL_EXIT': return '부분청산 검토'
    if state=='EXIT' or signal=='EXIT': return '청산 검토'
    if state=='WATCH': return '관찰'
    return signal or state or '데이터 대기'

def engine_matrix(live):
    cp=f((live or {}).get('power'),None) if live else None
    symbol=str((live or {}).get('symbol') or '').upper().strip()
    fu=fujimoto_live_for_symbol(symbol) if symbol else {}
    fs=fu.get('fujimoto_score')
    fstate=fu.get('engine_state') or '대기'
    faction=_fujimoto_judgment(fu) if fu else '데이터 대기'
    frisk='-' if not fu else ('POSITION' if fu.get('position_open') else 'SIGNAL_ONLY')
    return pd.DataFrame([
        {'엔진':'Core','상태':'LIVE' if live else '대기','점수':f'{cp:+.1f}' if cp is not None else '-','판단':action_ko(action_of(live)) if live else '데이터 대기','위험':(live or {}).get('risk') or (live or {}).get('risk_level') or '-'},
        {'엔진':'Fujimoto','상태':fstate,'점수':(f'{int(fs)}/100' if fs is not None else '-'),'판단':faction,'위험':frisk},
        {'엔진':'MA20','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Ethan','상태':'검증중','점수':'-','판단':'V-zone 재현 대기','위험':'-'},
        {'엔진':'Jared 3/4','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Predator','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'}])

'''

def main():
    s=APP.read_text()
    pat=r"# FUJIMOTO_V01_REJECT:.*?\ndef engine_matrix\(live\):.*?(?=\ndef render_manual_holding\()"
    ns,n=re.subn(pat,NEW,s,flags=re.S)
    if n!=1:
        raise SystemExit(f'PATCH_TARGET_COUNT={n}')
    APP.write_text(ns)
    print('UI_FUJIMOTO_LIVE_V56_FIX_OK')

if __name__=='__main__': main()
