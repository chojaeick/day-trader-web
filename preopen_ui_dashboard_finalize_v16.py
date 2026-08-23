from pathlib import Path
import re

APP=Path('app_v5.py')


def main():
    s=APP.read_text()

    # 1) Resolve Korean candidate names globally using the already-wired Kiwoom master search.
    helper = r'''
@st.cache_data(ttl=300,show_spinner=False)
def resolve_display_name(market,symbol,fallback=''):
    sym=str(symbol or '').strip().upper()
    fb=str(fallback or '').strip()
    if not sym:
        return fb or '-'
    if market=='KOREA':
        try:
            rows=search_symbol_ui('KOREA',sym)
            for r in rows:
                if str(r.get('symbol') or '').strip().upper()==sym:
                    nm=str(r.get('name') or '').strip()
                    if nm and nm!=sym:
                        return nm
        except Exception:
            pass
    return fb if fb and fb!=sym else sym


def enrich_display_names(rows,market):
    out=[]
    for src in rows or []:
        r=dict(src)
        sym=str(r.get('symbol') or '').strip().upper()
        old=str(r.get('name') or '').strip()
        r['name']=resolve_display_name(market,sym,old)
        out.append(r)
    return out

'''
    if 'def enrich_display_names(' not in s:
        anchor='def recommendation_table(rows,market,limit=5):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: recommendation_table')
        s=s.replace(anchor,helper+anchor,1)

    # Recommendation table: enrich before rendering.
    s=s.replace("def recommendation_table(rows,market,limit=5):\n    out=[]",
                "def recommendation_table(rows,market,limit=5):\n    rows=enrich_display_names(rows,market)\n    out=[]",1)

    # Selected-detail: name resolver for Korean code-only rows.
    s=s.replace("symbol=r.get('symbol') or '-';name=r.get('name') or symbol;reason=",
                "symbol=r.get('symbol') or '-';name=resolve_display_name(market,symbol,r.get('name') or '');reason=",1)

    # Holdings display helper: ensure it resolves through the same path.
    if 'def holding_display_name(' in s:
        s=re.sub(
            r"@st\.cache_data\(ttl=300,show_spinner=False\)\ndef holding_display_name\(market,symbol\):.*?(?=\ndef render_positions\()",
            "@st.cache_data(ttl=300,show_spinner=False)\ndef holding_display_name(market,symbol):\n    return resolve_display_name(market,symbol,'')\n\n",
            s, count=1, flags=re.S)

    # 2) Strong visual cleanup: one compact command bar, clearer cards and tables.
    css = r'''
<style>
:root{--v5-bg:#0a0f17;--v5-panel:#0d1623;--v5-border:#20334a;--v5-text:#eef5ff;--v5-muted:#8092aa;--v5-blue:#1f8cff;--v5-green:#00d97e;--v5-red:#ff4d61;--v5-amber:#ffb020}
.stApp{background:radial-gradient(circle at 70% -10%,#0d2035 0,#0a0f17 34%,#080d14 72%);color:var(--v5-text)}
.block-container{padding-top:.55rem!important;padding-bottom:1rem!important;max-width:1540px!important}
.v5-title{font-size:2.18rem!important;font-weight:900!important;letter-spacing:-.04em;margin:0!important}
.v5-sub{font-size:.72rem!important;color:var(--v5-muted)!important;margin:.08rem 0 .28rem!important}
h1,h2,h3{letter-spacing:-.025em}
[data-testid="stVerticalBlock"]{gap:.32rem!important}
[data-testid="stHorizontalBlock"]{gap:.7rem!important}
[data-testid="stMetric"]{background:linear-gradient(180deg,#101b29,#0c1521);border:1px solid var(--v5-border);border-radius:10px;padding:.48rem .72rem!important;min-height:72px}
[data-testid="stMetricLabel"]{color:#8da0b9!important;font-size:.7rem!important}
[data-testid="stMetricValue"]{font-size:1.22rem!important;font-weight:800!important}
[data-testid="stDataFrame"]{border:1px solid var(--v5-border);border-radius:10px;overflow:hidden;background:#0b131e}
.v5-card{background:linear-gradient(180deg,#0e1927,#0b1420)!important;border:1px solid var(--v5-border)!important;border-radius:10px!important;padding:10px 12px!important;box-shadow:0 8px 24px rgba(0,0,0,.16)}
.hold-symbol{font-size:1rem!important;font-weight:850!important;color:#f5f8ff}.hold-sub{font-size:.62rem!important;color:#70839c!important}
.hold-head{font-size:.64rem!important;color:#7d90aa!important}.hold-val{font-size:.92rem!important;font-weight:780!important}
.stButton>button{border-radius:8px!important;border:1px solid #29415c!important;background:#101a27!important;font-weight:720!important;min-height:2rem!important}
.stButton>button[kind="primary"]{background:linear-gradient(180deg,#238cff,#0875e8)!important;border-color:#3b9cff!important;color:white!important}
[data-baseweb="select"]>div,[data-baseweb="input"]{border-radius:8px!important;background:#111925!important;border-color:#27384d!important}
[data-testid="stExpander"]{border:1px solid var(--v5-border)!important;border-radius:10px!important;background:#0b1420!important}
hr{border-color:#1d2a3a!important;margin:.45rem 0!important}
</style>
'''
    if '--v5-bg:#0a0f17' not in s:
        pos=s.find('</style>')
        if pos<0:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: base style')
        s=s[:pos+8]+css+s[pos+8:]

    # 3) Candidate/selected name-first labels in selector where possible.
    # Change candidate selector label from code-first to name-first without changing value mapping.
    s=s.replace("f\"{r.get('symbol')} · {action_ko(action_of(r))} · Power {f(r.get('power')):+.1f}\"",
                "f\"{resolve_display_name(market,r.get('symbol'),r.get('name') or '')} · {r.get('symbol')} · {action_ko(action_of(r))}\"",1)

    APP.write_text(s)
    print('PREOPEN_UI_DASHBOARD_FINALIZE_V16_OK')

if __name__=='__main__':
    main()
