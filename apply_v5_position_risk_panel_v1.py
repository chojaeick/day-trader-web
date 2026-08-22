from pathlib import Path

p=Path('/home/ubuntu/day-trader-api/app_v5.py')
s=p.read_text()

anchor="""        avg,qty,cur,pnl,pct,floor,ceiling,t1,t2=position_values(p,market)\n        c1,c2,c3,c4,c5=st.columns(5)\n        c1.metric(sym,f'{qty:,.0f}주'); c2.metric('현재가',money(cur,market)); c3.metric('평단',money(avg,market)); c4.metric('평가손익',money(pnl,market),f'{pct:+.2f}%'); c5.metric('판단',action_ko(action_of(p)))\n"""

replacement="""        avg,qty,cur,pnl,pct,floor,ceiling,t1,t2=position_values(p,market)\n        initial_floor=p.get('initial_floor')\n        current_floor=p.get('current_floor') or floor\n        warning_floor=p.get('warning_floor')\n        floor_mode=p.get('floor_mode') or '-'\n        high_watermark=p.get('high_watermark')\n        live_action=action_of(p)\n\n        if current_floor is not None and cur>0 and cur<=f(current_floor):\n            management='손절/EXIT 검토'\n        elif warning_floor is not None and cur>0 and cur<=f(warning_floor):\n            management='경계 · 축소 검토'\n        elif live_action in {'EXIT_REVIEW','REDUCE_REVIEW'}:\n            management=action_ko(live_action)\n        else:\n            management=action_ko(live_action)\n\n        c1,c2,c3,c4,c5=st.columns(5)\n        c1.metric(sym,f'{qty:,.0f}주')\n        c2.metric('현재가',money(cur,market))\n        c3.metric('평단',money(avg,market))\n        c4.metric('평가손익',money(pnl,market),f'{pct:+.2f}%')\n        c5.metric('관리 판단',management)\n\n        r1,r2,r3,r4,r5=st.columns(5)\n        r1.metric('초기 Floor',money(initial_floor,market) if initial_floor is not None else '-')\n        r2.metric('현재 Floor',money(current_floor,market) if current_floor is not None else '-')\n        r3.metric('Warning Floor',money(warning_floor,market) if warning_floor is not None else '-')\n        r4.metric('Floor Mode',str(floor_mode))\n        r5.metric('고점',money(high_watermark,market) if high_watermark is not None else '-')\n"""

if anchor not in s:
    raise SystemExit('PATCH ABORTED: position metric anchor missing')
s=s.replace(anchor,replacement,1)

old="""        st.markdown(f'''<div class=\"v5-card\"><div class=\"v5-kicker\">REGISTERED POSITION</div><div><b>Floor</b> {money(floor,market) if floor is not None else '-'} · <b>Ceiling</b> {money(ceiling,market) if ceiling is not None else '-'} · <b>T1</b> {money(t1,market) if t1 is not None else '-'} · <b>T2</b> {money(t2,market) if t2 is not None else '-'}</div></div>''',unsafe_allow_html=True)\n"""
new="""        st.markdown(f'''<div class=\"v5-card\"><div class=\"v5-kicker\">POSITION RISK / EXIT MANAGEMENT</div><div><b>현재 관리</b> {management} · <b>Ceiling</b> {money(ceiling,market) if ceiling is not None else '-'} · <b>T1</b> {money(t1,market) if t1 is not None else '-'} · <b>T2</b> {money(t2,market) if t2 is not None else '-'}</div><div class=\"v5-muted\">앱 장부 기준 관리 정보이며 실제 증권사 주문은 전송하지 않습니다.</div></div>''',unsafe_allow_html=True)\n"""
if old in s:
    s=s.replace(old,new,1)

p.write_text(s)
print('PATCH_OK')
print('RISK_PANEL', 'Warning Floor' in s and '현재 Floor' in s)
print('EXIT_GUIDANCE', '손절/EXIT 검토' in s)
