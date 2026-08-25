from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()

if 'from .paper_trading import PaperBroker' not in s:
    s=s.replace('from .analytics import ticks_to_bars, multi_timeframe_signal\n','from .analytics import ticks_to_bars, multi_timeframe_signal\nfrom .paper_trading import PaperBroker\n')

needle="self.store=V4Store(db_path); self.finder={m:{'rows':[],'updated_at':None} for m in ('USA','KOREA')}; self.tracker={m:{'rows':[],'updated_at':None} for m in ('USA','KOREA')}; self._last={}; self._snap={}; self._rank={}; self._kr_gate_cache={}; self._lock=threading.RLock()"
repl=needle+"; self.paper=PaperBroker(db_path)"
if needle in s and 'self.paper=PaperBroker(db_path)' not in s:
    s=s.replace(needle,repl)

marker='    def _finalize(self,market,rows):\n'
method='''    def _paper_williams_step(self, market, row):\n        """Paper-only Williams execution bridge. Never calls a real broker."""\n        market=market.upper(); sym=str(row.get('symbol') or '')\n        if not sym:return None\n        price=_f(row.get('price'))\n        if price<=0:return None\n        try:\n            pos=next((p for p in self.paper.account(market).get('positions',[]) if str(p.get('symbol'))==sym),None)\n            # Existing paper position: mark structure and close only on frozen STRUCT0 break.\n            if pos:\n                support=row.get('williams_support')\n                st=row.get('williams_struct_state') or 'HOLD'\n                self.paper.mark(market,sym,price,support=support,support_updates=row.get('williams_support_updates'),state=st)\n                if bool(row.get('williams_exit_ready')):\n                    return self.paper.exit(market,sym,price,reason='SUPPORT_BREAK_EXIT',support=support)\n                return {'ok':True,'action':'HOLD','market':market,'symbol':sym,'price':price,'support':support}\n            # New paper entry only when Williams exact evaluator has produced a fresh ENTRY signal on the row.\n            wentry=bool(row.get('williams_entry') or row.get('williams_signal_entry'))\n            if wentry and row.get('session')=='REGULAR':\n                return self.paper.enter(market,sym,price,strategy_id='WILLIAMS_STRUCT0',reason='WILLIAMS_ENTRY',support=row.get('williams_support'))\n        except Exception as e:\n            return {'ok':False,'action':'ERROR','reason':f'{type(e).__name__}: {e}'}\n        return None\n\n'''
if '_paper_williams_step' not in s:
    s=s.replace(marker,method+marker)

old="            if market=='USA' and r.get('session')=='REGULAR' and (r.get('data_integrity') or {}).get('valid'):\n                self.store.update_validation_outcomes(market,sym,_f(r.get('price')))\n            elif market=='KOREA' and r.get('session')=='REGULAR':\n                self.store.update_validation_outcomes(market,sym,_f(r.get('price')))\n"
new=old+"            paper_result=self._paper_williams_step(market,r)\n            if paper_result is not None:r['paper_williams']=paper_result\n"
if old in s and "paper_result=self._paper_williams_step" not in s:
    s=s.replace(old,new)

old_status="market=market.upper(); return {'market':market,'session':_session(market),'finder':self.finder.get(market),'tracker':self.tracker.get(market),'positions':self.store.positions(market),'events':self.store.events(market,20),'version':'V4_CLEAN_ENGINE_ALPHA'}"
new_status="market=market.upper(); return {'market':market,'session':_session(market),'finder':self.finder.get(market),'tracker':self.tracker.get(market),'positions':self.store.positions(market),'paper_account':self.paper.account(market),'paper_trades':self.paper.trades(market,20),'events':self.store.events(market,20),'version':'V4_CLEAN_ENGINE_ALPHA'}"
if old_status in s and "'paper_account':self.paper.account(market)" not in s:
    s=s.replace(old_status,new_status)

P.write_text(s)
print('PATCHED',P)
print('ADDED=PaperBroker + _paper_williams_step + status paper_account/paper_trades')
print('ENTRY_SOURCE=williams_entry or williams_signal_entry only')
print('EXIT_SOURCE=williams_exit_ready frozen STRUCT0 only')
print('REAL_BROKER_ORDERS=NO')
