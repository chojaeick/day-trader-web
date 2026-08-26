#!/usr/bin/env python3
"""V137 Williams live replication audit.

READ ONLY. NO ORDERS. NO DOWNLOADS. NO PATCHES.

Purpose
-------
Compare the frozen V136 winner specification against the current live engine
implementation and report exact mismatches before any USA paper-trading deploy.

Frozen strategy (from V135/V136)
-------------------------------
ENTRY
- first valid Williams cross only
- trigger = day_open + 0.5 * previous-day range
- RSI2 > 50
- 09:30 <= ET <= 11:00
- current volume >= 1.5 * average prior 10 bars
- CCI20 > 100
- MACD histogram rising vs prior bar
EXIT
- hard stop = -1.0%
- MACD < signal AND CCI20 falling for 2 consecutive bars
- no forced minimum hold

This script intentionally does not assume any particular function names. It scans
live_server/v4_engine.py and live_server/api.py for evidence of each frozen rule,
plus known contaminating rules introduced during Korea mock work.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server'/'v4_engine.py'
API=ROOT/'live_server'/'api.py'

FROZEN={
    'entry_first_cross_only': 'first valid Williams cross only',
    'entry_trigger_prev_range_half': 'day_open + 0.5 * previous-day range',
    'entry_rsi2_gt_50': 'RSI2 > 50',
    'entry_window_0930_1100': '09:30-11:00 ET',
    'entry_volume_ratio_1_5': 'volume >= 1.5x prior-10 average',
    'entry_cci20_gt_100': 'CCI20 > 100',
    'entry_macd_hist_rising': 'MACD histogram rising',
    'exit_hard_stop_1_0': '-1.0% hard stop',
    'exit_combo_two_bar': 'MACD<signal + CCI falling for 2 bars',
    'exit_no_forced_hold': 'NO forced minimum hold',
}

def read(p):
    try:return p.read_text(errors='ignore')
    except Exception:return ''

def grep_lines(txt, patterns):
    out=[]
    lines=txt.splitlines()
    for i,line in enumerate(lines,1):
        low=line.lower()
        if any(re.search(p,low) for p in patterns):
            out.append((i,line.rstrip()))
    return out

def has_any(txt, pats):
    low=txt.lower()
    return any(re.search(p,low,re.S) for p in pats)

def main():
    eng=read(ENGINE); api=read(API); alltxt=eng+'\n'+api
    print('=== V137 WILLIAMS LIVE REPLICATION AUDIT ===')
    print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE PATCHES=NONE')
    print('ENGINE=',ENGINE,'EXISTS=',ENGINE.exists(),'BYTES=',len(eng))
    print('API=',API,'EXISTS=',API.exists(),'BYTES=',len(api))
    print('\n=== FROZEN SPEC ===')
    for k,v in FROZEN.items(): print(k,'=',v)

    checks=[]
    # Evidence checks are deliberately broad; PASS means code evidence exists, not semantic equivalence.
    checks.append(('entry_first_cross_only', has_any(eng,[r'first[_ ]?(seen|cross|signal)',r'first.*williams',r'struct5_order_sent'])))
    checks.append(('entry_trigger_prev_range_half', has_any(eng,[r'0\.5\s*\*.*(?:ph|prev.*range|high.*low)',r'(?:day_open|open).*\+.*0\.5'])))
    checks.append(('entry_rsi2_gt_50', has_any(eng,[r'rsi.?2.*(?:>|>=)\s*50',r'(?:r2|rsi2).*50'])))
    checks.append(('entry_window_0930_1100', has_any(eng,[r'930.*1100',r'09:30.*11:00',r'9\s*\*\s*60.*11\s*\*\s*60'])))
    checks.append(('entry_volume_ratio_1_5', has_any(eng,[r'(?:vol|volume).*1\.5',r'1\.5.*(?:vol|volume)'])))
    checks.append(('entry_cci20_gt_100', has_any(eng,[r'cci.*(?:>|>=)\s*100',r'cci.*100'])))
    checks.append(('entry_macd_hist_rising', has_any(eng,[r'hist.*>.*hist',r'macd.*signal.*(?:prev|\-1)',r'macd.*hist'])))
    checks.append(('exit_hard_stop_1_0', has_any(eng,[r'0\.99\b',r'1\.0\s*%.*(?:stop|hard)',r'(?:hard|stop).*1\.0'])))
    checks.append(('exit_combo_two_bar', has_any(eng,[r'weak_run.*>=\s*2',r'(?:combo|weak).*2.?bar',r'cci.*fall.*2'])))
    # This is reverse logic: pass only if no forced 300s hold evidence exists in Williams/USA neighborhood.
    forced_hold=has_any(eng,[r'hold_sec\s*<\s*300',r'forced_min_hold_300',r'min(?:imum)?[_ ]?hold.*300'])
    checks.append(('exit_no_forced_hold', not forced_hold))

    print('\n=== SPEC EVIDENCE CHECK ===')
    passed=0
    for k,ok in checks:
        print(k, 'PASS' if ok else 'MISSING_OR_MISMATCH')
        passed+=int(ok)

    print('\n=== KNOWN CONTAMINATION SCAN ===')
    contam_patterns={
      'FORCED_5MIN_HOLD':[r'hold_sec\s*<\s*300',r'forced_min_hold_300',r'min(?:imum)?[_ ]?hold.*300'],
      'KOREA_MOCK_HARD_STOP_1_5':[r'0\.985',r'1\.5\s*%.*hard',r'hard_stop.*1\.5'],
      'WILLIAMS_MOCK_ORDER_PATH':[r'williams_mock_buy',r'williams_mock_sell',r'kiwoommockbroker'],
      'POST_ENTRY_STRUCTURE_EXIT':[r'williams_exit_ready',r'post.entry.*support',r'structure_start_time'],
      'V123_WATCHDOG':[r'v123',r'hard_stop_watchdog'],
    }
    contamination=[]
    for name,pats in contam_patterns.items():
        hits=grep_lines(alltxt,pats)
        print(name,'HITS=',len(hits))
        for ln,s in hits[:8]: print(' ',ln,s[:220])
        if hits: contamination.append(name)

    print('\n=== USA WILLIAMS RELATED LINES ===')
    lines=grep_lines(eng,[r'williams',r'cci',r'macd',r'rsi',r'hard_stop',r'weak_run'])
    print('TOTAL_RELATED_LINES=',len(lines))
    for ln,s in lines[:120]: print(f'{ln}: {s[:240]}')

    print('\n=== VERDICT ===')
    print('SPEC_PASS_COUNT=',passed,'/',len(checks))
    print('CONTAMINATION_FLAGS=',','.join(contamination) if contamination else 'NONE')
    ready=(passed==len(checks) and not forced_hold)
    print('LIVE_REPLICATION_READY=',ready)
    if ready:
        print('NEXT=BUILD_FROZEN_USA_PAPER_PATH_AND_REPLAY_EQUIVALENCE_TEST')
    else:
        print('NEXT=DO_NOT_DEPLOY; PATCH_ONLY_EXACT_MISMATCHES_TO_FROZEN_SPEC, THEN REAUDIT')

if __name__=='__main__': main()
