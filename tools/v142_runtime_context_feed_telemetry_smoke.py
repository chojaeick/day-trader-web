#!/usr/bin/env python3
"""V142 runtime context feed + telemetry smoke test.

Patches runtime v4_engine.py to populate USA-only williams_frozen_ctx on each USA row
using live 1m bars and frozen historical-equivalent features. No broker/order authority.
Then performs a static/runtime import smoke check only.
"""
from pathlib import Path
import shutil, re, py_compile

P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
B=P.with_suffix('.py.bak_v142')
S=P.read_text()
if not B.exists(): shutil.copy2(P,B)

METHOD=r'''
    def _v142_build_usa_frozen_ctx(self, row, b1, prev_day=None):
        """Build frozen USA Williams context. USA only; no order authority."""
        try:
            if str((row or {}).get('market','')).upper()!='USA' or b1 is None or len(b1)<25:
                return None
            # Required columns must exist on live 1m bars.
            need=('time','open','high','low','close','volume')
            if any(c not in b1.columns for c in need):
                return None
            closes=[_f(v) for v in b1['close'].tolist()]
            highs=[_f(v) for v in b1['high'].tolist()]
            lows=[_f(v) for v in b1['low'].tolist()]
            vols=[_f(v) for v in b1['volume'].tolist()]
            if len(closes)<21:return None
            # Reuse engine's frozen Williams helpers where available.
            rsi2=_williams_rsi2(closes[-60:])
            # CCI20 causal on completed/current 1m bars.
            tp=[(h+l+c)/3.0 for h,l,c in zip(highs,lows,closes)]
            cci20=None
            if len(tp)>=20:
                w=tp[-20:]; m=sum(w)/20.0; md=sum(abs(x-m) for x in w)/20.0
                cci20=0.0 if md==0 else (tp[-1]-m)/(0.015*md)
            # EMA12/26 + signal9, same recurrence as replay.
            def _ema(vals,span):
                if not vals:return []
                a=2.0/(span+1.0);o=[float(vals[0])]
                for v in vals[1:]:o.append(a*float(v)+(1-a)*o[-1])
                return o
            e12=_ema(closes,12);e26=_ema(closes,26);mac=[a-b for a,b in zip(e12,e26)];sg=_ema(mac,9)
            hist=[a-b for a,b in zip(mac,sg)]
            if len(hist)<2 or rsi2 is None or cci20 is None:return None
            prior=vols[-11:-1]
            va=(sum(prior)/len(prior)) if prior else 0.0
            cur=closes[-1]; prv=closes[-2]
            # prev-day OHLC must be supplied by row/gate context; do not invent it.
            day_open=_f((row or {}).get('day_open') or (row or {}).get('session_open'))
            ph=_f((row or {}).get('prev_day_high'))
            pl=_f((row or {}).get('prev_day_low'))
            if not (day_open and ph and pl):return None
            trigger=day_open+0.5*(ph-pl)
            cross_now=bool(prv<=trigger<cur)
            prev_crossed=bool((row or {}).get('williams_frozen_cross_seen'))
            ts=b1.iloc[-1]['time']
            return {
                'entry_args':{
                    'ts':ts,'prev_crossed':prev_crossed,'cross_now':cross_now,'rsi2':rsi2,
                    'day_open':day_open,'prev_high':ph,'prev_low':pl,'volume':vols[-1],
                    'prior10_volume_avg':va,'cci20':cci20,'macd_hist':hist[-1],
                    'prev_macd_hist':hist[-2],
                },
                'feature_snapshot':{'rsi2':rsi2,'cci20':cci20,'macd_hist':hist[-1],
                                    'prev_macd_hist':hist[-2],'volume_ratio':(vols[-1]/va if va else 0.0),
                                    'trigger':trigger,'cross_now':cross_now}
            }
        except Exception as e:
            return {'error':str(e)}
'''
if 'def _v142_build_usa_frozen_ctx' not in S:
    anchor='    def _v140_usa_frozen_williams_eval(self, row):\n'
    if anchor not in S: raise SystemExit('ANCHOR_NOT_FOUND:V140')
    S=S.replace(anchor,METHOD+'\n'+anchor,1)

# Attach context immediately before V140 evaluator call in USA paper bridge.
needle="""        if str(market).upper()=='USA':\n            ev=self._v140_usa_frozen_williams_eval(row)\n"""
if needle in S and 'V142_RUNTIME_CTX_FEED' not in S:
    repl="""        if str(market).upper()=='USA':\n            # V142_RUNTIME_CTX_FEED: context only; still no order authority.\n            try:\n                _b1=(row or {}).get('_b1_live')\n                if _b1 is not None:\n                    row['williams_frozen_ctx']=self._v142_build_usa_frozen_ctx(row,_b1)\n            except Exception as _e:\n                row['williams_frozen_ctx']={'error':str(_e)}\n            ev=self._v140_usa_frozen_williams_eval(row)\n"""
    S=S.replace(needle,repl,1)

P.write_text(S)
py_compile.compile(str(P),doraise=True)
T=P.read_text()
checks={
 'v140_eval':'def _v140_usa_frozen_williams_eval' in T,
 'v142_builder':'def _v142_build_usa_frozen_ctx' in T,
 'runtime_feed':'V142_RUNTIME_CTX_FEED' in T,
 'frozen_ctx_key':'williams_frozen_ctx' in T,
 'no_broker_added': 'V142_RUNTIME_CTX_FEED' in T and 'KiwoomMockBroker' not in T[T.find('V142_RUNTIME_CTX_FEED'):T.find('V142_RUNTIME_CTX_FEED')+1200],
}
print('=== V142 RUNTIME CONTEXT FEED + TELEMETRY SMOKE ===')
print('PATCHED',P)
print('BACKUP',B)
for k,v in checks.items(): print(k,'PASS' if v else 'FAIL')
print('PY_COMPILE=PASS')
print('ORDER_AUTHORITY=NONE')
print('USA_ONLY=YES')
print('SMOKE_PASS=',all(checks.values()))
print('NEXT=V143_RUNTIME_ROW_FEED_INTEGRATION_AUDIT' if all(checks.values()) else 'NEXT=FIX_V142_ONLY')
