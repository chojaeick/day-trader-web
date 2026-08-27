#!/usr/bin/env python3
from pathlib import Path
import re, subprocess, shutil, time, urllib.request, json

print('=== V234 STOP KOREA MOCK + ADD 1M/5M ENTRY GUARD ===')
print('PHASE1=STOP_KIWOOM_MOCK_AUTO_IMMEDIATELY')
print('PHASE2=PATCH_WILLIAMS_ENTRY_WITH_5M_DIRECTION_AND_1M_TIMING_GUARD')
print('REAL_ACCOUNT_CHANGE=NONE USA_CHANGE=NONE EXIT_LOGIC_CHANGE=NONE')

ENV=Path('/home/ubuntu/day-trader-api/.env')
ENG=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
if not ENV.exists() or not ENG.exists():
    raise SystemExit('V234_ABORT required runtime files missing')

# ---- PHASE 1: stop new KR mock orders first ----
env_txt=ENV.read_text()
backup_env=ENV.with_name('.env.bak_v234')
shutil.copy2(ENV, backup_env)
if re.search(r'(?m)^WILLIAMS_KIWOOM_MOCK_AUTO=', env_txt):
    env_txt=re.sub(r'(?m)^WILLIAMS_KIWOOM_MOCK_AUTO=.*$', 'WILLIAMS_KIWOOM_MOCK_AUTO=0', env_txt)
else:
    env_txt += '\nWILLIAMS_KIWOOM_MOCK_AUTO=0\n'
ENV.write_text(env_txt)
print('MOCK_AUTO_ENV_OFF=', 'WILLIAMS_KIWOOM_MOCK_AUTO=0' in ENV.read_text())
rc=subprocess.run(['sudo','systemctl','restart','day-trader-api']).returncode
print('STOP_RESTART_RC=',rc)
time.sleep(3)

# ---- PHASE 2: patch evaluator, but keep auto OFF ----
s=ENG.read_text()
backup_eng=ENG.with_name('v4_engine.py.bak_v234')
shutil.copy2(ENG, backup_eng)
marker='V234_MTF_ENTRY_GUARD'
if marker in s:
    print('PATCH_ALREADY_PRESENT=YES')
    patched=0
