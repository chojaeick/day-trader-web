#!/usr/bin/env python3
import subprocess

print('=== V235A DIAGNOSE API NOT READY AFTER V235 ===')
print('NO_CODE_CHANGE=YES NO_RESTART=YES')

cmd=['sudo','journalctl','-u','day-trader-api','-n','220','--no-pager']
r=subprocess.run(cmd,capture_output=True,text=True)
print('JOURNAL_RC=',r.returncode)
lines=(r.stdout or '').splitlines()
keys=('Traceback','ERROR','Exception','SyntaxError','NameError','AttributeError','TypeError','ImportError','Application startup','Uvicorn','Started server process','Failed to start','code=exited','status=')
hits=[ln for ln in lines if any(k in ln for k in keys)]
print('=== FILTERED_LAST_ERRORS ===')
for ln in hits[-80:]:
    print(ln)
print('=== SERVICE ===')
subprocess.run(['systemctl','is-active','day-trader-api'])
print('=== PORT8000 ===')
subprocess.run(['bash','-lc',"ss -ltnp | grep ':8000' || true"])
