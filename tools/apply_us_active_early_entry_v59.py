#!/usr/bin/env python3
from pathlib import Path
import os, py_compile, subprocess, tempfile, time, json, re

R=Path('/home/ubuntu/day-trader-api')
P=R/'live_server'/'v22e_us_mock_live.py'
A=R/'v22e_us_mock_account.json'
SERVICE='day-trader-v22e-us'

s=P.read_text(encoding='utf-8')
if 'V59_ACTIVE_EARLY_ENTRY = True' not in s:
    marker='V58_LIVE_CAPITAL_FULL_FINDER = True'
    if marker not in s: raise SystemExit('ABORT V58 marker missing')
    s=s.replace(marker,marker+'\nV59_ACTIVE_EARLY_ENTRY = True',1)

    # Live-only early entry threshold. Strict Engine5 signal remains valid as path A.
    if "EARLY_ENTRY_SCORE =" not in s:
        anchor="CAPITAL_USE_PCT = min(0.999, max(0.90, float(os.getenv('V22E_US_CAPITAL_USE_PCT', '0.995') or 0.995)))"
        if anchor not in s: raise SystemExit('ABORT capital config anchor missing')
        s=s.replace(anchor,anchor+"\nEARLY_ENTRY_SCORE = float(os.getenv('V22E_US_EARLY_ENTRY_SCORE', '45') or 45)",1)

    start=s.find('def evaluate_entry(b5):')
    end=s.find('\n\ndef evaluate_exit(',start)
    if start<0 or end<0: raise SystemExit('ABORT evaluate_entry block missing')
    new=r'''def evaluate_entry(b5):
    z=engine.enrich(b5)
    if z.empty:
        return {'enter':False,'reason':'NO_ENGINE_ROWS'}
    r=z.iloc[-1]
    px=f(r.get('close')); il=f(r.get('inner_lower')); ou=f(r.get('outer_upper'))
    band_r=max(px-il,0.0) if px and il else 0.0
    score=f(r.get('entry_score'))

    strict=bool(r.get('entry_signal')) and band_r>0
    # Active V22E live path: keep the four directional conditions, but do not
    # wait for two completed 5m persistence bars. This catches acceleration
    # earlier while still requiring trend/MACD/RSI alignment.
    g_trend=bool(r.get('gate_trend_up'))
    g_macd=bool(r.get('gate_macd_rising'))
    g_accel=bool(r.get('gate_macd_accel'))
    g_rsi=bool(r.get('gate_rsi_rising'))
    early=bool(band_r>0 and score>=EARLY_ENTRY_SCORE and g_trend and g_macd and g_accel and g_rsi)
    enter=bool(strict or early)
    reason='V22E_ENTRY_STRICT' if strict else 'V22E_EARLY_ENTRY' if early else 'NO_ENTRY'
    return {
        'enter':enter,'reason':reason,'score':score,'effective_score':score,'price':px,
        'bar_time':str(r.get('time') or ''),'band_r':band_r,
        'stop_price':px-band_r if band_r else 0.0,
        'tp1_price':px+2*band_r if band_r else 0.0,'outer_upper':ou,
        'strict_signal':strict,'early_signal':early,'early_score_threshold':EARLY_ENTRY_SCORE,
        'gates':{'trend':g_trend,'macd_rising':g_macd,'macd_accel':g_accel,'rsi_rising':g_rsi,
                 'macd_context':bool(r.get('gate_macd_context')),'rsi_persistent':bool(r.get('gate_rsi_persistent'))}
    }
'''
    s=s[:start]+new+s[end:]

    # Preserve the decision reason in order logs instead of collapsing to generic V22E_ENTRY.
    old="res = order_once('BUY', sym, qty, px, ex, bar_key, 'V22E_ENTRY')"
    if old in s:
        s=s.replace(old,"res = order_once('BUY', sym, qty, px, ex, bar_key, str(d.get('reason') or 'V22E_ENTRY'))",1)

fd,name=tempfile.mkstemp(prefix='v59_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(s,encoding='utf-8'); py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS',flush=True)
bak=Path(str(P)+'.pre_v59')
if not bak.exists(): subprocess.run(['sudo','cp','-a',P,bak],check=True)
subprocess.run(['sudo','install','-m','0644',t,P],check=True); t.unlink(missing_ok=True)
subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)
time.sleep(8)
active=subprocess.check_output(['sudo','systemctl','is-active',SERVICE],text=True).strip()
if active!='active':
    subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','120','--no-pager'],check=False)
    raise SystemExit('ABORT V22E inactive')
print('V22E_SERVICE=ACTIVE',flush=True)

try:d=json.loads(A.read_text(encoding='utf-8')) if A.exists() else {}
except Exception:d={}
print('US_ACCOUNT='+json.dumps({'total_assets':d.get('total_assets'),'cash':d.get('cash'),'orderable_cash':d.get('orderable_cash'),'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []]},ensure_ascii=False),flush=True)
print('ENTRY_PROFILE=STRICT_OR_EARLY_DIRECTIONAL',flush=True)
print('EARLY_ENTRY_SCORE=45',flush=True)
print('EARLY_ENTRY_GATES=TREND_UP+MACD_RISING+MACD_ACCEL+RSI_RISING',flush=True)
print('PERSISTENCE_WAIT=NOT_REQUIRED_FOR_EARLY_PATH',flush=True)
print('FINDER_EVALUATION=TOP20_FULL_POOL',flush=True)
print('ORDER_SIZING=LIVE_ACCOUNT_99_5PCT_ACROSS_REMAINING_SLOTS',flush=True)
print('MAX_POSITIONS=4',flush=True)
print('DEPLOY=PASS',flush=True)
