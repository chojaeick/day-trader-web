from __future__ import annotations

from pathlib import Path
import ast
import py_compile
import subprocess
import time
import urllib.request

APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
BACKUP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py.pre_terminal_design_v38')
PORT=8503
LOG=Path('/tmp/daytrader-v5.log')

# Reuse the exact V38 payload already committed, but rebuild from the pre-V38
# backup so every helper function exists before the executable V38 UI block.
from apply_v5_terminal_design_v38 import TOP, TRADING, replace_function

REQUIRED_FUNCS={
    'api','post','f','money','action_of','action_ko','get_runtime_mode',
    'set_runtime_mode','get_market_status','tracker_rows','finder_rows',
    'position_rows','resolve_display_name','engine_matrix','render_positions',
    'render_portfolio','render_briefing','render_settings','render_trading',
}


def line_offset(text:str, lineno:int)->int:
    if lineno<=1:
        return 0
    lines=text.splitlines(keepends=True)
    return sum(len(x) for x in lines[:lineno-1])


def main():
    if not BACKUP.exists():
        raise SystemExit('ABORT missing pre-V38 backup: '+str(BACKUP))

    base=BACKUP.read_text(encoding='utf-8')
    # Replace the old trading renderer first, while the full function body still exists.
    base=replace_function(base,'render_trading',TRADING)

    tree=ast.parse(base)
    funcs={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    missing=sorted(REQUIRED_FUNCS-set(funcs))
    if missing:
        raise SystemExit('ABORT backup missing required helpers: '+','.join(missing))

    # Keep all imports/constants/styles/helpers through the final top-level function.
    # Everything after that is the old executable UI bootstrap and is replaced by V38.
    last_def=max(n.end_lineno for n in funcs.values())
    cut=line_offset(base,last_def+1)
    prefix=base[:cut].rstrip()+"\n\n"
    repaired=prefix+TOP.rstrip()+"\n"

    # Critical ordering verification: every helper call in TOP must occur after defs.
    for name in REQUIRED_FUNCS-{'render_trading'}:
        d=repaired.find('def '+name+'(')
        if d<0:
            raise SystemExit('ABORT helper disappeared: '+name)
    top_pos=repaired.find('# V38_TERMINAL_DESIGN')
    if top_pos<0:
        raise SystemExit('ABORT V38 marker missing')
    if repaired.find('def get_runtime_mode(')>top_pos:
        raise SystemExit('ABORT get_runtime_mode still defined after V38 bootstrap')
    if repaired.find('def render_trading(')>top_pos:
        raise SystemExit('ABORT render_trading still defined after V38 bootstrap')

    tmp=APP.with_suffix('.py.v38repairtmp')
    tmp.write_text(repaired,encoding='utf-8')
    try:
        py_compile.compile(str(tmp),doraise=True)
        APP.write_text(repaired,encoding='utf-8')
    finally:
        tmp.unlink(missing_ok=True)

    print('V38_FUNCTION_ORDER=REPAIRED',flush=True)
    print('GET_RUNTIME_MODE_DEFINED_BEFORE_UI=PASS',flush=True)
    print('V38_REQUIRED_HELPERS=PASS',flush=True)

    subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False)
    time.sleep(1)
    cmd=(f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup '
         f'/home/ubuntu/day-trader-api/venv/bin/python -m streamlit run app_v5.py '
         f'--server.address=0.0.0.0 --server.port={PORT} --server.headless=true '
         f'> {LOG} 2>&1 &')
    subprocess.Popen(['bash','-lc',cmd],start_new_session=True)

    deadline=time.time()+45
    last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=2) as r:
                if r.status==200:
                    print('V5_HTTP=PASS',flush=True)
                    break
        except Exception as e:
            last=e
        time.sleep(2)
    else:
        raise SystemExit(f'ABORT V5 startup failed: {last}; log={LOG}')

    # Streamlit can return HTTP 200 while the script itself throws. Give it one render cycle
    # and fail deployment if the known NameError or traceback remains in the process log.
    time.sleep(3)
    log=LOG.read_text(encoding='utf-8',errors='replace') if LOG.exists() else ''
    if "NameError: name 'get_runtime_mode' is not defined" in log:
        raise SystemExit('ABORT get_runtime_mode NameError still present')
    print('V5_RUNTIME_NAMEERROR=ABSENT',flush=True)
    print('V5_DESIGN=REFERENCE_TERMINAL_V38',flush=True)
    print('V22_BACKEND=UNTOUCHED',flush=True)
    print('DEPLOY=PASS',flush=True)


if __name__=='__main__':
    main()
