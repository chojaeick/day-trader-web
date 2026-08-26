#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

# V111: paper-validation only. Keep the existing Williams/V110 signal intact,
# but add a fast 1-minute structural breakout trigger using only the latest 5 bars.
# This is intended to catch fresh intraday leaders that enter the candidate pool
# after the day-level Williams trigger has already passed.

old=r'''            out['historical_cross_recovered']=bool(recovered_cross_time)
            out['recovered_cross_time']=recovered_cross_time
            out['recovered_cross_age_min']=recovered_cross_age_min
            out['recovered_cross_rsi2']=recovered_cross_rsi2
'''

new=r'''            out['historical_cross_recovered']=bool(recovered_cross_time)
            out['recovered_cross_time']=recovered_cross_time
            out['recovered_cross_age_min']=recovered_cross_age_min
            out['recovered_cross_rsi2']=recovered_cross_rsi2

            # V111 STRUCT5 paper-entry trigger.
            # Window = current 1m bar + previous 4 completed 1m bars.
            # Resistance is the highest HIGH of the previous four bars.
            # Entry fires only on a fresh close breakout, RSI2>50, and no lower-low
            # deterioration across the most recent two bars. No future bars are used.
            struct5_signal=False
            struct5_resistance=None
            struct5_higher_low=False
            struct5_rsi2=None
            struct5_reason='NEED_5_BARS'
            if len(cur)>=5:
                w5=cur.tail(5).reset_index(drop=True)
                prev4=w5.iloc[:4]
                nowbar=w5.iloc[4]
                prevbar=w5.iloc[3]
                struct5_resistance=float(prev4['high'].max())
                struct5_rsi2=_williams_rsi2([float(v) for v in cur['close'].tail(30).tolist()])
                # Latest two-bar low structure must not undercut the preceding two-bar low structure.
                old_low=float(w5.iloc[:2]['low'].min())
                new_low=float(w5.iloc[2:4]['low'].min())
                struct5_higher_low=bool(new_low >= old_low)
                fresh_break=bool(float(prevbar['close']) <= struct5_resistance < float(nowbar['close']))
                rank_ok=bool(finder_rank is not None and int(finder_rank) <= 20)
                rsi_ok=bool(struct5_rsi2 is not None and float(struct5_rsi2) > 50.0)
                day_key=now_kst.strftime('%Y%m%d')
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
                elif not fresh_break:
                    struct5_reason='WAIT_BREAKOUT'
                elif not struct5_higher_low:
                    struct5_reason='LOW_STRUCTURE_WEAK'
                elif not rank_ok:
                    struct5_reason='RANK_FAIL'
                elif not rsi_ok:
                    struct5_reason='RSI2_FAIL'

            out['struct5_signal']=bool(struct5_signal)
            out['struct5_resistance']=struct5_resistance
            out['struct5_higher_low']=bool(struct5_higher_low)
            out['struct5_rsi2']=struct5_rsi2
            out['struct5_reason']=struct5_reason
'''

if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V110 recovery telemetry block')
s=s.replace(old,new,1)

# Expose compact STRUCT5 diagnostics at tracker level.
old2="""                'williams_cross_age_min':williams_entry_eval.get('recovered_cross_age_min'),\n                'williams_entry_eval':williams_entry_eval,\n"""
new2="""                'williams_cross_age_min':williams_entry_eval.get('recovered_cross_age_min'),\n                'williams_struct5_signal':bool(williams_entry_eval.get('struct5_signal')),\n                'williams_struct5_resistance':williams_entry_eval.get('struct5_resistance'),\n                'williams_struct5_higher_low':bool(williams_entry_eval.get('struct5_higher_low')),\n                'williams_struct5_reason':williams_entry_eval.get('struct5_reason'),\n                'williams_entry_eval':williams_entry_eval,\n"""
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V110 tracker recovery telemetry block')
s=s.replace(old2,new2,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('V111_STRUCT5_PAPER_ENTRY=ENABLED')
print('WINDOW=LATEST_5_CAUSAL_1M_BARS')
print('TRIGGER=CLOSE_BREAK_PREV4_HIGH + HIGHER_LOW + RSI2_GT50 + RANK_LE20')
print('ORIGINAL_WILLIAMS_V110=UNCHANGED')
print('MAX_ONE_STRUCT5_SIGNAL_PER_SYMBOL_DAY=YES')
print('EXTRA_BROKER_API_CALLS=0')
print('MOCK_ORDER_PATH=UNCHANGED')
print('REAL_BROKER_FALLBACK=NO')
