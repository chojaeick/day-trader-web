#!/usr/bin/env python3
import os, sys, subprocess
from pathlib import Path

print('=== V187B RUN LIVE SYMBOL PROBE IN VENV ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
repo=Path.home()/'day-trader-api-repo'
venv=Path('/home/ubuntu/day-trader-api/venv/bin/python3')
target=repo/'tools/v187_probe_kiwoom_live_symbol_code_from_rankings.py'
print('VENV_PY=',venv,'EXISTS=',venv.exists())
print('TARGET=',target,'EXISTS=',target.exists())
if not venv.exists():
    print('VENV_PY_MISSING'); sys.exit(2)
if not target.exists():
    print('TARGET_MISSING'); sys.exit(3)
cp=subprocess.run([str(venv),str(target)],cwd=str(repo),text=True)
print('V187B_CHILD_RC=',cp.returncode)
sys.exit(cp.returncode)
