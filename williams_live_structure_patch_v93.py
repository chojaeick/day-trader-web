#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
anchor='''    def _korea_shadow_gate(self,sym,korea,cache_seconds=45):\n'''
if anchor not in s:
    raise SystemExit('ANCHOR_NOT_FOUND')
block=r'''
    def _williams_structure_state(self,b1,entry_price=None):
        """Frozen Williams STRUCT0 live state candidate.

        Causal only:
        - confirm swing-low with 2 bars to the right
        - support may only ratchet upward
        - HOLD while latest close >= confirmed support
        - EXIT_READY when latest close < support
        No RSI/CCI/MACD exit and no re-entry logic.
        """
        out={
            'mode':'STRUCT0_FROZEN_V92',
            'state':'WATCH',
            'support':None,
            'support_updates':0,
            'break':False,
            'entry_price':_f(entry_price) if entry_price else None,
            'last_close':None,
            'bars':0,
        }
        if b1 is None or len(b1)<7:
            return out
        try:
            b=b1.copy().reset_index(drop=True)
            for col in ('open','high','low','close'):
                b[col]=pd.to_numeric(b[col],errors='coerce')
            b=b.dropna(subset=['high','low','close']).reset_index(drop=True)
            out['bars']=len(b)
            if len(b)<7:return out

            support=None; updates=0
            # A swing at j is only usable when j+2 exists: fully causal.
            for i in range(4,len(b)):
                j=i-2
                lo=_f(b.iloc[j]['low'])
                if lo<=0:continue
                window=[_f(b.iloc[k]['low'],float('inf')) for k in range(j-2,j+3)]
                if lo<=min(window):
                    if support is None:
                        support=lo
                    elif lo>support:
                        support=lo; updates+=1
            last=_f(b.iloc[-1]['close'])
            out['last_close']=last or None
            out['support']=support
            out['support_updates']=updates
            if support is None or last<=0:
                return out
            br=bool(last<support)
            out['break']=br
            out['state']='EXIT_READY' if br else 'HOLD'
            return out
        except Exception as e:
            out['state']='DATA_INVALID'
            out['error']=type(e).__name__
            return out

'''
if '_williams_structure_state' not in s:
    s=s.replace(anchor,block+anchor,1)

# Extend Korea row with shadow-only Williams structure state, without changing order behavior.
needle="""        return {'market':'USA','symbol':sym"""
# no direct Korea-row mutation here; method is added only. Runtime wiring is a separate safe step.
P.write_text(s)
print('PATCHED',P)
print('ADDED_METHOD=_williams_structure_state')
print('ORDER_BEHAVIOR_CHANGED=NO')
