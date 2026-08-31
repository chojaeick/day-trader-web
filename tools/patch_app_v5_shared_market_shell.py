from pathlib import Path
import re

p = Path('/home/ubuntu/day-trader-api/app_v5.py')
s = p.read_text()

# Remove temporary full-page KR isolation if present.
s = re.sub(
    r"\n# ===== KR_UI_ISOLATION_V1 =====.*?# ===== /KR_UI_ISOLATION_V1 =====\n",
    "\n",
    s,
    count=1,
    flags=re.S,
)

# If already patched, leave dispatch in place.
if 'render_kr_trading(API_URL)' not in s:
    pat = re.compile(r"with\s+t1\s*:\s*\n?\s*render_trading\s*\(\s*market\s*\)")
    repl = """with t1:\n    if market == 'KOREA':\n        from ui_kr import render_kr_trading\n        render_kr_trading(API_URL)\n    else:\n        from ui_us import render_us_trading\n        render_us_trading(render_trading)"""
    s, n = pat.subn(repl, s, count=1)
    if n == 0:
        # Local production app can differ from repo formatting. Find the Trading
        # tab context and replace the first render_trading(market) after it.
        idx = s.find('render_trading(market)')
        if idx < 0:
            raise SystemExit('RENDER_TRADING_CALL_NOT_FOUND')
        start = s.rfind('\n', 0, idx) + 1
        indent = s[start:idx]
        line_end = s.find('\n', idx)
        if line_end < 0:
            line_end = len(s)
        call_line = s[start:line_end]
        if call_line.strip() != 'render_trading(market)':
            raise SystemExit('UNSAFE_RENDER_TRADING_CONTEXT')
        block = (
            indent + "if market == 'KOREA':\n" +
            indent + "    from ui_kr import render_kr_trading\n" +
            indent + "    render_kr_trading(API_URL)\n" +
            indent + "else:\n" +
            indent + "    from ui_us import render_us_trading\n" +
            indent + "    render_us_trading(render_trading)"
        )
        s = s[:start] + block + s[line_end:]

p.write_text(s)
print('SHARED_MARKET_SHELL_PATCHED')
