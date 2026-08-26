#!/usr/bin/env python3
"""V141: build/audit USA frozen Williams live feature context adapter.

Purpose
- Do NOT grant order authority.
- Verify runtime can construct every frozen-entry/exit input from existing USA row/bars.
- Patch only telemetry context generation into runtime v4_engine.py.
- Keep Korea paths untouched.
"""
from pathlib import Path
import shutil

P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
B=P.with_suffix('.py.bak_v141')
S=P.read_text(errors='ignore')
if not B.exists(): shutil.copy2(P,B)

METHOD=r'''
    def _v141_build_usa_frozen_ctx(self, row, gate=None, b1=None):
        """Construct frozen Williams USA context from causal live data only."""
        try:
            if str((row or {}).get('market','')).upper()!='USA': return None
            g=gate or {}
            bars=b1
            if bars is None or len(bars)<21: return {'ready':False,'reason':'BARS_LT_21'}
            closes=[_f(v) for v in bars['close'].tolist()]
            highs=[_f(v) for v in bars['high'].tolist()]
            lows=[_f(v) for v in bars['low'].tolist()]
            vols=[_f(v) for v in bars['volume'].tolist()] if 'volume' in bars.columns else [0.0]*len(bars)
            if not closes or min(len(closes),len(highs),len(lows))<21: return {'ready':False,'reason':'BARS_INVALID'}
            i=len(closes)-1
            # Prefer already-computed causal Williams diagnostics from gate/row. V141 does not invent strategy logic.
            ctx={
                'ts': (bars.iloc[-1].get('time') if 'time' in bars.columns else (bars.iloc[-1].get('ts') if 'ts' in bars.columns else None)),
                'prev_crossed': bool((row or {}).get('williams_cross_seen') or g.get('williams_cross_seen')),
                'cross_now': bool((row or {}).get('williams_entry_raw_cross') or g.get('williams_raw_cross')),
                'rsi2': (row or {}).get('williams_entry_rsi2'),
                'day_open': g.get('williams_day_open') or (row or {}).get('williams_day_open'),
                'prev_high': g.get('williams_prev_high') or (row or {}).get('williams_prev_high'),
                'prev_low': g.get('williams_prev_low') or (row or {}).get('williams_prev_low'),
                'volume': vols[i],
                'prior10_volume_avg': (sum(vols[max(0,i-10):i])/len(vols[max(0,i-10):i])) if i>0 and vols[max(0,i-10):i] else 0.0,
                'cci20': g.get('williams_cci20') or (row or {}).get('williams_cci20'),
                'macd_hist': g.get('williams_macd_hist') or (row or {}).get('williams_macd_hist'),
                'prev_macd_hist': g.get('williams_prev_macd_hist') or (row or {}).get('williams_prev_macd_hist'),
            }
            missing=[k for k,v in ctx.items() if v is None]
            out={'ready':not missing,'missing':missing,'entry_args':ctx if not missing else None}
            pos=(row or {}).get('position') or {}
            exit_args={
                'entry_price': (row or {}).get('avg_entry') or pos.get('avg_entry'),
                'price': (row or {}).get('price'),
                'macd': g.get('macd') or (row or {}).get('macd'),
                'signal': g.get('macd_signal') or (row or {}).get('macd_signal'),
                'cci20': ctx.get('cci20'),
                'prev_cci20': g.get('williams_prev_cci20') or (row or {}).get('williams_prev_cci20'),
                'weak_run': (row or {}).get('williams_combo_weak_run') or 0,
            }
            if exit_args['entry_price'] and all(exit_args.get(k) is not None for k in ('price','macd','signal','cci20','prev_cci20')):
                out['exit_args']=exit_args
            else:
                out['exit_args']=None
            return out
        except Exception as e:
            return {'ready':False,'reason':'ERROR','error':str(e)}
'''
if 'def _v141_build_usa_frozen_ctx' not in S:
    anchor='    def _v140_usa_frozen_williams_eval(self, row):\n'
    if anchor not in S: raise SystemExit('V140_ANCHOR_NOT_FOUND')
    S=S.replace(anchor,METHOD+'\n'+anchor,1)

# Audit available names/markers; do not grant authority.
markers={
 'v140_eval':'def _v140_usa_frozen_williams_eval' in S,
 'usa_gate':'_usa_entry_trigger' in S,
 'ticks_to_bars':'ticks_to_bars' in S,
 'williams_rsi2':'_williams_rsi2' in S,
 'williams_gate':'_williams_entry_from_gate' in S,
 'cci_text':'cci' in S.lower(),
 'macd_text':'macd' in S.lower(),
 'paper_bridge':'def _paper_williams_step' in S,
}
P.write_text(S)
print('=== V141 LIVE FEATURE CONTEXT ADAPTER AUDIT ===')
print('PATCHED',P)
print('BACKUP',B)
for k,v in markers.items(): print(k,'PASS' if v else 'MISSING')
print('ORDER_AUTHORITY=NONE')
print('USA_ONLY_ADAPTER=YES')
print('KOREA_PATH_TOUCHED=NO')
print('ADAPTER_INSTALLED=', 'def _v141_build_usa_frozen_ctx' in S)
print('STATIC_PASS=', all(markers.values()) and 'def _v141_build_usa_frozen_ctx' in S)
print('NEXT=V142_RUNTIME_CONTEXT_FEED_AND_TELEMETRY_SMOKE_TEST')
