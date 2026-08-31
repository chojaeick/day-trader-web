from pathlib import Path

p = Path('/home/ubuntu/day-trader-api/app_v5.py')
s = p.read_text()

old = "render_kr_trading(API_URL)"
new = "render_kr_trading(API_URL, render_positions)"

if new in s:
    print('KR_SHARED_POSITIONS_ALREADY_PATCHED')
elif old in s:
    s = s.replace(old, new, 1)
    p.write_text(s)
    print('KR_SHARED_POSITIONS_PATCHED')
else:
    raise SystemExit('KR_RENDER_CALL_NOT_FOUND')
