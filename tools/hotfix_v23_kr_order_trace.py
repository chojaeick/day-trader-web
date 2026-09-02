from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()

needle="""            _v22_decision=_v22_kr_entry(row)\n            row['engine5_v22_decision']=_v22_decision\n            entry=bool(_v22_decision.get('enter'))\n            exit_ready=bool(row.get(\"williams_exit_ready\"))\n"""
repl="""            _v22_decision=_v22_kr_entry(row)\n            row['engine5_v22_decision']=_v22_decision\n            entry=bool(_v22_decision.get('enter'))\n            import logging as _logging\n            _logging.warning('V23_KR_ORDER_DECISION sym=%s enter=%s score=%s reason=%s session=%s in_pos=%s', sym, entry, _v22_decision.get('score'), _v22_decision.get('reason') or _v22_decision.get('entry_reason') or _v22_decision.get('reasons'), row.get('session'), in_pos)\n            exit_ready=bool(row.get(\"williams_exit_ready\"))\n"""

if 'V23_KR_ORDER_DECISION sym=%s' in s:
    print('V23 KR ORDER TRACE ALREADY CONNECTED')
    raise SystemExit(0)
if needle not in s:
    raise SystemExit('V23 DECISION TARGET NOT FOUND - NOTHING CHANGED')
s=s.replace(needle,repl,1)
p.write_text(s)
print('V23 KR ORDER TRACE CONNECTED')
