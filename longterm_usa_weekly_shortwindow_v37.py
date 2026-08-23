from pathlib import Path
import re

API=Path('live_server/api.py')
APP=Path('app_v5.py')


def main():
    s=API.read_text()

    # Kiwoom usa06012 often returns only a bounded recent window for US names.
    # Long requests can return zero rows; weekly MA10 only needs ~10+ weeks,
    # so query a short recent window for the weekly fallback.
    old="rows,pages,meta=_v35_us_daily_rows(symbol)"
    new="rows,pages,meta=_v35_us_daily_rows(symbol,days=(500 if kind=='month' else 180),max_pages=(8 if kind=='month' else 4))"
    if old in s:
        s=s.replace(old,new,1)
    elif new not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: v35 us history helper')

    API.write_text(s)

    a=APP.read_text()
    # Force Streamlit data cache invalidation after changing the broker feed behavior.
    a=a.replace("cache_epoch='v33'","cache_epoch='v37'",1)
    a=a.replace("cache_epoch='v32'","cache_epoch='v37'",1)
    a=a.replace('class="v24-ver">v33</span>','class="v24-ver">v37</span>',1)
    a=a.replace('class="v24-ver">v32</span>','class="v24-ver">v37</span>',1)
    APP.write_text(a)

    print('LONGTERM_USA_WEEKLY_SHORTWINDOW_V37_OK')

if __name__=='__main__':
    main()
