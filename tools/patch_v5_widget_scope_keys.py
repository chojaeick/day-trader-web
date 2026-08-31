from pathlib import Path

app = Path('/home/ubuntu/day-trader-api/app_v5.py')
pages = Path('/home/ubuntu/day-trader-api/ui_pages.py')

s = app.read_text()

# render_positions is used in more than one Streamlit tab. Streamlit executes
# every tab body on each rerun, so widget keys must be scoped by page.
s = s.replace("def render_positions(market,tracker):", "def render_positions(market,tracker,scope='trading'):")
s = s.replace("render_manual_holding(market,'holdings')", "render_manual_holding(market,f'holdings_{scope}')")
s = s.replace("key=f'kind_{market}_{sym}'", "key=f'kind_{market}_{scope}_{sym}'")
s = s.replace("key=f'kind_apply_{market}_{sym}'", "key=f'kind_apply_{market}_{scope}_{sym}'")
s = s.replace("key=f'del_{market}_{sym}'", "key=f'del_{market}_{scope}_{sym}'")

if "def render_positions(market,tracker,scope='trading'):" not in s:
    raise SystemExit('RENDER_POSITIONS_SCOPE_PATCH_FAILED')

app.write_text(s)

u = pages.read_text()
u = u.replace("render_positions(market, tracker)\n", "render_positions(market, tracker, scope='portfolio')\n", 1)
if "render_positions(market, tracker, scope='portfolio')" not in u:
    raise SystemExit('PORTFOLIO_SCOPE_PATCH_FAILED')
pages.write_text(u)

print('V5_WIDGET_SCOPE_KEYS_PATCHED')
