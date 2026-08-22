from pathlib import Path

p=Path('/home/ubuntu/day-trader-api/app_v5.py')
s=p.read_text()

old="""def api(path, timeout=10):\n    if not API_URL:\n        return {'ok': False, 'error': 'DAYTRADER_API_URL is empty'}\n    try:\n        r = requests.get(API_URL + path, timeout=timeout)\n        r.raise_for_status()\n        return r.json()\n    except Exception as e:\n        return {'ok': False, 'error': str(e)}\n"""
new=old+"""\n\ndef post(path, payload, timeout=10):\n    if not API_URL:\n        return {'ok': False, 'error': 'DAYTRADER_API_URL is empty'}\n    try:\n        r = requests.post(API_URL + path, json=payload, timeout=timeout)\n        r.raise_for_status()\n        return r.json()\n    except Exception as e:\n        return {'ok': False, 'error': str(e)}\n"""
if 'def post(path, payload' not in s:
    if old not in s: raise SystemExit('api anchor missing')
    s=s.replace(old,new,1)

old2="""    actual=qty*buy_px\n    st.caption(f'예상 체결금액 {money(actual,market)} · 잔여금액 {money(max(amount-actual,0),market)}')\n    st.warning('수동 주문 전용 · 이 화면은 실제 주문을 전송하지 않습니다.')\n"""
new2="""    actual=qty*buy_px\n    st.caption(f'예상 체결금액 {money(actual,market)} · 잔여금액 {money(max(amount-actual,0),market)}')\n    st.warning('수동 주문 전용 · 이 버튼은 증권사 주문이 아니라 앱의 보유 포지션 장부에만 등록합니다.')\n    if st.button('이 매수를 실제 보유 단타로 등록', disabled=(qty<=0 or buy_px<=0), key=f'reg_{market}_{symbol}'):\n        result=post('/api/v4/position/buy', {'market':market,'symbol':symbol,'qty':qty,'price':buy_px,'note':'V5 manual registration'})\n        if result.get('ok'):\n            st.success(f'{symbol} {qty:,}주 @ {money(buy_px,market)} 등록 완료')\n            st.rerun()\n        else:\n            st.error(f\"등록 실패: {result.get('error') or result}\")\n"""
if old2 not in s: raise SystemExit('buy box anchor missing')
s=s.replace(old2,new2,1)

old3="pos_rows=positions.get('rows') if isinstance(positions,dict) else None"
new3="pos_rows=positions.get('data') if isinstance(positions,dict) else None"
if old3 not in s: raise SystemExit('positions response anchor missing')
s=s.replace(old3,new3,1)

old4="""        shown+=1\n        sym=p.get('symbol') or '-'; avg,qty,cur,pnl,pct,floor,ceiling,t1,t2=position_values(p,market)\n"""
new4="""        shown+=1\n        sym=p.get('symbol') or '-'\n        live=next((r for r in tracker if str(r.get('symbol'))==str(sym)), None)\n        if live and not (p.get('current_price') or p.get('price')):\n            p=dict(p); p['current_price']=live.get('price') or live.get('current_price')\n        avg,qty,cur,pnl,pct,floor,ceiling,t1,t2=position_values(p,market)\n"""
if old4 not in s: raise SystemExit('position loop anchor missing')
s=s.replace(old4,new4,1)

p.write_text(s)
print('PATCH_OK')
print('POSITIONS_API_USES_DATA', "positions.get('data')" in s)
print('MANUAL_REGISTER_BUTTON', '이 매수를 실제 보유 단타로 등록' in s)
