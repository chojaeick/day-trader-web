from pathlib import Path
import py_compile, os, time
APP=Path('/home/ubuntu/day-trader-api/app_v5.py')
print('=== CURRENT APP 1260-1310 ===')
if APP.exists():
    lines=APP.read_text(encoding='utf-8',errors='replace').splitlines()
    for i in range(1259,min(len(lines),1310)):
        print(f'{i+1}: {lines[i]}')
print('=== BACKUPS ===')
for p in sorted(APP.parent.glob('app_v5.py*'), key=lambda x:x.stat().st_mtime, reverse=True):
    if p==APP: continue
    status='UNKNOWN'
    try:
        py_compile.compile(str(p), doraise=True)
        status='COMPILE_PASS'
    except Exception as e:
        status='COMPILE_FAIL:'+repr(e)
    st=p.stat()
    print(f'{p.name}\tmtime={int(st.st_mtime)}\tsize={st.st_size}\t{status}')
print('READ_ONLY=YES')
print('SERVICE_RESTART=NO')
print('ORDER_SENT=NO')
