#!/usr/bin/env python3
import sqlite3, json, urllib.request
from collections import Counter

DB='/home/ubuntu/day-trader-api/daytrader.db'
SINCE='2026-08-27T03:00:00+00:00'
print('=== V236 KOREA NO-TRADE SIGNAL PATH AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE')
print('SINCE=',SINCE)

try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=5) as r:
        print('RUNTIME_HTTP=',r.status,'BODY=',json.loads(r.read().decode()))
except Exception as e:
    print('RUNTIME_FAIL=',type(e).__name__,str(e)[:160])

con=sqlite3.connect(DB)
con.row_factory=sqlite3.Row
rows=con.execute('''
SELECT id,ts,symbol,event_type,power,message,payload_json
FROM v4_signal_events
WHERE market='KOREA' AND ts>=?
ORDER BY id
''',(SINCE,)).fetchall()

cnt=Counter(str(r['event_type']) for r in rows)
print('\n=== EVENT COUNTS ===')
for k,v in cnt.most_common(): print(k,v)

wanted=['WILLIAMS_MTF_PASS','WILLIAMS_MTF_BLOCK','WILLIAMS_MOCK_BUY','WILLIAMS_MOCK_SELL','WILLIAMS_MOCK_ERROR']
print('\n=== WILLIAMS / MTF EVENTS ===')
found=0
for r in rows:
    if r['event_type'] not in wanted: continue
    found+=1
    d=dict(r); p={}
    try: p=json.loads(d.get('payload_json') or '{}')
    except Exception: pass
    row=p.get('row') if isinstance(p,dict) else None
    if not isinstance(row,dict): row={}
    g=row.get('v234_mtf_guard') if isinstance(row.get('v234_mtf_guard'),dict) else {}
    print({
      'id':d['id'],'ts':d['ts'],'symbol':d['symbol'],'event_type':d['event_type'],'message':d['message'],
      'price':row.get('price'),'entry':row.get('williams_entry'),'stage':row.get('williams_entry_stage'),
      'raw_cross':row.get('williams_entry_raw_cross'),'cross_recovered':row.get('williams_cross_recovered'),
      'struct5':row.get('williams_struct5_signal'),'struct5_reason':row.get('williams_struct5_reason'),
      'resistance':row.get('williams_struct5_resistance'),'finder_rank':row.get('finder_rank'),
      'mtf_ok':g.get('ok'),'one_min_ok':g.get('one_min_ok'),'five_min_ok':g.get('five_min_ok'),
      'one_improve_count':g.get('one_improve_count'),'rsi1':g.get('rsi1'),'cci1':g.get('cci1'),
      'hist1':g.get('hist1'),'rsi5':g.get('rsi5'),'hist5':g.get('hist5'),'hist5_prev':g.get('hist5_prev'),
      'close5':g.get('close5'),'ema20_5':g.get('ema20_5')
    })
print('WILLIAMS_MTF_EVENT_COUNT=',found)

print('\n=== RECENT KOREA PAPER TRADES ===')
try:
    rr=con.execute('''SELECT id,ts,symbol,side,qty,signal_price,fill_price,reason,realized_pnl FROM paper_trades WHERE market='KOREA' AND ts>=? ORDER BY id''',(SINCE,)).fetchall()
    for r in rr: print(dict(r))
    print('PAPER_TRADE_COUNT=',len(rr))
except Exception as e:
    print('PAPER_TRADE_QUERY_FAIL=',type(e).__name__,str(e))
con.close()

print('\n=== DIAGNOSIS ===')
if found==0:
    print('NO_FRESH_WILLIAMS_SIGNAL_REACHED_MTF_TELEMETRY')
    print('NEXT=INSPECT_WILLIAMS_SIGNAL_GENERATION_RATE; DO_NOT_RELAX_MTF_YET')
else:
    print('MTF_EVENTS_PRESENT=YES')
    print('NEXT=COMPARE_PASS_VS_BLOCK_AND_RELAX_ONLY_THE_DOMINANT_FALSE_NEGATIVE_GUARD_IF_NEEDED')
