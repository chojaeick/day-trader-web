from pathlib import Path
import re

API=Path('app_v5.py')

HELPERS=r'''

# ===== UI FUJIMOTO TOP5 V58 REGEX =====
def fujimoto_top5_rows_korea(base_rows, limit=5):
    try:
        x=api('/api/v5/fujimoto-auto-v4/KOREA',5)
        live=x.get('rows') or [] if isinstance(x,dict) else []
    except Exception:
        live=[]
    by={str(r.get('symbol') or '').upper():dict(r) for r in live}
    merged=[]; seen=set()
    # evaluated Fujimoto rows first, ordered by trade_priority
    ranked=[r for r in live if r.get('trade_priority') is not None]
    ranked.sort(key=lambda r: float(r.get('trade_priority') or -1e9), reverse=True)
    for r in ranked:
        sym=str(r.get('symbol') or '').upper()
        if not sym or sym in seen: continue
        seen.add(sym)
        z=dict(r)
        z['power']=r.get('trade_priority')
        z['state']=r.get('engine_state') or r.get('state')
        sig=str(r.get('signal') or '').upper()
        if sig=='ENTRY_CANDIDATE': z['prototype_action']='BUY_REVIEW'
        elif sig=='READY': z['prototype_action']='WAIT'
        elif sig in ('EXIT','HARD_EXIT'): z['prototype_action']='EXIT_REVIEW'
        elif sig=='PARTIAL_EXIT': z['prototype_action']='REDUCE_REVIEW'
        else: z['prototype_action']='WATCH'
        merged.append(z)
        if len(merged)>=limit: return merged
    # fill remaining slots from existing source
    for r in base_rows or []:
        sym=str(r.get('symbol') or '').upper()
        if not sym or sym in seen: continue
        seen.add(sym); merged.append(dict(r))
        if len(merged)>=limit: break
    return merged
'''

def main():
    s=API.read_text()
    if 'UI FUJIMOTO TOP5 V58 REGEX' not in s:
        # insert helpers immediately before render_trading
        m=re.search(r'\ndef render_trading\(market\):',s)
        if not m:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading')
        s=s[:m.start()]+HELPERS+s[m.start():]

    # robustly inject immediately after the first source assignment inside render_trading
    m=re.search(r'(def render_trading\(market\):.*?\n)(.*?)(\n\s*a,b,c,d=st\.columns\(4\))',s,re.S)
    if not m:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading preamble')
    body=m.group(2)
    if 'source=fujimoto_top5_rows_korea' not in body:
        # append override regardless of exact whitespace/original source expression
        body=body.rstrip()+"\n    if market=='KOREA':\n        source=fujimoto_top5_rows_korea(source,5)\n"
        s=s[:m.start()]+m.group(1)+body+m.group(3)+s[m.end():]

    API.write_text(s)
    print('UI_FUJIMOTO_TOP5_V58_REGEX_OK')

if __name__=='__main__': main()
