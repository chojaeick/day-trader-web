from pathlib import Path
import re

APP = Path('app_v5.py')


def main():
    s = APP.read_text()

    anchor = "if 'v5_market' not in st.session_state:\n"
    if anchor not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: market state anchor')

    idx = s.index(anchor)
    prefix, suffix = s[:idx], s[idx:]

    # Remove any previously accumulated visible DAY TRADER title/header calls
    # from the executable tail before market initialization. Keep CSS definitions.
    tail_start = max(0, len(prefix) - 8000)
    head, tail = prefix[:tail_start], prefix[tail_start:]

    # One-line Streamlit title/caption calls.
    tail = re.sub(r"(?m)^\s*st\.title\([^\n]*DAY TRADER V5[^\n]*\)\s*\n?", "", tail)
    tail = re.sub(r"(?m)^\s*st\.caption\([^\n]*DECISION TERMINAL[^\n]*\)\s*\n?", "", tail)

    # Single-line markdown calls containing a visible V5 title (v18-v23 variants).
    tail = re.sub(
        r"(?m)^\s*st\.markdown\([^\n]*DAY TRADER V5[^\n]*unsafe_allow_html\s*=\s*True[^\n]*\)\s*\n?",
        "",
        tail,
    )

    # Multi-line markdown blocks containing a visible DAY TRADER title. Avoid CSS blocks.
    def drop_visible_title_block(m):
        block = m.group(0)
        if '<style>' in block.lower():
            return block
        if 'DAY TRADER V5' in block:
            return ''
        return block

    tail = re.sub(
        r"(?ms)^\s*st\.markdown\(\s*(?:f|r)?(?:'''|\"\"\").*?(?:'''|\"\"\")\s*,?\s*unsafe_allow_html\s*=\s*True\s*\)\s*\n?",
        drop_visible_title_block,
        tail,
    )

    # Remove stray v20/v21/v22/v23 visible header fragments if a previous patch
    # generated them as adjacent markdown expressions.
    tail = re.sub(r"(?m)^.*<div class=[\"']v(?:18|19|20|21|22|23)-header[\"']>.*$\n?", "", tail)

    header = (
        "st.markdown('<div class=\"v24-header\"><span class=\"v24-bolt\">⚡</span>"
        "<span>DAY TRADER V5</span><span class=\"v24-ver\">v24</span></div>"
        "<div class=\"v24-tagline\">DECISION TERMINAL · MANUAL ORDER · 실시간 연결 유지 · 단타 분석은 필요할 때 가속</div>',"
        "unsafe_allow_html=True)\n"
    )

    s = head + tail + header + suffix

    # Append a deterministic final CSS override once.
    if 'V24 HEADER DEDUPE' not in s:
        css = '''st.markdown("""
<style>
/* ===== V24 HEADER DEDUPE ===== */
.block-container{padding-top:.55rem!important;max-width:1560px!important}
.v24-header{display:flex;align-items:center;gap:.48rem;font-size:2rem;font-weight:950;letter-spacing:-.045em;line-height:1.08;margin:0 0 .08rem;color:#f5f8ff}
.v24-bolt{color:#ffc21c;font-size:1.72rem;line-height:1}.v24-ver{font-size:.62rem;color:#4d9cff;font-weight:850;letter-spacing:0;margin-left:.08rem}
.v24-tagline{color:#8393a8;font-size:.70rem;margin:0 0 .36rem}
/* Hide accidental empty title containers left by older patches. */
[data-testid="stHeading"]:empty{display:none!important}
</style>
""", unsafe_allow_html=True)
'''
        api_anchor = 'def api(path, timeout=10):\n'
        if api_anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        s = s.replace(api_anchor, css + '\n' + api_anchor, 1)

    APP.write_text(s)
    print('PREOPEN_UI_HEADER_DEDUPE_V24_OK')


if __name__ == '__main__':
    main()
