from pathlib import Path

P=Path('app_v5.py')
s=P.read_text()

MARK='# ===== UI FUJIMOTO TOP5 V58 FIX ====='
if MARK in s:
    print('UI_FUJIMOTO_TOP5_V58_FIX_ALREADY_APPLIED')
    raise SystemExit(0)

helper='''\n# ===== UI FUJIMOTO TOP5 V58 FIX =====\ndef fujimoto_top5_rows_korea(base_rows):\n    if not isinstance(base_rows,list):\n        base_rows=[]\n    x=api('/api/v5/fujimoto-auto-v4/KOREA',5)\n    fuji=(x.get('rows') or []) if isinstance(x,dict) else []\n    fmap={str(r.get('symbol') or '').upper():r for r in fuji if r.get('symbol')}\n    out=[]\n    seen=set()\n    # evaluated Fujimoto rows first, by trade_priority\n    ranked=[r for r in fuji if r.get('trade_priority') is not None]\n    ranked.sort(key=lambda r: f(r.get('trade_priority'),-1),reverse=True)\n    bmap={str(r.get('symbol') or '').upper():r for r in base_rows if r.get('symbol')}\n    for fr in ranked:\n        sym=str(fr.get('symbol') or '').upper()\n        if not sym or sym in seen: continue\n        merged=dict(bmap.get(sym) or {})\n        merged.update(fr)\n        merged['state']=fr.get('engine_state') or merged.get('state')\n        merged['prototype_action']={'ENTRY':'BUY_REVIEW','ENTRY_READY':'WAIT','PREPARE':'WATCH','WATCH':'WATCH','HOLD':'HOLD','PARTIAL_EXIT':'REDUCE_REVIEW','EXIT':'EXIT_REVIEW'}.get(str(fr.get('engine_state') or '').upper(), merged.get('prototype_action'))\n        out.append(merged); seen.add(sym)\n        if len(out)>=5: break\n    # fill remaining slots with original candidates\n    for r in base_rows:\n        sym=str(r.get('symbol') or '').upper()\n        if not sym or sym in seen: continue\n        out.append(dict(r)); seen.add(sym)\n        if len(out)>=5: break\n    return out\n'''

anchor='def render_trading(market):\n'
if anchor not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading')
s=s.replace(anchor,helper+'\n'+anchor,1)

old="    source=active or standby\n"
new="    source=active or standby\n    if market=='KOREA' and source:\n        source=fujimoto_top5_rows_korea(source)\n"
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: source line')
s=s.replace(old,new,1)

P.write_text(s)
print('UI_FUJIMOTO_TOP5_V58_FIX_OK')
