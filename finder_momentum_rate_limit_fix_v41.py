from pathlib import Path

KI=Path('live_server/kiwoom.py')
API=Path('live_server/api.py')


def main():
    s=KI.read_text()

    # V41: Kiwoom usa06012 has a strict request-flow limit.  The V39/V40
    # momentum lane must not fan out daily-history calls concurrently.
    s=s.replace('with ThreadPoolExecutor(max_workers=4) as exe:',
                'with ThreadPoolExecutor(max_workers=1) as exe:')

    # Pace every usa06012 page request in the V39 daily feature.  _time is
    # already imported locally by that helper.
    needle="""            d=r.json()\n            if d.get('return_code') not in (None,0):\n                raise RuntimeError(f\"usa06012 {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}\")\n"""
    repl="""            d=r.json()\n            _time.sleep(0.35)\n            if d.get('return_code') not in (None,0):\n                raise RuntimeError(f\"usa06012 {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}\")\n"""
    if needle in s and '_time.sleep(0.35)' not in s:
        s=s.replace(needle,repl,1)

    KI.write_text(s)

    a=API.read_text()
    a=a.replace('volume=k.volume_rank()','volume=k.ranking_today_volume(\'0\')')
    a=a.replace('dollar=k.dollar_rank()','dollar=k.ranking_today_volume(\'1\')')
    API.write_text(a)

    print('FINDER_MOMENTUM_RATE_LIMIT_FIX_V41_OK')

if __name__=='__main__':
    main()
