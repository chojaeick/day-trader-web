from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()
old="""                _budget=max(0.0,_cash*0.995)\n                qty=int(_budget//price)\n"""
new="""                _budget=max(0.0,min(_cash*0.995,1000000.0))\n                qty=int(_budget//price)\n"""
if new in s:
    print('V23 KR POSITION BUDGET 1M ALREADY CONNECTED')
elif old not in s:
    raise SystemExit('EXACT POSITION BUDGET BLOCK NOT FOUND - NOTHING CHANGED')
else:
    s=s.replace(old,new,1)
    p.write_text(s)
    print('V23 KR POSITION BUDGET 1M CONNECTED')
