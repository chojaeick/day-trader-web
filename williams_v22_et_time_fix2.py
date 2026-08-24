from pathlib import Path

P=Path('daytrade_entry_sim_v2_williams_filters_usa.py')

def main():
    s=P.read_text()
    old = '''rows=con.execute("select ts,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and session='REGULAR' order by ts",(symbol,d)).fetchall()'''
    new = '''rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and session='REGULAR' order by et_time",(symbol,d)).fetchall()'''
    if old not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: select ts block')
    s=s.replace(old,new,1)

    old2 = '''            # Robust ET time parsing.
            # Prefer explicit et_time column semantics when encoded in ts-like strings;
            # support ISO timestamps, YYYYMMDDHHMMSS, and epoch seconds/ms.
            s=str(ts).strip()
            hhmm=None
            try:
                if 'T' in s:
                    t=s.split('T',1)[1][:5]
                    hhmm=int(t.replace(':',''))
                elif s.isdigit() and len(s) in (10,13):
                    from datetime import datetime, timezone
                    from zoneinfo import ZoneInfo
                    epoch=float(s)/(1000.0 if len(s)==13 else 1.0)
                    dt=datetime.fromtimestamp(epoch,timezone.utc).astimezone(ZoneInfo('America/New_York'))
                    hhmm=dt.hour*100+dt.minute
                elif s.isdigit() and len(s)>=12:
                    # YYYYMMDDHHMM[SS]
                    hhmm=int(s[8:12])
                elif ' ' in s and ':' in s:
                    t=s.split(' ',1)[1][:5]
                    hhmm=int(t.replace(':',''))
            except Exception:
                hhmm=None
            morning=bool(hhmm is not None and 930 <= hhmm <= 1100)
'''
    new2 = '''            # Use explicit ET time from historical_minute_bars.et_time.
            et=str(ts).strip()
            hhmm=None
            try:
                if 'T' in et:
                    t=et.split('T',1)[1][:5]
                    hhmm=int(t.replace(':',''))
                elif ':' in et:
                    hhmm=int(et[:5].replace(':',''))
                elif et.isdigit() and len(et)>=4:
                    hhmm=int(et[-6:-2] if len(et)>=6 else et[:4])
            except Exception:
                hhmm=None
            morning=bool(hhmm is not None and 930 <= hhmm <= 1100)
'''
    if old2 not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: parser block')
    s=s.replace(old2,new2,1)
    s=s.replace('=== WILLIAMS V2.1 FILTER COMPARISON USA ===','=== WILLIAMS V2.2 FILTER COMPARISON USA ===',1)
    P.write_text(s)
    print('WILLIAMS_V22_ET_TIME_FIX_OK')

if __name__=='__main__':
    main()
