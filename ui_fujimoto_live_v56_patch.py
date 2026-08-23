from pathlib import Path

P=Path('app_v5.py')

OLD="""# FUJIMOTO_V01_REJECT: 369 trades @ cost 0.20%, WR 20.33%, PF 0.384, NET -73.402%. Informational only; excluded from aggregate vote.
def engine_matrix(live):
    cp=f((live or {}).get('power'),None) if live else None
    return pd.DataFrame([
        {'엔진':'Core','상태':'LIVE' if live else '대기','점수':f'{cp:+.1f}' if cp is not None else '-','판단':action_ko(action_of(live)) if live else '데이터 대기','위험':(live or {}).get('risk') or (live or {}).get('risk_level') or '-'},
        {'엔진':'Fujimoto','상태':'검증완료','점수':'PF 0.384','판단':'비채택 · v0.1','위험':'REJECT'},
        {'엔진':'MA20','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Ethan','상태':'검증중','점수':'-','판단':'V-zone 재현 대기','위험':'-'},
        {'엔진':'Jared 3/4','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Predator','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'}])
"""

NEW="""@st.cache_data(ttl=5,show_spinner=False)
def fujimoto_live_map():
    x=api('/api/v5/fujimoto-auto-v4/KOREA',8)
    if not isinstance(x,dict) or not x.get('ok'):
        return {}
    out={}
    for r in x.get('rows') or []:
        sym=str(r.get('symbol') or '').upper().strip()
        if sym: out[sym]=r
    return out


def _fujimoto_judgment(fr):
    state=str((fr or {}).get('engine_state') or '').upper()
    signal=str((fr or {}).get('signal') or '').upper()
    if signal=='ENTRY_CANDIDATE' or state=='ENTRY': return '진입 후보'
    if state=='ENTRY_READY' or signal=='READY': return '진입 준비'
    if state=='PREPARE': return '준비/관찰'
    if state=='HOLD': return '보유 유지'
    if state=='PARTIAL_EXIT': return '부분청산 검토'
    if state=='EXIT': return '청산 검토'
    if state in ('WATCH','NOT_EVALUATED'): return '관찰' if state=='WATCH' else '평가 대기'
    return signal or state or '데이터 대기'


def engine_matrix(live):
    cp=f((live or {}).get('power'),None) if live else None
    sym=str((live or {}).get('symbol') or '').upper().strip()
    fr=fujimoto_live_map().get(sym) if sym else None
    fs=(fr or {}).get('fujimoto_score')
    fstate=str((fr or {}).get('engine_state') or 'NOT_EVALUATED')
    factual='-' if fs is None else f'{float(fs):.0f}/100'
    risk='POSITION' if (fr or {}).get('position_open') else ('ACTIONABLE' if (fr or {}).get('actionable') else '-')
    return pd.DataFrame([
        {'엔진':'Core','상태':'LIVE' if live else '대기','점수':f'{cp:+.1f}' if cp is not None else '-','판단':action_ko(action_of(live)) if live else '데이터 대기','위험':(live or {}).get('risk') or (live or {}).get('risk_level') or '-'},
        {'엔진':'Fujimoto','상태':fstate,'점수':factual,'판단':_fujimoto_judgment(fr),'위험':risk},
        {'엔진':'MA20','상태':'진단값','점수':('-' if not fr or fr.get('ma20') is None else f\"{float(fr.get('ma20')):,.0f}\"),'판단':'Fujimoto 보조 컨텍스트','위험':'-'},
        {'엔진':'Ethan','상태':'검증중','점수':'-','판단':'V-zone 재현 대기','위험':'-'},
        {'엔진':'Jared 3/4','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'},
        {'엔진':'Predator','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'}])
"""

def main():
    s=P.read_text()
    if 'def fujimoto_live_map()' in s:
        print('UI_FUJIMOTO_LIVE_V56_ALREADY_APPLIED'); return
    if OLD not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: old Fujimoto reject matrix')
    P.write_text(s.replace(OLD,NEW,1))
    print('UI_FUJIMOTO_LIVE_V56_PATCH_OK')

if __name__=='__main__': main()
