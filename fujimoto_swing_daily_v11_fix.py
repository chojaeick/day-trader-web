from pathlib import Path

KOREA=Path('live_server/korea.py')


def main():
    s=KOREA.read_text()
    old="""        if rsi50_down is not None and rsi50_down<=2: sub(20,'RSI_50_BREAKDOWN')
        if dead is not None and dead<=3: sub(25,'MACD_DEAD_CROSS_RECENT')
        if hist_falling3: sub(10,'HISTOGRAM_FALLING_3D')
        score=max(0,min(100,int(round(score))))

        if score>=80: state='STRONG_ENTRY'
        elif score>=65: state='ENTRY_READY'
        elif score>=50: state='PREPARE'
        else: state='WATCH'
        exit_signal=bool((rsi50_down is not None and rsi50_down<=2) or (dead is not None and dead<=3))
        if exit_signal: state='EXIT_REVIEW'
"""
    new="""        # V1.1: resolve conflicting recent crosses by most-recent signal, not stale bearish memory.
        rsi_bear_latest=bool(rsi50_down is not None and rsi50_down<=2 and (rsi50_up is None or rsi50_down < rsi50_up))
        macd_bear_latest=bool(dead is not None and dead<=3 and (golden is None or dead < golden))
        if rsi_bear_latest: sub(20,'RSI_50_BREAKDOWN_LATEST')
        if macd_bear_latest: sub(25,'MACD_DEAD_CROSS_LATEST')
        if hist_falling3: sub(10,'HISTOGRAM_FALLING_3D')
        score=max(0,min(100,int(round(score))))

        if score>=80: state='STRONG_ENTRY'
        elif score>=65: state='ENTRY_READY'
        elif score>=50: state='PREPARE'
        else: state='WATCH'

        current_bearish=bool(rr < 50 or mm < ss or hist_falling3)
        exit_signal=bool(current_bearish and (rsi_bear_latest or macd_bear_latest))
        if exit_signal: state='EXIT_REVIEW'
"""
    if old not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: Fujimoto swing exit block')
    s=s.replace(old,new,1)
    s=s.replace("'version':'FUJIMOTO_SWING_DAILY_V1'","'version':'FUJIMOTO_SWING_DAILY_V1_1'",1)
    s=s.replace("'note':'Daily RSI(14)+MACD(12,26,9) swing model. Intended for roughly 2-10 trading-day holds, not intraday entry timing.'","'note':'Daily RSI(14)+MACD(12,26,9) swing model v1.1. Most-recent cross wins; EXIT requires current bearish structure plus latest bearish cross.'",1)
    KOREA.write_text(s)
    print('FUJIMOTO_SWING_DAILY_V11_FIX_OK')

if __name__=='__main__': main()
