#!/usr/bin/env python3
from pathlib import Path
import re

p=Path('live_server/v4_engine.py')
s=p.read_text()

if '_williams_usa_paper_fields' in s:
    print('ALREADY_PATCHED live_server/v4_engine.py')
    print('ADDED=_williams_usa_paper_fields')
    print('ENTRY=frozen Williams V23 trigger, paper-only')
    print('EXIT=frozen STRUCT0 support break, paper-only')
    print('REAL_BROKER_ORDERS=NO')
    raise SystemExit(0)

# Enrich already-built USA tracker rows immediately before sorting/finalize.
pat = re.compile(
    r"(?P<indent>        )rows\.sort\(key=_tracker_sort_key\)\s*\n"
    r"(?P=indent)self\._finalize\('USA',rows\)\s*\n"
    r"(?P=indent)return self\.tracker\['USA'\]\s*\n\s*"
    r"(?P<defline>    def _usa_row\([^\n]+\):)"
)

m=pat.search(s)
if not m:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: USA finalize/_usa_row anchor changed')

helper = '''        # V97B: enrich production USA tracker rows with PAPER-ONLY frozen Williams fields.
        # Does not change production V4 state, production positions, or broker order behavior.
        for _wr in rows:
            try:
                _wsym=str(_wr.get('symbol') or '')
                _wr.update(self._williams_usa_paper_fields(_wsym,db,_wr,fmap.get(_wsym)))
            except Exception as e:
                _wr.update({
                    'williams_entry':False,'williams_signal_entry':False,
                    'williams_struct_state':None,'williams_support':None,
                    'williams_support_updates':None,'williams_exit_ready':False,
                    'williams_error':f'{type(e).__name__}: {e}'[:240],
                    'williams_orders_enabled':False,
                })
        rows.sort(key=_tracker_sort_key)
        self._finalize('USA',rows)
        return self.tracker['USA']

    def _williams_usa_paper_fields(self,sym,db,row,finder=None):
        """USA PAPER adapter for frozen Williams V23 entry + frozen STRUCT0 exit.

        Entry: day_open + 0.5*(prior regular-day high-low), RSI2>50,
        Finder rank<=20, using the existing williams_live_evaluate_v23.
        Exit: causal confirmed higher-low STRUCT0 close-break.
        This method never calls a real broker.
        """
        out={
            'williams_entry':False,'williams_signal_entry':False,
            'williams_struct_state':None,'williams_support':None,
            'williams_support_updates':None,'williams_exit_ready':False,
            'williams_trigger':None,'williams_rsi2':None,
            'williams_stage':'WAIT_DATA','williams_orders_enabled':False,
            'williams_error':None,
        }
        if str(row.get('session') or '').upper()!='REGULAR':
            out['williams_stage']='SESSION_NOT_REGULAR'; return out
        if not (row.get('data_integrity') or {}).get('valid'):
            out['williams_stage']='DATA_INVALID'; return out

        ticks=db.ticks(sym,40000)
        if ticks:
            _last_et_date=pd.to_datetime(ticks[-1]['ts'],utc=True).tz_convert('America/New_York').date()
            ticks=[t for t in ticks if pd.to_datetime(t['ts'],utc=True).tz_convert('America/New_York').date()==_last_et_date]
        b1=ticks_to_bars(ticks,1)
        if b1 is None or len(b1)<16:
            out['williams_stage']='INSUFFICIENT_1M'; return out
        closes=[_f(x) for x in pd.to_numeric(b1['close'],errors='coerce').dropna().tolist()]
        if len(closes)<16:
            out['williams_stage']='INSUFFICIENT_CLOSES'; return out

        # Existing PAPER position: only frozen STRUCT0 governs HOLD/EXIT.
        ppos=next((x for x in self.paper.account('USA').get('positions',[])
                   if str(x.get('symbol') or '').upper()==str(sym).upper()),None)
        if ppos:
            st=self._williams_structure_state(
                b1,
                entry_price=_f(ppos.get('entry_fill_price') or ppos.get('entry_price'))
            )
            out['williams_struct_state']=st.get('state')
            out['williams_support']=st.get('support')
            out['williams_support_updates']=st.get('support_updates')
            out['williams_exit_ready']=bool(st.get('break'))
            out['williams_stage']='POSITION_'+str(st.get('state') or 'HOLD')
            return out

        # Prior regular-session OHLC comes from historical_minute_bars.
        et=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
        today=et.strftime('%Y%m%d')
        with self.store._c() as c:
            prev=c.execute(
                """SELECT trade_date,MAX(high) ph,MIN(low) pl
                   FROM historical_minute_bars
                   WHERE symbol=? AND interval_min=1 AND trade_date<? AND session='REGULAR'
                   GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1""",
                (str(sym).upper(),today)
            ).fetchone()
        if not prev or _f(prev['ph'])<=0 or _f(prev['pl'])<=0:
            out['williams_stage']='NO_PREV_DAY'; return out

        day_open=_f(b1.iloc[0]['open'])
        prev_price=_f(b1.iloc[-2]['close'])
        cur_price=_f(b1.iloc[-1]['close'])
        if day_open<=0 or prev_price<=0 or cur_price<=0:
            out['williams_stage']='BAD_PRICE_INPUT'; return out

        rank=(finder or {}).get('rank') or row.get('finder_rank')
        ev=williams_live_evaluate_v23(
            sym,_f(prev['ph']),_f(prev['pl']),day_open,
            prev_price,cur_price,closes[-120:],finder_rank=rank
        )
        trig=day_open+0.5*(_f(prev['ph'])-_f(prev['pl']))
        out['williams_trigger']=round(trig,6)
        out['williams_rsi2']=(ev or {}).get('rsi2')
        out['williams_stage']=(ev or {}).get('stage') or 'EVALUATED'
        sig=bool((ev or {}).get('signal') or (ev or {}).get('entry') or
                 (ev or {}).get('signal_entry') or (ev or {}).get('williams_entry'))
        out['williams_entry']=sig
        out['williams_signal_entry']=sig
        return out

'''

replacement = helper + m.group('defline')
s = s[:m.start()] + replacement + s[m.end():]
p.write_text(s)

print('PATCHED live_server/v4_engine.py')
print('ADDED=_williams_usa_paper_fields')
print('ENTRY=frozen Williams V23 trigger, paper-only')
print('EXIT=frozen STRUCT0 support break, paper-only')
print('REAL_BROKER_ORDERS=NO')
