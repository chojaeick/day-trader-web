#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

# Reuse the already healthy KR shadow-gate minute bars for Williams STRUCT0.
# This avoids a second parsing path / second chart call that can leave Williams in DATA_WAIT
# even when shadow_gate has bars_1m=900 and a valid latest_price.

old_call="""            williams_struct=self._williams_structure_shadow(sym,korea,entry_price=_wentry)\n"""
new_call="""            williams_struct=self._williams_structure_from_gate(gate,entry_price=_wentry)\n"""
if old_call not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: williams structure shadow call')
s=s.replace(old_call,new_call,1)

anchor="""    def _williams_structure_shadow(self,sym,korea,entry_price=None):\n"""
if anchor not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: _williams_structure_shadow anchor')

method=r'''    def _williams_structure_from_gate(self,gate,entry_price=None):
        """Build frozen STRUCT0 state from the already-fetched KR gate bars.

        No API call. No order side effect. Uses gate['bars_raw'] when available.
        Falls back to DATA_WAIT when the gate has no reusable bars.
        """
        empty={
            'mode':'STRUCT0_FROZEN_V92',
            'state':'DATA_WAIT',
            'support':None,
            'support_updates':0,
            'break':False,
            'entry_price':_f(entry_price) if entry_price else None,
            'last_close':None,
            'bars':0,
            'shadow_only':True,
            'orders_enabled':False,
        }
        try:
            raw=gate.get('bars_raw') if isinstance(gate,dict) else None
            if not raw:
                return empty
            b1=pd.DataFrame(raw)
            if b1.empty:
                return empty
            need={'open','high','low','close'}
            if not need.issubset(set(b1.columns)):
                empty['state']='DATA_INVALID'
                empty['columns']=[str(x) for x in list(b1.columns)[:30]]
                return empty
            out=self._williams_structure_state(b1,entry_price=entry_price)
            out['shadow_only']=True
            out['orders_enabled']=False
            out['source']='KOREA_SHADOW_GATE_REUSE'
            return out
        except Exception as e:
            empty['state']='DATA_INVALID'
            empty['error']=type(e).__name__
            return empty

'''
if '_williams_structure_from_gate' not in s:
    s=s.replace(anchor,method+anchor,1)

# Inject normalized chronological OHLC rows into the existing gate output.
# The gate already has dataframe b by this point and uses a=b.iloc[-1].
old="""                'latest_1m':str(a.get('time')),\n\n                'latest_price':_f(a.get('close')),\n\n                'latest_5m':five.iloc[-1]['dt'].isoformat(),\n"""
new="""                'latest_1m':str(a.get('time')),\n\n                'latest_price':_f(a.get('close')),\n\n                'bars_raw':b[['open','high','low','close']].tail(240).to_dict('records'),\n\n                'latest_5m':five.iloc[-1]['dt'].isoformat(),\n"""
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: gate latest_1m/latest_price block')
s=s.replace(old,new,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('WILLIAMS_SOURCE=REUSE_EXISTING_KOREA_SHADOW_GATE_BARS')
print('SECOND_WILLIAMS_CHART_CALL=REMOVED_FROM_TRACKER_PATH')
print('ORDER_BEHAVIOR_CHANGED=NO')
