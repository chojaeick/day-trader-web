from pathlib import Path
import re

p = Path('/home/ubuntu/day-trader-api/app_v5.py')
s = p.read_text()

# Remove the temporary KR full-page isolation block.  This restores the common
# app_v5 shell (title, market switch, mode controls, tabs) for both markets.
s, n = re.subn(
    r"\n# ===== KR_UI_ISOLATION_V1 =====.*?# ===== /KR_UI_ISOLATION_V1 =====\n",
    "\n",
    s,
    count=1,
    flags=re.S,
)
if n == 0 and 'KR_UI_ISOLATION_V1' in s:
    raise SystemExit('FAILED_TO_REMOVE_KR_ISOLATION')

# Trading tab is the market-module dispatch boundary.  Existing USA renderer is
# injected into ui_us so no USA runtime/engine/order implementation changes.
old = "with t1:render_trading(market)"
new = """with t1:
    if market == 'KOREA':
        from ui_kr import render_kr_trading
        render_kr_trading(API_URL)
    else:
        from ui_us import render_us_trading
        render_us_trading(render_trading)"""

if 'render_kr_trading(API_URL)' not in s:
    if old not in s:
        raise SystemExit('TRADING_TAB_MARKER_NOT_FOUND')
    s = s.replace(old, new, 1)

p.write_text(s)
print('SHARED_MARKET_SHELL_PATCHED')