else:
    anchor="""            out['current_price']=current_price\n            return out\n"""
    if s.count(anchor)!=1:
        raise SystemExit(f'V234_ABORT anchor count={s.count(anchor)}')
    block="""            out['current_price']=current_price\n\n            # V234_MTF_ENTRY_GUARD: 5m decides direction, 1m decides timing.\n            # Applies only to a fresh Williams entry signal; no order side effect here.\n            if bool(out.get('signal')):\n                try:\n                    _m=cur.copy()\n                    _m['_dt']=pd.to_datetime(_m['time'].astype(str).str[:14],format='%Y%m%d%H%M%S',errors='coerce')\n                    _m=_m.dropna(subset=['_dt']).sort_values('_dt').reset_index(drop=True)\n                    _c=pd.to_numeric(_m['close'],errors='coerce').astype(float)\n                    _h=pd.to_numeric(_m['high'],errors='coerce').astype(float)\n                    _l=pd.to_numeric(_m['low'],errors='coerce').astype(float)\n\n                    def _rsi14(_s):\n                        _d=_s.diff(); _g=_d.clip(lower=0); _dn=(-_d.clip(upper=0))\n                        _ag=_g.ewm(alpha=1/14,adjust=False,min_periods=14).mean()\n                        _ad=_dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()\n                        _rs=_ag/_ad.replace(0,pd.NA)\n                        return (100-(100/(1+_rs))).astype(float)\n\n                    def _macd(_s):\n                        _mac=_s.ewm(span=12,adjust=False).mean()-_s.ewm(span=26,adjust=False).mean()\n                        _sig=_mac.ewm(span=9,adjust=False).mean(); return _mac,_sig,_mac-_sig\n\n                    def _cci9(_h,_l,_c):\n                        _tp=(_h+_l+_c)/3.0; _ma=_tp.rolling(9).mean()\n                        _md=_tp.rolling(9).apply(lambda z: float((z-z.mean()).abs().mean()),raw=False)\n                        return (_tp-_ma)/(0.015*_md.replace(0,pd.NA))\n\n                    _r1=_rsi14(_c); _mac1,_sig1,_hist1=_macd(_c); _cci1=_cci9(_h,_l,_c)\n                    _r1n=float(_r1.iloc[-1]) if len(_r1) and pd.notna(_r1.iloc[-1]) else None\n                    _r1p=float(_r1.iloc[-2]) if len(_r1)>1 and pd.notna(_r1.iloc[-2]) else None\n                    _h1n=float(_hist1.iloc[-1]) if len(_hist1) and pd.notna(_hist1.iloc[-1]) else None\n                    _h1p=float(_hist1.iloc[-2]) if len(_hist1)>1 and pd.notna(_hist1.iloc[-2]) else None\n                    _c1n=float(_cci1.iloc[-1]) if len(_cci1) and pd.notna(_cci1.iloc[-1]) else None\n                    _c1p=float(_cci1.iloc[-2]) if len(_cci1)>1 and pd.notna(_cci1.iloc[-2]) else None\n                    _improve=sum([\n                        bool(_r1n is not None and _r1p is not None and _r1n>=_r1p),\n                        bool(_h1n is not None and _h1p is not None and _h1n>=_h1p),\n                        bool(_c1n is not None and _c1p is not None and _c1n>=_c1p),\n                    ])\n                    _rsi70_exit=bool(_r1p is not None and _r1n is not None and _r1p>=70.0 and _r1n<70.0)\n                    _cci_dump=bool(_c1p is not None and _c1n is not None and ((_c1p-_c1n)>=100.0 or (_c1p>=100.0 and _c1n<100.0)))\n                    _one_ok=bool(_improve>=2 and not _rsi70_exit and not _cci_dump)\n\n                    # Build completed 5m candles only; current incomplete 5m bucket is excluded.\n                    _m2=_m.set_index('_dt')[['open','high','low','close']].apply(pd.to_numeric,errors='coerce')\n                    _bucket=_m['_dt'].iloc[-1].floor('5min')\n                    _m2=_m2[_m2.index < _bucket]\n                    _b5=_m2.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna().reset_index()\n                    _five_ok=False; _r5n=_h5n=_h5p=_ema5=_cl5=None\n                    if len(_b5)>=20:\n                        _c5=pd.to_numeric(_b5['close'],errors='coerce').astype(float)\n                        _r5=_rsi14(_c5); _mac5,_sig5,_hist5=_macd(_c5); _ema20=_c5.ewm(span=20,adjust=False).mean()\n                        _r5n=float(_r5.iloc[-1]) if pd.notna(_r5.iloc[-1]) else None\n                        _h5n=float(_hist5.iloc[-1]) if pd.notna(_hist5.iloc[-1]) else None\n                        _h5p=float(_hist5.iloc[-2]) if len(_hist5)>1 and pd.notna(_hist5.iloc[-2]) else None\n                        _m5n=float(_mac5.iloc[-1]); _s5n=float(_sig5.iloc[-1]); _ema5=float(_ema20.iloc[-1]); _cl5=float(_c5.iloc[-1])\n                        _five_ok=bool(_r5n is not None and _r5n>=45.0 and ((_m5n>=_s5n) or (_h5n is not None and _h5p is not None and _h5n>=_h5p)) and _cl5>=_ema5)\n\n                    _mtf_ok=bool(_one_ok and _five_ok)\n                    out['v234_mtf_guard']={\n                        'ok':_mtf_ok,'one_min_ok':_one_ok,'five_min_ok':_five_ok,'one_improve_count':_improve,\n                        'rsi1':_r1n,'rsi1_prev':_r1p,'cci1':_c1n,'cci1_prev':_c1p,'hist1':_h1n,'hist1_prev':_h1p,\n                        'rsi70_exit':_rsi70_exit,'cci_dump':_cci_dump,'rsi5':_r5n,'hist5':_h5n,'hist5_prev':_h5p,\n                        'close5':_cl5,'ema20_5':_ema5\n                    }\n                    if not _mtf_ok:\n                        out['signal']=False\n                        out['stage']='BLOCKED_MTF'\n                except Exception as _e:\n                    # Fail closed for new mock entries if the MTF guard cannot be evaluated.\n                    out['signal']=False\n                    out['stage']='BLOCKED_MTF_DATA'\n                    out['v234_mtf_guard']={'ok':False,'error':f'{type(_e).__name__}: {_e}'[:180]}\n\n            return out\n"""
    s=s.replace(anchor,block)
    ENG.write_text(s)
    patched=1
print('PATCH_MTF_GUARD=',patched)

# compile only; DO NOT re-enable orders in this script
py='/home/ubuntu/day-trader-api/venv/bin/python3'
rc=subprocess.run([py,'-m','py_compile',str(ENG)]).returncode
print('PY_COMPILE_RC=',rc)
static={
 'MTF_MARKER': marker in ENG.read_text(),
 'AUTO_STILL_OFF': 'WILLIAMS_KIWOOM_MOCK_AUTO=0' in ENV.read_text(),
 'STRUCT5_GUARD_V233_KEPT': 'V233_STRUCT5_LIVE_PRICE_GUARD' in ENG.read_text(),
}
print('STATIC_CHECKS=',static)
print('V234_PASS=', bool(rc==0 and all(static.values())))
print('KOREA_MOCK_AUTO_RUNNING=NO')
print('NEXT=RUN_V234B_VERIFY_THEN_REENABLE_ONLY_IF_PASS')
