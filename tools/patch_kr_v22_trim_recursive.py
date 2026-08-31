from pathlib import Path

p=Path('/home/ubuntu/day-trader-api/live_server/api.py')
s=p.read_text()

helper="""
def _v5_kr_trim_payload(obj):
    if isinstance(obj, dict):
        out={}
        for k,v in obj.items():
            if k in {'bars_raw','raw_bars'}:
                continue
            out[k]=_v5_kr_trim_payload(v)
        return out
    if isinstance(obj, list):
        return [_v5_kr_trim_payload(v) for v in obj]
    return obj

"""

marker='def _v5_kr_daytrade_entry_impl(limit=10,eval_limit=5,max_pages=1):'
if '_v5_kr_trim_payload' not in s:
    if marker not in s:
        raise SystemExit('KR daytrade helper marker not found')
    s=s.replace(marker,helper+marker,1)

target='        rows.append(x)\n'
replacement='        rows.append(_v5_kr_trim_payload(x))\n'
if replacement not in s:
    if target not in s:
        raise SystemExit('rows.append(x) target not found')
    start=s.find(marker)
    pos=s.find(target,start)
    if pos < 0:
        raise SystemExit('KR rows.append(x) not found after helper')
    s=s[:pos]+replacement+s[pos+len(target):]

p.write_text(s)
print('TRIM_RECURSIVE_OK')
