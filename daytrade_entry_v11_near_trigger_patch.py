from pathlib import Path

KOREA=Path('live_server/korea.py')

OLD=r'''                if gate_state=='DEFENSIVE':
                    entry_candidate=breakout and volume_confirm and entry_score>=80
                else:
                    entry_candidate=breakout and entry_score>=72

                state='ENTRY_CANDIDATE' if entry_candidate else ('READY' if breakout else 'WATCH')
                signal='ENTRY_CANDIDATE' if entry_candidate else ('READY' if breakout else 'NONE')
                r.update({
                    'evaluated':True,
                    'bar_time':cur.get('time'),
                    'trigger_price':round(trigger,4),
                    'last_price':round(last,4),
                    'previous_range':round(prev_range,4),
                    'breakout':breakout,
                    'current_volume':cur_vol,
                    'avg_recent_volume':round(avg_vol,2),
                    'volume_confirm':volume_confirm,
                    'entry_score':round(entry_score,1),
                    'state':state,
                    'signal':signal,
                    'reason':'BREAKOUT_CONFIRMED' if entry_candidate else ('BREAKOUT_WAIT_VOLUME_OR_SCORE' if breakout else 'WAIT_BREAKOUT'),
                })
'''

NEW=r'''                # Distance to trigger: negative means price is still below the breakout line.
                trigger_distance_pct=((last-trigger)/trigger*100.0) if trigger>0 else None
                near_trigger=bool(
                    trigger_distance_pct is not None and
                    -0.50 <= trigger_distance_pct < 0.0
                )

                # Reward confirmed approach to the trigger without calling it an entry yet.
                if near_trigger and volume_confirm:
                    entry_score=min(100.0,entry_score+4.0)

                if gate_state=='DEFENSIVE':
                    entry_candidate=breakout and volume_confirm and entry_score>=80
                else:
                    entry_candidate=breakout and entry_score>=72

                ready=bool(
                    (breakout and not entry_candidate) or
                    (near_trigger and volume_confirm and entry_score>=70)
                )

                state='ENTRY_CANDIDATE' if entry_candidate else ('READY' if ready else 'WATCH')
                signal='ENTRY_CANDIDATE' if entry_candidate else ('READY' if ready else 'NONE')

                if entry_candidate:
                    reason='BREAKOUT_CONFIRMED'
                elif breakout:
                    reason='BREAKOUT_WAIT_VOLUME_OR_SCORE'
                elif near_trigger and volume_confirm:
                    reason='NEAR_TRIGGER_VOLUME_CONFIRMED'
                elif near_trigger:
                    reason='NEAR_TRIGGER_WAIT_VOLUME'
                else:
                    reason='WAIT_BREAKOUT'

                r.update({
                    'evaluated':True,
                    'bar_time':cur.get('time'),
                    'trigger_price':round(trigger,4),
                    'last_price':round(last,4),
                    'trigger_distance_pct':None if trigger_distance_pct is None else round(trigger_distance_pct,3),
                    'near_trigger':near_trigger,
                    'previous_range':round(prev_range,4),
                    'breakout':breakout,
                    'current_volume':cur_vol,
                    'avg_recent_volume':round(avg_vol,2),
                    'volume_confirm':volume_confirm,
                    'entry_score':round(entry_score,1),
                    'state':state,
                    'signal':signal,
                    'reason':reason,
                })
'''

def main():
    s=KOREA.read_text()
    if 'trigger_distance_pct' in s and "NEAR_TRIGGER_VOLUME_CONFIRMED" in s:
        print('DAYTRADE_ENTRY_V11_NEAR_TRIGGER_PATCH_ALREADY_APPLIED')
        return
    if OLD not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: daytrade entry state block')
    s=s.replace(OLD,NEW,1)
    s=s.replace("'version':'DAYTRADE_ENTRY_V1',","'version':'DAYTRADE_ENTRY_V1_1_NEAR_TRIGGER',",1)
    s=s.replace("'formula':'Market Gate + Finder(value/volume/gain ranks) + 1m breakout: current close > current open + 0.5*(previous 1m high-low)',","'formula':'Market Gate + Finder(value/volume/gain ranks) + 1m breakout; READY when price is within 0.50% below trigger with volume confirmation',",1)
    KOREA.write_text(s)
    print('DAYTRADE_ENTRY_V11_NEAR_TRIGGER_PATCH_OK')

if __name__=='__main__':
    main()
