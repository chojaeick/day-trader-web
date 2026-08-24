from pathlib import Path

P=Path('daytrade_entry_sim_v2_williams_filters_usa.py')

OLD="""            # infer local ET time from ts string; DB ts is ET-like timestamp in these caches
            s=str(ts)
            hhmm=None
            try:
                if 'T' in s:
                    t=s.split('T',1)[1][:5].replace(':',''); hhmm=int(t)
                elif len(s)>=12:
                    hhmm=int(s[-6:-2])
            except: hhmm=None
            morning=bool(hhmm is not None and 930 <= hhmm <= 1100)
"""

NEW="""            # Robust ET time parsing.
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
"""

def main():
    s=P.read_text()
    if OLD not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: morning parser')
    s=s.replace(OLD,NEW,1)
    s=s.replace('=== WILLIAMS V2 FILTER COMPARISON USA ===','=== WILLIAMS V2.1 FILTER COMPARISON USA ===',1)
    P.write_text(s)
    print('WILLIAMS_V21_TIME_PARSE_FIX_OK')

if __name__=='__main__':
    main()
