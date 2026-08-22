from pathlib import Path

p=Path('/home/ubuntu/day-trader-api/app_v5.py')
s=p.read_text()

old="avg=f(p.get('avg_price') or p.get('entry_price')); qty=f(p.get('qty') or p.get('quantity'))"
new="avg=f(p.get('avg_entry') or p.get('avg_price') or p.get('entry_price')); qty=f(p.get('qty') or p.get('quantity'))"
if old not in s:
    raise SystemExit('avg field anchor missing')
s=s.replace(old,new,1)

old2="floor=p.get('hard_floor') or p.get('floor') or p.get('dynamic_floor')"
new2="floor=p.get('current_floor') or p.get('hard_floor') or p.get('initial_floor') or p.get('floor') or p.get('dynamic_floor')"
if old2 not in s:
    raise SystemExit('floor field anchor missing')
s=s.replace(old2,new2,1)

old3="ceiling=p.get('dynamic_ceiling') or p.get('ceiling')"
new3="ceiling=p.get('dynamic_ceiling') or p.get('ceiling')"
# no-op anchor retained intentionally
if old3 not in s:
    raise SystemExit('ceiling anchor missing')

old4="""        if live and not (p.get('current_price') or p.get('price')):\n            p=dict(p); p['current_price']=live.get('price') or live.get('current_price')\n        avg,qty,cur,pnl,pct,floor,ceiling,t1,t2=position_values(p,market)\n        c1,c2,c3,c4,c5=st.columns(5)\n        c1.metric(sym,f'{qty:,.0f}주'); c2.metric('현재가',money(cur,market)); c3.metric('평단',money(avg,market)); c4.metric('평가손익',money(pnl,market),f'{pct:+.2f}%'); c5.metric('판단',action_ko(action_of(p)))\n"""
new4="""        p=dict(p)\n        if live:\n            if not (p.get('current_price') or p.get('price')):\n                p['current_price']=live.get('price') or live.get('current_price')\n            p['state']=live.get('state') or p.get('state')\n            p['prototype_action']=live.get('prototype_action') or live.get('proto_action') or p.get('prototype_action')\n            p['entry_gate']=live.get('entry_gate') or p.get('entry_gate')\n            p['risk']=live.get('risk') or live.get('risk_level') or p.get('risk')\n            p['power']=live.get('power') if live.get('power') is not None else p.get('power')\n        avg,qty,cur,pnl,pct,floor,ceiling,t1,t2=position_values(p,market)\n        c1,c2,c3,c4,c5=st.columns(5)\n        c1.metric(sym,f'{qty:,.0f}주'); c2.metric('현재가',money(cur,market)); c3.metric('평단',money(avg,market)); c4.metric('평가손익',money(pnl,market),f'{pct:+.2f}%'); c5.metric('판단',action_ko(action_of(p)))\n"""
if old4 not in s:
    raise SystemExit('position merge anchor missing')
s=s.replace(old4,new4,1)

old5="""        st.markdown(f'''<div class=\"v5-card\"><div class=\"v5-kicker\">REGISTERED POSITION</div><div><b>Floor</b> {money(floor,market) if floor is not None else '-'} · <b>Ceiling</b> {money(ceiling,market) if ceiling is not None else '-'} · <b>T1</b> {money(t1,market) if t1 is not None else '-'} · <b>T2</b> {money(t2,market) if t2 is not None else '-'}</div></div>''',unsafe_allow_html=True)\n"""
new5="""        warning=p.get('warning_floor')\n        mode=p.get('floor_mode') or '-'\n        st.markdown(f'''<div class=\"v5-card\"><div class=\"v5-kicker\">REGISTERED POSITION</div><div><b>Floor</b> {money(floor,market) if floor is not None else '-'} · <b>Warning</b> {money(warning,market) if warning is not None else '-'} · <b>Mode</b> {mode} · <b>Ceiling</b> {money(ceiling,market) if ceiling is not None else '-'} · <b>T1</b> {money(t1,market) if t1 is not None else '-'} · <b>T2</b> {money(t2,market) if t2 is not None else '-'}</div></div>''',unsafe_allow_html=True)\n"""
if old5 not in s:
    raise SystemExit('position card anchor missing')
s=s.replace(old5,new5,1)

p.write_text(s)
print('PATCH_OK')
print('AVG_ENTRY_FIELD', "p.get('avg_entry')" in s)
print('CURRENT_FLOOR_FIELD', "p.get('current_floor')" in s)
print('TRACKER_ACTION_MERGE', "p['prototype_action']=live.get('prototype_action')" in s)
