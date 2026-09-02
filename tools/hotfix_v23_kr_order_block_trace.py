from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()

repls=[
("""                if sym not in _held_syms and len(_held_syms)>=_max_positions:\n                    return\n""",
 """                if sym not in _held_syms and len(_held_syms)>=_max_positions:\n                    import logging as _logging\n                    _logging.warning(\"V23_KR_ORDER_BLOCK sym=%s reason=MAX_POSITIONS held=%s\",sym,len(_held_syms))\n                    return\n"""),
("""                if not isinstance(_bal,dict) or _cache_age>=15.0:\n                    return\n""",
 """                if not isinstance(_bal,dict) or _cache_age>=15.0:\n                    import logging as _logging\n                    _logging.warning(\"V23_KR_ORDER_BLOCK sym=%s reason=ACCOUNT_CACHE cache_type=%s cache_age=%.3f\",sym,type(_bal).__name__,_cache_age)\n                    return\n"""),
("""                    if self._last.get(_reb_key):\n                        return\n""",
 """                    if self._last.get(_reb_key):\n                        import logging as _logging\n                        _logging.warning(\"V23_KR_ORDER_BLOCK sym=%s reason=REBALANCE_ALREADY_ATTEMPTED source=%s\",sym,_src['symbol'])\n                        return\n"""),
("""                if qty<1:\n                    return\n""",
 """                if qty<1:\n                    import logging as _logging\n                    _logging.warning(\"V23_KR_ORDER_BLOCK sym=%s reason=QTY_LT_1 cash=%s price=%s budget=%s\",sym,_cash,price,_budget)\n                    return\n"""),
("""                r=b.buy_market(sym,qty)\n""",
 """                import logging as _logging\n                _logging.warning(\"V23_KR_BUY_SUBMIT sym=%s qty=%s cash=%s price=%s total=%s\",sym,qty,_cash,price,_total)\n                r=b.buy_market(sym,qty)\n                _logging.warning(\"V23_KR_BUY_RESPONSE sym=%s resp=%s\",sym,r)\n"""),
]

changed=0
for old,new in repls:
    if old in s and new not in s:
        s=s.replace(old,new,1)
        changed+=1

if changed==0:
    print('V23 KR ORDER BLOCK TRACE ALREADY CONNECTED OR NO MATCH')
else:
    p.write_text(s)
    print(f'V23 KR ORDER BLOCK TRACE CONNECTED ({changed} patches)')
