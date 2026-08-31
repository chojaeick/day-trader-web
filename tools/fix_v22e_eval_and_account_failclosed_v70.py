#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import json, os, py_compile, subprocess, tempfile, time

R=Path('/home/ubuntu/day-trader-api')
RUNNER=R/'live_server/v22e_us_mock_live.py'
EVAL=R/'v22e_us_mock_eval.json'
SERVICE='day-trader-v22e-us'
BRIDGE=Path('/home/ubuntu/day-trader-api-repo/tools/repair_v5_us_data_bridge_v66.py')

s=RUNNER.read_text(encoding='utf-8')
orig=s

# 1) eval_store is a dict keyed by symbol. V69 incorrectly required list/tuple.
old="""                    _v69_rows = eval_store
                    if isinstance(_v69_rows, (list, tuple)) and len(_v69_rows) > 0:
                        import json as _v69_json, os as _v69_os
                        from pathlib import Path as _V69Path
                        _v69_p=_V69Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')
                        _v69_t=_V69Path(str(_v69_p)+'.v69tmp')
                        _v69_t.write_text(_v69_json.dumps({'rows':list(_v69_rows),'source':'V22E_HEARTBEAT_LIVE','session':locals().get('session') or locals().get('sess')}, ensure_ascii=False), encoding='utf-8')
                        _v69_os.replace(_v69_t,_v69_p)
"""
new="""                    _v69_rows = list(eval_store.values()) if isinstance(eval_store, dict) else list(eval_store or [])
                    if len(_v69_rows) > 0:
                        import json as _v69_json, os as _v69_os
                        from pathlib import Path as _V69Path
                        _v69_p=_V69Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')
                        _v69_t=_V69Path(str(_v69_p)+'.v69tmp')
                        _v69_t.write_text(_v69_json.dumps({'rows':_v69_rows,'source':'V22E_HEARTBEAT_LIVE','session':'REGULAR' if regular else 'PREMARKET' if premarket else 'CLOSED'}, ensure_ascii=False), encoding='utf-8')
                        _v69_os.replace(_v69_t,_v69_p)
"""
if old not in s:
    raise SystemExit('ABORT V69 publish anchor not found; runtime untouched')
s=s.replace(old,new,1)

# 2) Add explicit account-read health state beside holdings cache globals.
anchor="def refresh_holdings(force=False):\n    global _last_recon, _holdings_cache\n"
repl="def refresh_holdings(force=False):\n    global _last_recon, _holdings_cache, _holdings_read_ok\n"
if anchor not in s:
    raise SystemExit('ABORT refresh_holdings global anchor not found; runtime untouched')
s=s.replace(anchor,repl,1)

# initialize health flag immediately when a real refresh starts
anchor="""    if not force and now-_last_recon<RECON_SEC:
        return dict(_holdings_cache)
    out={}; b=broker()
"""
repl="""    if not force and now-_last_recon<RECON_SEC:
        return dict(_holdings_cache)
    _holdings_read_ok=True
    _read_errors=0
    out={}; b=broker()
"""
if anchor not in s:
    raise SystemExit('ABORT refresh start anchor not found; runtime untouched')
s=s.replace(anchor,repl,1)

# count whole-account and per-symbol read failures
s=s.replace("""    except Exception as e:
        log('ACCOUNT_READ_ERROR',exchange='ALL',error=repr(e))
""","""    except Exception as e:
        _read_errors += 1
        log('ACCOUNT_READ_ERROR',exchange='ALL',error=repr(e))
""",1)
s=s.replace("""            except Exception as e:
                log('ACCOUNT_READ_ERROR',exchange=ex,symbol=sym,error=repr(e))
""","""            except Exception as e:
                _read_errors += 1
                log('ACCOUNT_READ_ERROR',exchange=ex,symbol=sym,error=repr(e))
""",1)

