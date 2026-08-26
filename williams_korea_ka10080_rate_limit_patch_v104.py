#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

# Add a shared KR minute-chart cache/throttle helper before Williams structure shadow.
anchor='    def _williams_structure_shadow(self,sym,korea,entry_price=None):\n'
helper=r'''    def _kr_minute_chart_cached(self,sym,korea,interval=1,cache_seconds=8.0,min_spacing=0.24):
        """Shared KR chart cache + throttle for ka10080.

        Keeps the frozen Williams/shadow logic unchanged while avoiding burst
        duplicate requests that exceed Kiwoom's per-API flow limit.
        """
        import time as _time
        key=(str(sym),int(interval))
        cache=getattr(self,'_kr_chart_cache_v104',None)
        if cache is None:
            cache={}
            self._kr_chart_cache_v104=cache
        now=_time.monotonic()
        hit=cache.get(key)
        if hit and (now-hit[0]) < float(cache_seconds):
            return hit[1]

        last=float(getattr(self,'_kr_chart_last_call_v104',0.0) or 0.0)
        wait=float(min_spacing)-(now-last)
        if wait>0:
            _time.sleep(wait)
        data=korea.minute_chart(sym,int(interval),max_pages=1)
        self._kr_chart_last_call_v104=_time.monotonic()
        cache[key]=(self._kr_chart_last_call_v104,data)
        return data

'''
if '_kr_minute_chart_cached' not in s:
    if anchor not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: _williams_structure_shadow')
    s=s.replace(anchor,helper+anchor,1)

# Route KR 1m/5m chart calls through the shared cache/throttle.
repls={
    'korea.minute_chart(sym,1,max_pages=1)':'self._kr_minute_chart_cached(sym,korea,1)',
    'korea.minute_chart(sym,5,max_pages=1)':'self._kr_minute_chart_cached(sym,korea,5)',
}
count=0
for old,new in repls.items():
    n=s.count(old)
    if n:
        s=s.replace(old,new)
        count+=n

if s==orig:
    raise SystemExit('NO_CHANGE')
if count==0:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: KR minute_chart calls')

P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('KR_KA10080_CACHE_TTL=8s')
print('KR_KA10080_MIN_SPACING=0.24s')
print('KR_CHART_CALLS_REWIRED=',count)
print('ORDER_BEHAVIOR_CHANGED=NO')
