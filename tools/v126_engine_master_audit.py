#!/usr/bin/env python3
"""V126 MASTER ENGINE / DATASET AUDIT — READ ONLY.

Purpose
-------
Before any new US paper trading, inventory and verify everything already built:
- downloaded historical_minute_bars coverage (reuse first; no downloading)
- candidate replay/backtest/strategy scripts already on the server/repo
- saved result artifacts that may contain win-rate / PF / expectancy metrics
- current live Williams engine mutations and safety patches
- today's Williams mock order history summary

This script NEVER imports broker classes, NEVER calls APIs, NEVER places orders,
and NEVER modifies the database or systemd service. It writes only a JSON report
under /tmp for follow-up analysis.
"""
from __future__ import annotations
import os, re, json, sqlite3, hashlib
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone

RUNTIME=Path('/home/ubuntu/day-trader-api')
REPO=Path('/home/ubuntu/day-trader-api-repo')
DB=RUNTIME/'daytrader.db'
OUT=Path('/tmp/v126_engine_master_audit.json')

STRATEGY_WORDS=('williams','trend','replay','rebound','scalp','fujimoto','breakout','entry','exit','causal','oos','backtest','simulate','simulation')
RESULT_EXT={'.json','.csv','.txt','.log','.md'}
METRIC_PATTERNS={
    'win_rate': re.compile(r'(?i)(?:win[_ -]?rate|승률)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*%?'),
    'profit_factor': re.compile(r'(?i)(?:profit[_ -]?factor|\bPF\b)\s*[:=]?\s*(-?\d+(?:\.\d+)?)'),
    'expectancy': re.compile(r'(?i)(?:expectancy|기대값)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*%?'),
    'trades': re.compile(r'(?i)(?:trades?|거래수)\s*[:=]?\s*(\d+)'),
    'max_drawdown': re.compile(r'(?i)(?:max[_ -]?drawdown|MDD|최대낙폭)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*%?'),
}

def q(c,sql,args=()):
    return c.execute(sql,args).fetchall()

def table_exists(c,name):
    return c.execute("select 1 from sqlite_master where type='table' and name=?",(name,)).fetchone() is not None

def sha256(path):
    h=hashlib.sha256()
    try:
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

def discover_scripts(root:Path):
    out=[]
    if not root.exists(): return out
    for p in root.rglob('*.py'):
        try:
            rel=str(p.relative_to(root))
        except Exception:
            rel=str(p)
        low=rel.lower()
        if any(w in low for w in STRATEGY_WORDS):
            try: st=p.stat()
            except Exception: continue
            out.append({'root':str(root),'path':rel,'size':st.st_size,'mtime':st.st_mtime,'sha256':sha256(p)})
    return sorted(out,key=lambda x:x['path'])

def discover_results(root:Path):
    out=[]
    if not root.exists(): return out
    scanned=0
    for p in root.rglob('*'):
        if scanned>5000: break
        if not p.is_file() or p.suffix.lower() not in RESULT_EXT: continue
        low=str(p).lower()
        if not any(w in low for w in STRATEGY_WORDS): continue
        scanned+=1
        try:
            if p.stat().st_size>2_000_000: continue
            txt=p.read_text(errors='ignore')[-200000:]
        except Exception:
            continue
        metrics={}
        for k,pat in METRIC_PATTERNS.items():
            ms=pat.findall(txt)
            if ms: metrics[k]=ms[-1]
        if metrics:
            out.append({'path':str(p),'metrics':metrics,'mtime':p.stat().st_mtime})
    return sorted(out,key=lambda x:x['mtime'],reverse=True)[:200]

def current_engine_audit():
    p=RUNTIME/'live_server'/'v4_engine.py'
    if not p.exists(): return {'missing':True}
    s=p.read_text(errors='ignore')
    checks={
        'forced_min_hold_300': bool(re.search(r'hold_sec\s*<\s*300(?:\.0)?',s)),
        'hard_stop_1_5pct_in_engine': 'entry_price*0.985' in s.replace(' ',''),
        'v125_time_preserved': "'bars_raw':b[[c for c in ('time','open','high','low','close')" in s.replace(' ',''),
        'williams_mock_present': 'WILLIAMS_MOCK' in s,
        'post_entry_start_time_present': 'entered_bar_time' in s and 'start_time=_wmock_start' in s,
    }
    markers=sorted(set(re.findall(r'V(?:1(?:1[5-9]|2[0-5])|\d+(?:\.\d+)+)',s)))
    return {'path':str(p),'sha256':sha256(p),'checks':checks,'version_markers':markers}

def api_audit():
    p=RUNTIME/'live_server'/'api.py'
    if not p.exists(): return {'missing':True}
    s=p.read_text(errors='ignore')
    return {
        'path':str(p),'sha256':sha256(p),
        'v122_korea_dedicated_loop':'korea_safety_forever' in s,
        'v123_watchdog':'williams_mock_hard_stop_forever' in s,
        'runtime_default_normal': bool(re.search(r"runtime_mode\s*=\s*\{[^}]*['\"]mode['\"]\s*:\s*['\"]NORMAL",s,re.S)),
    }

