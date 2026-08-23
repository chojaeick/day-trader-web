from pathlib import Path
import re

APP=Path('app_v5.py')

HELPER=r'''

# ===== UI FUJIMOTO TOP5 V58 =====
@st.cache_data(ttl=8,show_spinner=False)
def fujimoto_top5_rows_korea():
    x=api('/api/v5/fujimoto-auto-v4/KOREA',5)
    if not isinstance(x,dict) or not x.get('ok',False):
        return []
    rows=x.get('rows') or []
    out=[]
    for r in rows:
        if not isinstance(r,dict):
            continue
        if r.get('fujimoto_score') is None or r.get('trade_priority') is None:
            continue
        out.append(dict(r))
    out.sort(key=lambda r: float(r.get('trade_priority') or -1), reverse=True)
    return out


def fujimoto_merge_rank_ui(source,market):
    src=[dict(r) for r in (source or []) if isinstance(r,dict)]
    if market!='KOREA':
        return src
    fuji=fujimoto_top5_rows_korea()
    if not fuji:
        return src
    by_src={str(r.get('symbol') or '').upper():r for r in src}
    merged=[];seen=set()
    for fr in fuji:
        sym=str(fr.get('symbol') or '').upper()
        if not sym or sym in seen:
            continue
        base=dict(by_src.get(sym) or {})
        base.update({k:v for k,v in fr.items() if v is not None})
        base['symbol']=sym
        base['name']=fr.get('name') or base.get('name') or sym
        base['price']=base.get('price') or base.get('current_price')
        base['state']=fr.get('engine_state') or base.get('state')
        sig=str(fr.get('signal') or '').upper()
        if sig in ('ENTRY','ENTRY_CANDIDATE'):
            base['prototype_action']='BUY_REVIEW'
        elif sig in ('READY','PREPARE','WATCH','NONE'):
            base['prototype_action']='WAIT' if sig in ('READY','PREPARE') else 'WATCH'
        elif sig in ('PARTIAL_EXIT','REDUCE'):
            base['prototype_action']='REDUCE_REVIEW'
        elif sig in ('EXIT','HARD_EXIT'):
            base['prototype_action']='EXIT_REVIEW'
        base['power']=fr.get('fujimoto_score')
        base['reason']='Fujimoto '+str(fr.get('engine_state') or '-')+' · Score '+str(fr.get('fujimoto_score'))+' · Priority '+str(fr.get('trade_priority'))
        merged.append(base);seen.add(sym)
    for r in src:
        sym=str(r.get('symbol') or '').upper()
        if sym and sym not in seen:
            merged.append(r);seen.add(sym)
    return merged
'''


def main():
    s=APP.read_text()
    if 'UI FUJIMOTO TOP5 V58' not in s:
        anchor='def render_trading(market):'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading')
        s=s.replace(anchor,HELPER+'\n'+anchor,1)
    old="    source=active or standby\n"
    new="    source=active or standby\n    source=fujimoto_merge_rank_ui(source,market)\n"
    if 'source=fujimoto_merge_rank_ui(source,market)' not in s:
        if old not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: source assignment')
        s=s.replace(old,new,1)
    APP.write_text(s)
    print('UI_FUJIMOTO_TOP5_V58_OK')

if __name__=='__main__':
    main()
