#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

# V113: move from 1-share plumbing tests to a bounded 1,000,000 KRW paper portfolio.
# - Dynamic sizing against an in-memory 1M KRW capital envelope.
# - Max 5 simultaneous Williams mock positions.
# - Sell exactly the quantity bought.
# - Ignore STRUCT0 exit during the first 5 minutes after entry, except emergency -1.5% hard stop.

old=r'''                r=b.buy_market(sym,1)
                order_no=r.get("ord_no") or r.get("order_no")
                self._last[key]={"in_pos":True,"buy_order_no":order_no}
'''
new=r'''                import time as _time
                capital=float(os.getenv("WILLIAMS_MOCK_CAPITAL_KRW","1000000") or 1000000)
                max_positions=max(1,int(os.getenv("WILLIAMS_MOCK_MAX_POSITIONS","5") or 5))
                price=_f(row.get("price"))
                if price<=0:
                    return

                # Reserve capital for positions opened by this bridge in the current process.
                reserved=0.0
                open_count=0
                for _k,_st in list(self._last.items()):
                    if not (isinstance(_k,tuple) and len(_k)>=2 and _k[0]=="WILLIAMS_MOCK"):
                        continue
                    if not isinstance(_st,dict) or not _st.get("in_pos"):
                        continue
                    open_count+=1
                    reserved += _f(_st.get("entry_price"))*_f(_st.get("qty"),1)
                if open_count>=max_positions:
                    return
                available=max(0.0,capital-reserved)
                if available < price:
                    return
                slot_budget=min(capital/max_positions,available)
                qty=int(slot_budget//price)
                if qty<1:
                    qty=1
                if qty*price>available:
                    return

                r=b.buy_market(sym,qty)
                order_no=r.get("ord_no") or r.get("order_no")
                self._last[key]={
                    "in_pos":True,
                    "buy_order_no":order_no,
                    "qty":qty,
                    "entry_price":price,
                    "entered_ts":_time.time(),
                }
'''
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V112 accepted-buy core')
s=s.replace(old,new,1)

old2=r'''                _logging.warning("WILLIAMS_MOCK_BUY_ACCEPTED sym=%s qty=1 order_no=%s struct5=%s",sym,order_no,bool(row.get("williams_struct5_signal")))
                self.store.event("KOREA",sym,"WILLIAMS_MOCK_BUY",None,"ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock BUY 1',payload={"order":r,"row":row})
'''
new2=r'''                _logging.warning("WILLIAMS_MOCK_BUY_ACCEPTED sym=%s qty=%s price=%s order_no=%s struct5=%s",sym,qty,price,order_no,bool(row.get("williams_struct5_signal")))
                self.store.event("KOREA",sym,"WILLIAMS_MOCK_BUY",None,"ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock BUY {qty}',payload={"order":r,"row":row,"qty":qty,"entry_price":price})
'''
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V112 buy accepted log')
s=s.replace(old2,new2,1)

old3=r'''            elif exit_ready and in_pos:
                r=b.sell_market(sym,1)
                self._last[key]={"in_pos":False,"sell_order_no":r.get("ord_no") or r.get("order_no")}
                self.store.event("KOREA",sym,"WILLIAMS_MOCK_SELL","HOLD","ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock SELL 1',payload={"order":r,"row":row})
'''
new3=r'''            elif exit_ready and in_pos:
                import time as _time
                qty=max(1,int(_f(st.get("qty"),1)))
                entry_price=_f(st.get("entry_price"))
                price=_f(row.get("price"))
                entered_ts=_f(st.get("entered_ts"))
                hold_sec=(_time.time()-entered_ts) if entered_ts else 999999.0
                hard_stop=bool(entry_price and price and price<=entry_price*0.985)

                # STRUCT0 support may include bars formed before this fresh entry.
                # Give the post-entry 1m structure five minutes to form; emergency stop remains live.
                if hold_sec < 300.0 and not hard_stop:
                    return

                r=b.sell_market(sym,qty)
                sell_order_no=r.get("ord_no") or r.get("order_no")
                self._last[key]={"in_pos":False,"sell_order_no":sell_order_no,"qty":qty,"entry_price":entry_price,"entered_ts":entered_ts}
                import logging as _logging
                _logging.warning("WILLIAMS_MOCK_SELL_ACCEPTED sym=%s qty=%s price=%s hold_sec=%.1f hard_stop=%s order_no=%s",sym,qty,price,hold_sec,hard_stop,sell_order_no)
                self.store.event("KOREA",sym,"WILLIAMS_MOCK_SELL","HOLD","ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock SELL {qty}',payload={"order":r,"row":row,"qty":qty,"entry_price":entry_price,"hold_sec":hold_sec,"hard_stop":hard_stop})
'''
if old3 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: mock sell block')
s=s.replace(old3,new3,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('PAPER_CAPITAL_KRW=1000000')
print('MAX_SIMULTANEOUS_POSITIONS=5')
print('POSITION_SIZE=DYNAMIC_AVAILABLE_CAPITAL')
print('SELL_QTY=MATCH_BUY_QTY')
print('MIN_HOLD_FOR_STRUCT_EXIT=5m')
print('EMERGENCY_STOP=-1.5%')
print('REAL_BROKER_FALLBACK=NO')