def db_audit():
    rep={'exists':DB.exists(),'path':str(DB)}
    if not DB.exists(): return rep
    c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=30)
    c.row_factory=sqlite3.Row
    try:
        rep['tables']=[r['name'] for r in q(c,"select name from sqlite_master where type='table' order by name")]
        if table_exists(c,'historical_minute_bars'):
            cols=[r['name'] for r in q(c,'pragma table_info(historical_minute_bars)')]
            rep['historical_columns']=cols
            total=q(c,'select count(*) n from historical_minute_bars')[0]['n']
            rep['historical_total_rows']=total
            rep['historical_symbols']=q(c,'select count(distinct symbol) n from historical_minute_bars')[0]['n']
            rep['historical_dates']=q(c,'select count(distinct trade_date) n from historical_minute_bars')[0]['n']
            coverage=[dict(r) for r in q(c,"""
                select symbol,trade_date,
                       count(*) bars,
                       sum(case when session='PRE' then 1 else 0 end) pre,
                       sum(case when session='REGULAR' then 1 else 0 end) regular,
                       sum(case when session='POST' then 1 else 0 end) post,
                       min(et_time) first_et,max(et_time) last_et
                from historical_minute_bars
                group by symbol,trade_date
                order by trade_date,symbol
            """)]
            rep['coverage']=coverage
            rep['full_regular_390_pairs']=sum(1 for r in coverage if int(r.get('regular') or 0)>=390)
            rep['pairs_total']=len(coverage)
            rep['symbols_by_days']=dict(Counter(r['symbol'] for r in coverage))
            rep['dates_by_symbols']=dict(Counter(r['trade_date'] for r in coverage))
            # duplicate key check using known schema columns when present
            if all(x in cols for x in ('symbol','trade_date','et_time','interval_min')):
                rep['duplicate_groups']=q(c,"""select count(*) n from (
                    select symbol,trade_date,interval_min,et_time,count(*) c
                    from historical_minute_bars
                    group by symbol,trade_date,interval_min,et_time having c>1)""")[0]['n']
        for t in ('v4_trade_log','v4_tracker_snapshots','v4_signal_events','v4_validation_marks'):
            if table_exists(c,t): rep[t+'_rows']=q(c,f'select count(*) n from {t}')[0]['n']
        if table_exists(c,'v4_signal_events'):
            rep['williams_events_today']=[dict(r) for r in q(c,"""
                select event_type,count(*) n from v4_signal_events
                where market='KOREA' and ts like '2026-08-26%'
                  and event_type like 'WILLIAMS%'
                group by event_type order by event_type
            """)]
    finally:
        c.close()
    return rep

def main():
    report={
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'mode':'READ_ONLY_MASTER_AUDIT',
        'db':db_audit(),
        'runtime_engine':current_engine_audit(),
        'runtime_api':api_audit(),
        'strategy_scripts_runtime':discover_scripts(RUNTIME),
        'strategy_scripts_repo':discover_scripts(REPO),
        'saved_metric_artifacts':discover_results(RUNTIME)+discover_results(REPO),
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2))

    d=report['db']
    print('=== V126 MASTER ENGINE / DATASET AUDIT ===')
    print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE')
    print('DB',d.get('path'),'ROWS',d.get('historical_total_rows'),'SYMBOLS',d.get('historical_symbols'),'DATES',d.get('historical_dates'))
    print('SYMBOL_DATE_PAIRS',d.get('pairs_total'),'FULL_REGULAR_390',d.get('full_regular_390_pairs'),'DUP_GROUPS',d.get('duplicate_groups'))
    print('\n=== DATA COVERAGE: SYMBOL -> DAYS ===')
    for s,n in sorted((d.get('symbols_by_days') or {}).items(), key=lambda x:(-x[1],x[0])):
        print(s,n)
    print('\n=== CURRENT LIVE ENGINE CHECKS ===')
    for k,v in (report['runtime_engine'].get('checks') or {}).items(): print(k,'=',v)
    print('ENGINE_MARKERS',','.join(report['runtime_engine'].get('version_markers') or []))
    print('API_V122_DEDICATED=',report['runtime_api'].get('v122_korea_dedicated_loop'))
    print('API_V123_WATCHDOG=',report['runtime_api'].get('v123_watchdog'))
    print('\n=== STRATEGY / REPLAY SCRIPTS FOUND ===')
    seen=set()
    for x in report['strategy_scripts_runtime']+report['strategy_scripts_repo']:
        key=x['path']
        if key in seen: continue
        seen.add(key); print(key)
    print('\n=== SAVED METRIC ARTIFACTS ===')
    if not report['saved_metric_artifacts']:
        print('NONE_FOUND_WITH_PARSEABLE_METRICS')
    else:
        for x in report['saved_metric_artifacts'][:80]: print(x['path'],x['metrics'])
    print('\nREPORT',OUT)
    print('NEXT=SELECT_EXISTING_CANDIDATES_AND_RUN_SAME_MASTER_DATASET')

if __name__=='__main__':
    main()
