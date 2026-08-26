#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

# V105 bugfix: the original patch checked for the method name after replacing
# the call-site, so the call itself caused the insertion guard to think the
# method already existed. Insert the actual method when its def is missing.

anchor="""    def _williams_structure_shadow(self,sym,korea,entry_price=None):\n"""
if anchor not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: _williams_structure_shadow anchor')

method=r'''    def _williams_structure_from_gate(self,gate,entry_price=None):
        """Build frozen STRUCT0 state from already-fetched KR gate bars.

        No API call. No order side effect. Reuses gate['bars_raw'].
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
            empty['error_msg']=str(e)[:200]
            return empty

'''

if '    def _williams_structure_from_gate(' not in s:
    s=s.replace(anchor,method+anchor,1)

# Ensure call-site is wired to the reuse method.
old_call="""            williams_struct=self._williams_structure_shadow(sym,korea,entry_price=_wentry)\n"""
new_call="""            williams_struct=self._williams_structure_from_gate(gate,entry_price=_wentry)\n"""
if old_call in s:
    s=s.replace(old_call,new_call,1)

# Ensure the gate exposes reusable OHLC bars.
needle="""                'latest_price':_f(a.get('close')),\n\n                'latest_5m':five.iloc[-1]['dt'].isoformat(),\n"""
replacement="""                'latest_price':_f(a.get('close')),\n\n                'bars_raw':b[['open','high','low','close']].tail(240).to_dict('records'),\n\n                'latest_5m':five.iloc[-1]['dt'].isoformat(),\n"""
if "'bars_raw':b[['open','high','low','close']]" not in s:
    if needle not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: gate reusable bars block')
    s=s.replace(needle,replacement,1)

if '    def _williams_structure_from_gate(' not in s:
    raise SystemExit('VERIFY_FAIL: method still missing')
if 'williams_struct=self._williams_structure_from_gate(gate,entry_price=_wentry)' not in s:
    raise SystemExit('VERIFY_FAIL: call-site not wired')

if s!=orig:
    P.write_text(s)
    print('PATCHED live_server/v4_engine.py')
else:
    print('ALREADY_PATCHED live_server/v4_engine.py')
print('ADDED_METHOD=_williams_structure_from_gate')
print('WILLIAMS_SOURCE=KOREA_SHADOW_GATE_REUSE')
print('SECOND_WILLIAMS_CHART_CALL=NO')
print('ORDER_BEHAVIOR_CHANGED=NO')
