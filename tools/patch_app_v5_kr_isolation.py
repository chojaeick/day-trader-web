from pathlib import Path

p = Path('/home/ubuntu/day-trader-api/app_v5.py')
s = p.read_text()
marker = "market=st.session_state['v5_market']\n"
block = """market=st.session_state['v5_market']

# ===== KR_UI_ISOLATION_V1 =====
# KOREA renders through its own module and stops here. USA code below is left
# untouched and continues to use the existing production-connected path.
if market == 'KOREA':
    from ui_kr import render_kr_app
    render_kr_app(API_URL)
    st.stop()
# ===== /KR_UI_ISOLATION_V1 =====
"""
if 'KR_UI_ISOLATION_V1' in s:
    print('KR_UI_ISOLATION_ALREADY_PRESENT')
else:
    if marker not in s:
        raise SystemExit('MARKET MARKER NOT FOUND')
    s = s.replace(marker, block, 1)
    p.write_text(s)
    print('KR_UI_ISOLATION_PATCHED')