# On read failure with no confirmed holdings, preserve last cache and mark unhealthy.
anchor="    _holdings_cache=out; _last_recon=time.monotonic(); return dict(out)\n"
repl="""    if _read_errors and not out:
        _holdings_read_ok=False
        _last_recon=time.monotonic()
        log('ACCOUNT_FAIL_CLOSED_CACHE_PRESERVED',cached_holdings=len(_holdings_cache),read_errors=_read_errors)
        return dict(_holdings_cache)
    _holdings_read_ok=True
    _holdings_cache=out; _last_recon=time.monotonic(); return dict(out)
"""
if anchor not in s:
    raise SystemExit('ABORT refresh return anchor not found; runtime untouched')
s=s.replace(anchor,repl,1)

# 3) Main loop: if account refresh is unhealthy, do not clear state or place any order.
anchor="""            holdings = refresh_holdings()
            if pending_buy:
"""
repl="""            holdings = refresh_holdings()
            if not globals().get('_holdings_read_ok', True):
                log('ACCOUNT_FAIL_CLOSED_ORDER_GATE',holdings_cached=len(holdings),order_gate='DISABLED_ACCOUNT_READ_ERROR')
                time.sleep(max(LOOP_SEC, 5))
                continue
            if pending_buy:
"""
if anchor not in s:
    raise SystemExit('ABORT main fail-closed anchor not found; runtime untouched')
s=s.replace(anchor,repl,1)

marker='V70_EVAL_DICT_PUBLISH_AND_ACCOUNT_FAIL_CLOSED = True'
if marker not in s:
    pos=0
    lines=s.splitlines(True)
    for i,line in enumerate(lines):
        if line.startswith('import ') or line.startswith('from '): pos=i+1
    lines.insert(pos,f'\n{marker}\n')
    s=''.join(lines)

if s==orig:
    raise SystemExit('ABORT no changes')

fd,name=tempfile.mkstemp(prefix='v70_runner_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(s,encoding='utf-8')
py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS',flush=True)

bak=Path(str(RUNNER)+'.pre_v70')
if not bak.exists(): subprocess.run(['sudo','cp','-a',RUNNER,bak],check=True)
subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',t,RUNNER],check=True)
t.unlink(missing_ok=True)
print('V70_RUNTIME_INSTALLED=YES',flush=True)
print('ACCOUNT_READ_FAILURE_POLICY=FAIL_CLOSED',flush=True)
print('STATE_CLEAR_ON_ACCOUNT_ERROR=DISABLED',flush=True)
print('ORDER_ON_ACCOUNT_ERROR=DISABLED',flush=True)
print('EVAL_STORE_TYPE=DICT_VALUES_PUBLISHED',flush=True)

# exactly one restart; avoid repeated OAuth token churn
subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)

# Wait for real non-empty published eval. Account 429 after restart is tolerated by fail-closed gate.
deadline=time.time()+120
rows=[]
source=None
while time.time()<deadline:
    try:
        d=json.loads(EVAL.read_text(encoding='utf-8'))
        if isinstance(d,dict) and isinstance(d.get('rows'),list):
            rows=d['rows']; source=d.get('source')
        elif isinstance(d,list): rows=d
        if rows: break
    except Exception: pass
    time.sleep(2)
print(f'V22E_EVAL_ROWS={len(rows)}',flush=True)
print(f'V22E_EVAL_SOURCE={source}',flush=True)

# Show latest safety/recovery heartbeat evidence.
subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','35','--no-pager'],check=False)
if not rows:
    raise SystemExit('ABORT eval not published after 120s; V5 untouched')

# Data-only UI bridge, only after real rows exist.
if not BRIDGE.exists(): raise SystemExit('ABORT V66 bridge missing')
p=subprocess.run(['sudo',str(R/'venv/bin/python'),str(BRIDGE)],text=True,capture_output=True)
print(p.stdout,end='')
if p.stderr: print(p.stderr,end='')
if p.returncode!=0: raise SystemExit(f'ABORT V66 bridge failed rc={p.returncode}')

print('FINDER_SOURCE=V22E_HEARTBEAT_LIVE',flush=True)
print('TRADING_STRATEGY_RULES=UNCHANGED',flush=True)
print('DEPLOY=PASS',flush=True)
