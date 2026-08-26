#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

# V107: connect the existing pure Williams V23 entry evaluator to the live Korea tracker.
# Reuse the already-fetched/cached ka10080 gate bars; do not add another broker API call.

# 1) Preserve enough chronological 1m data (including time) inside the gate for V23 inputs.
old="""                'bars_raw':b[['open','high','low','close']].tail(240).to_dict('records'),\n\n                'latest_5m':five.iloc[-1]['dt'].isoformat(),\n"""
new="""                'bars_raw':b[['open','high','low','close']].tail(240).to_dict('records'),\n\n                'williams_signal_bars':b[[c for c in ('time','open','high','low','close') if c in b.columns]].tail(900).to_dict('records'),\n\n                'latest_5m':five.iloc[-1]['dt'].isoformat(),\n"""
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V105 bars_raw gate block')
s=s.replace(old,new,1)

# 2) Add an adapter that derives prev-day range/current-day open/previous+current price
#    from the cached gate bars and calls the frozen williams_live_evaluate_v23() function.
anchor="""    def _williams_structure_from_gate(self,gate,entry_price=None):\n"""
if anchor not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: _williams_structure_from_gate anchor')

method=r'''    def _williams_entry_from_gate(self,sym,gate,finder_rank=None):
        empty={
            'signal':False,
            'stage':'DATA_WAIT',
            'raw_cross':False,
            'trigger':None,
            'rsi2':None,
            'finder_rank':finder_rank,
            'source':'KOREA_SHADOW_GATE_REUSE',
        }
        try:
            raw=(gate or {}).get('williams_signal_bars') or []
            if len(raw)<3:
                return empty
            x=pd.DataFrame(raw)
            need={'time','open','high','low','close'}
            if not need.issubset(set(x.columns)):
                empty['stage']='DATA_INVALID'
                empty['columns']=[str(c) for c in x.columns]
                return empty

            # Kiwoom minute time is normally YYYYMMDDHHMMSS. Keep only valid numeric rows.
            x=x.copy()
            x['time']=x['time'].astype(str).str.replace(r'\\D','',regex=True)
            for c in ('open','high','low','close'):
                x[c]=pd.to_numeric(x[c],errors='coerce').abs()
            x=x.dropna(subset=['open','high','low','close'])
            x=x[x['time'].str.len()>=8]
            if len(x)<3:
                return empty
            x=x.sort_values('time').reset_index(drop=True)
            x['day']=x['time'].str[:8]
            days=[d for d in x['day'].drop_duplicates().tolist() if d]
            if len(days)<2:
                empty['stage']='NEED_PREV_DAY'
                return empty

            cur_day=days[-1]
            prev_day=days[-2]
            prev=x[x['day']==prev_day]
            cur=x[x['day']==cur_day]
            if prev.empty or len(cur)<2:
                return empty

            prev_day_high=float(prev['high'].max())
            prev_day_low=float(prev['low'].min())
            day_open=float(cur.iloc[0]['open'])
            prev_price=float(cur.iloc[-2]['close'])
            current_price=float(cur.iloc[-1]['close'])
            recent_closes=[float(v) for v in cur['close'].tail(30).tolist()]

            out=williams_live_evaluate_v23(
                symbol=sym,
                prev_day_high=prev_day_high,
                prev_day_low=prev_day_low,
                day_open=day_open,
                prev_price=prev_price,
                current_price=current_price,
                recent_closes=recent_closes,
                finder_rank=finder_rank,
            )
            out['source']='KOREA_SHADOW_GATE_REUSE'
            out['prev_day']=prev_day
            out['current_day']=cur_day
            out['prev_day_high']=prev_day_high
            out['prev_day_low']=prev_day_low
            out['day_open']=day_open
            out['prev_price']=prev_price
            out['current_price']=current_price
            return out
        except Exception as e:
            empty['stage']='DATA_INVALID'
            empty['error']=f'{type(e).__name__}: {e}'[:240]
            return empty

'''
if 'def _williams_entry_from_gate(' not in s:
    s=s.replace(anchor,method+anchor,1)

# 3) Evaluate entry immediately after STRUCT0 state, using the same gate cache.
old2="""            williams_struct=self._williams_structure_from_gate(gate,entry_price=_wentry)\n"""
new2="""            williams_struct=self._williams_structure_from_gate(gate,entry_price=_wentry)\n            williams_entry_eval=self._williams_entry_from_gate(sym,gate,finder_rank=f.get('rank'))\n"""
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V105 structure-from-gate call')
s=s.replace(old2,new2,1)

# 4) Expose the exact fields consumed by _williams_mock_auto_step plus compact diagnostics.
old3="""                'williams_struct_state':williams_struct.get('state'),\n                'williams_support':williams_struct.get('support'),\n                'williams_support_updates':williams_struct.get('support_updates'),\n                'williams_exit_ready':bool(williams_struct.get('break')),\n                'williams_structure_shadow':williams_struct,\n"""
new3="""                'williams_entry':bool(williams_entry_eval.get('signal')),\n                'williams_signal_entry':bool(williams_entry_eval.get('signal')),\n                'williams_entry_stage':williams_entry_eval.get('stage'),\n                'williams_entry_trigger':williams_entry_eval.get('trigger'),\n                'williams_entry_rsi2':williams_entry_eval.get('rsi2'),\n                'williams_entry_raw_cross':bool(williams_entry_eval.get('raw_cross')),\n                'williams_entry_eval':williams_entry_eval,\n                'williams_struct_state':williams_struct.get('state'),\n                'williams_support':williams_struct.get('support'),\n                'williams_support_updates':williams_struct.get('support_updates'),\n                'williams_exit_ready':bool(williams_struct.get('break')),\n                'williams_structure_shadow':williams_struct,\n"""
if old3 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: Williams tracker telemetry block')
s=s.replace(old3,new3,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('ADDED_METHOD=_williams_entry_from_gate')
print('ENTRY_EVALUATOR=williams_live_evaluate_v23')
print('ENTRY_SOURCE=existing cached KOREA ka10080 bars')
print('EXTRA_BROKER_API_CALLS=0')
print('TRACKER_FIELDS=williams_entry,williams_signal_entry,williams_entry_stage,williams_entry_trigger,williams_entry_rsi2')
print('MOCK_AUTO_BRIDGE=V106 existing 1-share path')
print('REAL_BROKER_FALLBACK=NO')
