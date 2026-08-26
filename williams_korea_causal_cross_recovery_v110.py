#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

old=r'''            prev_day_high=float(prev['high'].max())
            prev_day_low=float(prev['low'].min())
            day_open=float(cur.iloc[0]['open'])
            prev_price=float(cur.iloc[-2]['close'])
            current_price=float(cur.iloc[-1]['close'])
            recent_closes=[float(v) for v in cur['close'].tail(30).tolist()]

            out=williams_live_evaluate_v23(
                symbol=sym,
                prev_day_high=prev_day_high,
                prev_day_low=prev_day_low,
                day_open=day_open,
                prev_price=prev_price,
                current_price=current_price,
                recent_closes=recent_closes,
                finder_rank=finder_rank,
            )
            out['source']='KOREA_SHADOW_GATE_REUSE'
'''

new=r'''            prev_day_high=float(prev['high'].max())
            prev_day_low=float(prev['low'].min())
            day_open=float(cur.iloc[0]['open'])
            prev_price=float(cur.iloc[-2]['close'])
            current_price=float(cur.iloc[-1]['close'])
            recent_closes=[float(v) for v in cur['close'].tail(30).tolist()]

            # V110: causal same-day recovery for symbols that enter the live candidate pool
            # after the actual Williams CrossUp already happened. Scan only bars that existed
            # at each historical minute, compute RSI2 causally, and recover only a cross that
            # is still inside the original 30-minute confirmation window.
            trigger=day_open + 0.5*(prev_day_high-prev_day_low)
            now_kst=_dt.now(_WILLIAMS_KST)
            st=_WILLIAMS_STATE[(str(sym),now_kst.strftime('%Y%m%d'))]
            recovered_cross_time=None
            recovered_cross_age_min=None
            recovered_cross_rsi2=None

            if not st.get('signal_sent') and st.get('armed_at') is None and len(cur)>=2:
                candidates=[]
                closes_all=[float(v) for v in cur['close'].tolist()]
                for i in range(1,len(cur)):
                    p0=float(cur.iloc[i-1]['close'])
                    p1=float(cur.iloc[i]['close'])
                    if not (p0 <= trigger < p1):
                        continue
                    rsi_i=_williams_rsi2(closes_all[:i+1])
                    if rsi_i is None or rsi_i <= 50.0:
                        continue
                    ts=str(cur.iloc[i]['time'])
                    try:
                        digits=''.join(ch for ch in ts if ch.isdigit())
                        if len(digits)>=14:
                            cross_dt=_dt.strptime(digits[:14],'%Y%m%d%H%M%S').replace(tzinfo=_WILLIAMS_KST)
                        elif len(digits)>=12:
                            cross_dt=_dt.strptime(digits[:12],'%Y%m%d%H%M').replace(tzinfo=_WILLIAMS_KST)
                        else:
                            continue
                    except Exception:
                        continue
                    age=(now_kst-cross_dt).total_seconds()/60.0
                    if 0.0 <= age <= 30.0:
                        candidates.append((cross_dt,age,float(rsi_i)))

                if candidates:
                    cross_dt,age,rsi_i=max(candidates,key=lambda z:z[0])
                    st['armed_at']=cross_dt
                    recovered_cross_time=cross_dt.isoformat()
                    recovered_cross_age_min=round(age,3)
                    recovered_cross_rsi2=round(rsi_i,4)

            out=williams_live_evaluate_v23(
                symbol=sym,
                prev_day_high=prev_day_high,
                prev_day_low=prev_day_low,
                day_open=day_open,
                prev_price=prev_price,
                current_price=current_price,
                recent_closes=recent_closes,
                finder_rank=finder_rank,
                now=now_kst,
            )
            out['source']='KOREA_SHADOW_GATE_REUSE'
            out['historical_cross_recovered']=bool(recovered_cross_time)
            out['recovered_cross_time']=recovered_cross_time
            out['recovered_cross_age_min']=recovered_cross_age_min
            out['recovered_cross_rsi2']=recovered_cross_rsi2
'''

if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V107 entry evaluator core block')
s=s.replace(old,new,1)

# Expose compact recovery diagnostics at tracker top level.
old2="""                'williams_entry_raw_cross':bool(williams_entry_eval.get('raw_cross')),\n                'williams_entry_eval':williams_entry_eval,\n"""
new2="""                'williams_entry_raw_cross':bool(williams_entry_eval.get('raw_cross')),\n                'williams_cross_recovered':bool(williams_entry_eval.get('historical_cross_recovered')),\n                'williams_cross_time':williams_entry_eval.get('recovered_cross_time'),\n                'williams_cross_age_min':williams_entry_eval.get('recovered_cross_age_min'),\n                'williams_entry_eval':williams_entry_eval,\n"""
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V107 tracker entry telemetry block')
s=s.replace(old2,new2,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('V110_CAUSAL_RECOVERY=ENABLED')
print('RECOVERY_SCOPE=SAME_DAY_CROSSUP_RSI2_GT50_LAST_30M')
print('FINDER_CONFIRMATION=UNCHANGED_LE20')
print('MAX_ONE_SIGNAL_PER_SYMBOL_DAY=UNCHANGED')
print('EXTRA_BROKER_API_CALLS=0')
print('MOCK_ORDER_PATH=UNCHANGED')
print('REAL_BROKER_FALLBACK=NO')
