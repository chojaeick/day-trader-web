from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()

old_refresh='''        if getattr(self, "_williams_mock_account_synced", False):
            bal = broker.request_account(
                "kt00004",
                {"qry_tp":"0", "dmst_stex_tp":"KRX"},
            )
            self._williams_mock_account_cache = bal
            self._williams_mock_account_cache_mono = _time.monotonic()
            return
'''
new_refresh='''        if getattr(self, "_williams_mock_account_synced", False):
            cash_bal = broker.request_account(
                "kt00001",
                {"qry_tp":"2"},
            )
            bal = dict(getattr(self, '_williams_mock_account_cache', {}) or {})
            if isinstance(cash_bal, dict):
                bal.update(cash_bal)
            self._williams_mock_account_cache = bal
            self._williams_mock_account_cache_mono = _time.monotonic()
            return
'''

old_cash="""                _cash=max(0.0,_n(_bal.get('entr') or _bal.get('dnca_tot_amt') or _bal.get('deposit') or _bal.get('cash')))\n"""
new_cash="""                _cash=max(0.0,_n(_bal.get('ord_alow_amt') or _bal.get('100stk_ord_alow_amt') or _bal.get('entr') or _bal.get('dnca_tot_amt') or _bal.get('deposit') or _bal.get('cash')))\n"""

changed=0
if new_refresh not in s:
    if old_refresh not in s:
        raise SystemExit('CURRENT REFRESH BLOCK NOT FOUND - NOTHING CHANGED')
    s=s.replace(old_refresh,new_refresh,1)
    changed+=1

if new_cash not in s:
    if old_cash not in s:
        raise SystemExit('CURRENT CASH PARSE LINE NOT FOUND - NOTHING CHANGED')
    s=s.replace(old_cash,new_cash,1)
    changed+=1

p.write_text(s)
print(f'V23 KR BUYING POWER CONNECTED ({changed} patches)')
