from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

KOREA_PATCH=r'''

    # ===== DAYTRADE ENTRY V1.2 SESSION GUARD =====
    def daytrade_entry_v12(self, limit=10, eval_limit=5, max_pages=1):
        """V1.1 + KST regular-session guard.

        Signal-only. READY/ENTRY_CANDIDATE are only live during Korean regular session
        (weekdays 09:00 <= KST < 15:30). Outside that window evaluated rows are retained
        as reference only and downgraded to CLOSED_STANDBY / NONE.
        """
        from datetime import datetime, timezone, timedelta

        d=self.daytrade_entry_v11(limit,eval_limit,max_pages)
        kst=datetime.now(timezone(timedelta(hours=9)))
        hhmm=kst.hour*100+kst.minute
        weekday=kst.weekday() < 5
        regular_open=bool(weekday and 900 <= hhmm < 1530)

        d['version']='DAYTRADE_ENTRY_V1_2_SESSION_GUARD'
        d['session']={
            'timezone':'Asia/Seoul',
            'kst_now':kst.isoformat(),
            'regular_open':regular_open,
            'rule':'Weekdays 09:00 <= KST < 15:30',
        }

        if not regular_open:
            for r in d.get('rows',[]):
                if r.get('evaluated'):
                    prev_state=r.get('state')
                    prev_signal=r.get('signal')
                    r['reference_state']=prev_state
                    r['reference_signal']=prev_signal
                    r['state']='CLOSED_STANDBY'
                    r['signal']='NONE'
                    r['reason']='MARKET_CLOSED_REFERENCE_ONLY'
            d['entry_candidate_count']=0
            d['ready_count']=0
            d['market_blocked']=True
            d['session_blocked']=True
        else:
            d['session_blocked']=False

        d['formula']=str(d.get('formula') or '')+'; live entry states only during KRX regular session'
        return d
'''


def main():
    s=KOREA.read_text()
    if 'def daytrade_entry_v12' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,KOREA_PATCH+'\n'+anchor,1)
        KOREA.write_text(s)

    a=API.read_text()
    old="return await asyncio.to_thread(korea.daytrade_entry_v11,limit,eval_limit,max_pages)"
    new="return await asyncio.to_thread(korea.daytrade_entry_v12,limit,eval_limit,max_pages)"
    if old in a:
        a=a.replace(old,new,1)
    elif 'korea.daytrade_entry_v12' not in a:
        old2="return await asyncio.to_thread(korea.daytrade_entry_v1,limit,eval_limit,max_pages)"
        if old2 in a:
            a=a.replace(old2,new,1)
        else:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: daytrade endpoint')
    API.write_text(a)
    print('DAYTRADE_ENTRY_V12_SESSION_GUARD_PATCH_OK')

if __name__=='__main__': main()
