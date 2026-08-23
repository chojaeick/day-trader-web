from pathlib import Path
import re

APP=Path('app_v5.py')

GUARD=r'''

# ===== UI LONGTERM MA10 SAFE GUARD V57 =====
# Some local UI revisions reference longterm_ma10_eval before the MA10 engine
# implementation is present.  Keep the page alive and report an explicit
# non-evaluated state instead of raising NameError.
if 'longterm_ma10_eval' not in globals():
    def longterm_ma10_eval(*args, **kwargs):
        symbol='-'
        market='KOREA'
        live=None
        for a in args:
            if isinstance(a,dict):
                live=a
                if a.get('symbol'): symbol=str(a.get('symbol'))
                if a.get('market'): market=str(a.get('market')).upper()
            elif isinstance(a,str):
                s=a.strip()
                if s.upper() in ('KOREA','USA'): market=s.upper()
                elif s: symbol=s
        live=live or {}
        # Preserve any MA10 fields already supplied by upstream code, but do
        # not invent a trading opinion when there is no MA10 engine result.
        score=live.get('ma10_score')
        state=live.get('ma10_state') or 'NOT_CONNECTED'
        signal=live.get('ma10_signal') or 'WAIT'
        reason=live.get('ma10_reason') or 'MA10 엔진 연결 대기'
        return {
            'ok':False,
            'available':False,
            'engine':'MA10',
            'symbol':symbol,
            'market':market,
            'state':state,
            'status':state,
            'score':score,
            'signal':signal,
            'action':'WAIT',
            'judgment':'연결 대기',
            'reason':reason,
            'risk':'-',
            'ma10':live.get('ma10'),
            'price':live.get('price') or live.get('current_price'),
        }
'''

def main():
    s=APP.read_text()
    # A real implementation wins. Only add the guard when the name is used
    # but no function definition exists.
    if re.search(r'^\s*def\s+longterm_ma10_eval\s*\(',s,re.M):
        print('UI_LONGTERM_MA10_GUARD_V57_ALREADY_DEFINED')
        return
    if 'longterm_ma10_eval' not in s:
        print('UI_LONGTERM_MA10_GUARD_V57_NOT_REFERENCED')
        return
    anchor='def engine_matrix('
    pos=s.find(anchor)
    if pos<0:
        # Insert before the first render function as a safe fallback.
        m=re.search(r'^def\s+render_',s,re.M)
        if not m: raise SystemExit('PATCH_TARGET_NOT_FOUND: engine/render anchor')
        pos=m.start()
    s=s[:pos]+GUARD+'\n'+s[pos:]
    APP.write_text(s)
    print('UI_LONGTERM_MA10_GUARD_V57_OK')

if __name__=='__main__':
    main()
