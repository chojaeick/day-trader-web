from pathlib import Path
import re

p = Path('/home/ubuntu/day-trader-api/app_v5.py')
s = p.read_text()

# Upgrade the current safe two-tab shell to the four core pages requested by the user.
# Do not alter market switch, runtime mode, KR/US trading dispatch, engine, broker or order code.
if "ui_pages import render_portfolio_page" not in s:
    anchor = "t1,t2=st.tabs(['⚡ Trading','💼 Portfolio'])"
    if anchor not in s:
        raise SystemExit('CORE_TABS_ANCHOR_NOT_FOUND')
    s = s.replace(anchor, "from ui_pages import render_portfolio_page, render_daily_history_page, render_longterm_search_page\n\nt1,t2,t3,t4=st.tabs(['⚡ Trading','💼 Portfolio','🧾 일별 매매내역','🔎 중장기 탐색'])", 1)

# Replace only the portfolio body and append t3/t4. Preserve the already-installed t1 market dispatch.
pat = re.compile(r"\nwith t2:\s*render_portfolio\(market\)\s*(?=\n?\Z)", re.S)
repl = """
with t2:
    render_portfolio_page(
        market,
        get_market_status=get_market_status,
        tracker_rows=tracker_rows,
        render_positions=render_positions,
    )
with t3:
    render_daily_history_page(
        market,
        get_market_status=get_market_status,
    )
with t4:
    render_longterm_search_page(
        market,
        get_market_status=get_market_status,
        tracker_rows=tracker_rows,
        finder_rows=finder_rows,
        search_symbol_ui=search_symbol_ui,
        validate_symbol_ui=validate_symbol_ui,
        quote_snapshot=quote_snapshot,
        money=money,
    )
"""
if 'render_daily_history_page(' not in s:
    s2, n = pat.subn(repl, s, count=1)
    if n != 1:
        raise SystemExit('PORTFOLIO_TAIL_NOT_FOUND')
    s = s2

p.write_text(s)
print('V5_CORE_PAGES_PATCHED')
