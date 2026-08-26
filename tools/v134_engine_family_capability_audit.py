#!/usr/bin/env python3
from pathlib import Path
import json,re
ROOT=Path('/home/ubuntu/day-trader-api-repo')
PATTERNS={
 'WILLIAMS':['williams','daytrade_entry_sim_v'],
 'ETHAN':['ethan_ny_breakout'],
 'FUJIMOTO':['fujimoto'],
 'MA20':['ma20_scalp'],
 'REBOUND':['rebound'],
 'TREND':['trend_v','trend_'],
 'V4_ENGINE':['live_server/v4_engine.py']
}
TOKENS=['historical_minute_bars','sqlite3','backtest','replay','metrics','profit','win','entry','exit','causal','oos']

def inspect(p):
    try:s=p.read_text(errors='ignore')
    except:return None
    low=s.lower()
    return {
      'path':str(p.relative_to(ROOT)),
      'lines':s.count('\n')+1,
      'hist_db':'historical_minute_bars' in s,
      'sqlite':'sqlite3' in low,
      'main':'if __name__' in s,
      'argparse':'argparse' in low,
      'oos':'oos' in low,
      'causal':'causal' in low,
      'metrics':bool(re.search(r'\b(metrics|win|profit_factor|pf|drawdown|mdd)\b',low)),
      'entry_exit':('entry' in low and 'exit' in low),
    }

def main():
    files=[p for p in ROOT.rglob('*.py') if 'venv' not in p.parts and '.git' not in p.parts]
    fam={k:[] for k in PATTERNS}
    for p in files:
        rel=str(p.relative_to(ROOT)).lower()
        for k,ps in PATTERNS.items():
            if any(x.lower() in rel for x in ps):
                z=inspect(p)
                if z:fam[k].append(z)
    print('=== V134 ENGINE FAMILY CAPABILITY AUDIT ===')
    print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE')
    report={}
    for k,rows in fam.items():
        rows=sorted(rows,key=lambda x:x['path'])
        runnable=[r for r in rows if r['main'] and (r['hist_db'] or r['sqlite'])]
        validated=[r for r in runnable if r['entry_exit'] and r['metrics']]
        print('\n--',k,'-- FILES',len(rows),'RUNNABLE_DB',len(runnable),'CANDIDATE_BACKTEST',len(validated))
        for r in validated[:30]:
            print(r['path'],'OOS=',r['oos'],'CAUSAL=',r['causal'],'LINES=',r['lines'])
        report[k]={'files':rows,'runnable_db':len(runnable),'candidate_backtest':len(validated)}
    out='/tmp/v134_engine_family_capability_audit.json'
    Path(out).write_text(json.dumps(report,indent=2))
    print('\nREPORT',out)
    print('NEXT=SELECT_EXECUTABLE_CROSS_FAMILY_CANDIDATES; DO_NOT_TUNE WILLIAMS FURTHER')
if __name__=='__main__':main()
