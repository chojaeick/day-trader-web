#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()

if '_williams_structure_state' not in s:
    raise SystemExit('V93_METHOD_NOT_FOUND: run williams_live_structure_patch_v93.py first')

# 1) Add a safe adapter that obtains the same 1m chart already used by KR shadow logic
#    and normalizes it for the frozen STRUCT0 evaluator. Telemetry only.
anchor='''    def _korea_shadow_gate(self,sym,korea,cache_seconds=45):\n'''
block=r'''
    def _williams_structure_shadow(self,sym,korea,entry_price=None):
        """Shadow-only adapter for frozen Williams STRUCT0 state.

        Reads KR 1m bars and returns HOLD / EXIT_READY telemetry.
        Never places orders and never changes production state.
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
            d=korea.minute_chart(sym,1,max_pages=1)
            if isinstance(d,pd.DataFrame):
                b1=d.copy()
            else:
                raw=d
                if isinstance(d,dict):
                    raw=(d.get('rows') or d.get('data') or d.get('output2') or
                         d.get('output') or d.get('items') or [])
                b1=pd.DataFrame(raw or [])
            if b1.empty:
                return empty

            # Normalize common Kiwoom/engine field names without assuming one response shape.
            aliases={
                'open':['open','stck_oprc','시가'],
                'high':['high','stck_hgpr','고가'],
                'low':['low','stck_lwpr','저가'],
                'close':['close','stck_prpr','현재가','price'],
            }
            ren={}
            for dst,cands in aliases.items():
                if dst in b1.columns: continue
                for c in cands:
                    if c in b1.columns:
                        ren[c]=dst; break
            if ren:b1=b1.rename(columns=ren)
            need={'open','high','low','close'}
            if not need.issubset(set(b1.columns)):
                empty['state']='DATA_INVALID'
                empty['columns']=[str(x) for x in list(b1.columns)[:30]]
                return empty

            # If a time-like column exists, force chronological order.
            for tc in ('datetime','timestamp','ts','time','체결시간','stck_cntg_hour'):
                if tc in b1.columns:
                    try:b1=b1.sort_values(tc).reset_index(drop=True)
                    except Exception:pass
                    break

            out=self._williams_structure_state(b1,entry_price=entry_price)
            out['shadow_only']=True
            out['orders_enabled']=False
            return out
        except Exception as e:
            empty['state']='DATA_INVALID'
            empty['error']=type(e).__name__
            return empty

'''
if '_williams_structure_shadow' not in s:
    if anchor not in s:
        raise SystemExit('ANCHOR_KOREA_GATE_NOT_FOUND')
    s=s.replace(anchor,block+anchor,1)

# 2) Wire shadow telemetry into refresh_korea_tracker after the existing KR gate call.
needle="""            gate=self._korea_shadow_gate(sym,korea)\n"""
insert="""            gate=self._korea_shadow_gate(sym,korea)\n\n            # WILLIAMS STRUCT0 V94 SHADOW ONLY: no state/order authority.\n            _wpos=pmap.get(sym) or {}\n            _wentry=_f(_wpos.get('avg_entry')) or None\n            williams_struct=self._williams_structure_shadow(sym,korea,entry_price=_wentry)\n"""
if 'williams_struct=self._williams_structure_shadow' not in s:
    if needle not in s:
        raise SystemExit('TRACKER_GATE_CALL_NOT_FOUND')
    s=s.replace(needle,insert,1)

# 3) Expose compact top-level telemetry fields on each KR tracker row.
needle2="""                'prototype_reason':proto_reason,\n                'components':{\n"""
insert2="""                'prototype_reason':proto_reason,\n\n                # Williams STRUCT0 shadow telemetry (diagnostic only).\n                'williams_struct_state':williams_struct.get('state'),\n                'williams_support':williams_struct.get('support'),\n                'williams_support_updates':williams_struct.get('support_updates'),\n                'williams_exit_ready':bool(williams_struct.get('break')),\n                'williams_structure_shadow':williams_struct,\n\n                'components':{\n"""
if "'williams_struct_state':williams_struct.get('state')" not in s:
    if needle2 not in s:
        raise SystemExit('TRACKER_ROW_ANCHOR_NOT_FOUND')
    s=s.replace(needle2,insert2,1)

P.write_text(s)
print('PATCHED',P)
print('ADDED_METHOD=_williams_structure_shadow')
print('WIRED=KOREA_TRACKER_SHADOW')
print('FIELDS=williams_struct_state,williams_support,williams_support_updates,williams_exit_ready')
print('ORDER_BEHAVIOR_CHANGED=NO')
