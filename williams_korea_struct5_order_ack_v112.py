#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

# V112: separate STRUCT5 signal detection from broker-order acknowledgement.
# V111 marked struct5_signal_sent before the mock broker actually accepted the order.
# If an order failed (e.g. rate limit), the signal was permanently consumed and never retried.

old=r'''                day_key=now_kst.strftime('%Y%m%d')
                s5=_WILLIAMS_STATE[(str(sym),day_key)]
                already=bool(s5.get('struct5_signal_sent'))
                struct5_signal=bool(fresh_break and struct5_higher_low and rank_ok and rsi_ok and not already)
                if struct5_signal:
                    s5['struct5_signal_sent']=True
                    s5['struct5_confirmed_at']=now_kst
                    out['signal']=True
                    out['stage']='ENTRY_CANDIDATE'
                    struct5_reason='FRESH_5BAR_BREAKOUT'
                elif already:
                    struct5_reason='ALREADY_SENT'
'''
new=r'''                day_key=now_kst.strftime('%Y%m%d')
                s5=_WILLIAMS_STATE[(str(sym),day_key)]
                already=bool(s5.get('struct5_order_sent'))
                struct5_signal=bool(fresh_break and struct5_higher_low and rank_ok and rsi_ok and not already)
                if struct5_signal:
                    # Detection only. Do NOT consume the signal here.
                    # Broker acknowledgement in _williams_mock_auto_step owns struct5_order_sent.
                    s5['struct5_last_detected_at']=now_kst
                    out['signal']=True
                    out['stage']='ENTRY_CANDIDATE'
                    struct5_reason='FRESH_5BAR_BREAKOUT_PENDING_ORDER'
                elif already:
                    struct5_reason='ORDER_ACKED'
'''
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V111 struct5 sent block')
s=s.replace(old,new,1)

# On successful mock BUY, acknowledge the STRUCT5 signal and emit a real journal line.
old2=r'''            if entry and not in_pos:
                r=b.buy_market(sym,1)
                self._last[key]={"in_pos":True,"buy_order_no":r.get("ord_no") or r.get("order_no")}
                self.store.event("KOREA",sym,"WILLIAMS_MOCK_BUY",None,"ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock BUY 1',payload={"order":r,"row":row})
'''
new2=r'''            if entry and not in_pos:
                # Retry guard: avoid hammering Kiwoom if a pending breakout survives multiple refreshes.
                import time as _time
                retry_key=("WILLIAMS_MOCK_RETRY",sym)
                last_try=self._last.get(retry_key) or {}
                if (_time.time()-_f(last_try.get("ts"),0)) < 15.0:
                    return
                self._last[retry_key]={"ts":_time.time()}
                r=b.buy_market(sym,1)
                order_no=r.get("ord_no") or r.get("order_no")
                self._last[key]={"in_pos":True,"buy_order_no":order_no}
                if row.get("williams_struct5_signal"):
                    day_key=_dt.now(_WILLIAMS_KST).strftime('%Y%m%d')
                    s5=_WILLIAMS_STATE[(str(sym),day_key)]
                    s5['struct5_order_sent']=True
                    s5['struct5_order_no']=order_no
                    s5['struct5_order_acked_at']=_dt.now(_WILLIAMS_KST)
                import logging as _logging
                _logging.warning("WILLIAMS_MOCK_BUY_ACCEPTED sym=%s qty=1 order_no=%s struct5=%s",sym,order_no,bool(row.get("williams_struct5_signal")))
                self.store.event("KOREA",sym,"WILLIAMS_MOCK_BUY",None,"ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock BUY 1',payload={"order":r,"row":row})
'''
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: mock buy block')
s=s.replace(old2,new2,1)

# Emit failures to journal as well as DB, so live diagnosis is visible immediately.
old3=r'''        except Exception as e:
            self.store.event("KOREA",sym,"WILLIAMS_MOCK_ERROR",None,"ERROR",power=_f(row.get("power")),message=str(e),payload={"row":row})
'''
new3=r'''        except Exception as e:
            import logging as _logging
            _logging.exception("WILLIAMS_MOCK_ERROR sym=%s error=%s",sym,e)
            self.store.event("KOREA",sym,"WILLIAMS_MOCK_ERROR",None,"ERROR",power=_f(row.get("power")),message=str(e),payload={"row":row})
'''
if old3 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: mock error block')
s=s.replace(old3,new3,1)

# Add ack diagnostic to tracker output if the struct5 block exists.
old4="""                'williams_struct5_reason':williams_entry_eval.get('struct5_reason'),\n                'williams_entry_eval':williams_entry_eval,\n"""
new4="""                'williams_struct5_reason':williams_entry_eval.get('struct5_reason'),\n                'williams_struct5_order_acked':bool(_WILLIAMS_STATE[(str(sym),_dt.now(_WILLIAMS_KST).strftime('%Y%m%d'))].get('struct5_order_sent')),\n                'williams_entry_eval':williams_entry_eval,\n"""
if old4 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: struct5 telemetry block')
s=s.replace(old4,new4,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('V112_SIGNAL_CONSUMPTION=BROKER_ACK_ONLY')
print('STRUCT5_FAILED_ORDER_RETRY=YES_15S_COOLDOWN')
print('JOURNAL_BUY_ACCEPT_LOG=YES')
print('JOURNAL_ERROR_TRACE=YES')
print('ORDER_QTY=1')
print('REAL_BROKER_FALLBACK=NO')
