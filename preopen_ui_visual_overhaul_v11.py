from pathlib import Path
import re

APP=Path('app_v5.py')

CSS=r'''
/* ===== V11 VISUAL OVERHAUL ===== */
.block-container{padding-top:.75rem!important;max-width:1540px!important;padding-left:1.15rem!important;padding-right:1.15rem!important}
html,body,[class*="css"]{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.v5-title{font-size:2.15rem!important;font-weight:900!important;letter-spacing:-.035em;margin:.15rem 0 0!important}
.v5-sub{font-size:.72rem!important;color:#8b98ab!important;margin:.05rem 0 .35rem!important}
h1,h2,h3{letter-spacing:-.025em!important}
hr{border-color:#223044!important;margin:.55rem 0!important}
[data-testid="stVerticalBlock"]{gap:.42rem!important}
[data-testid="stHorizontalBlock"]{gap:.65rem!important}
[data-testid="stMetric"]{background:linear-gradient(180deg,#0e1826 0%,#0a121d 100%);border:1px solid #20334b;border-radius:10px;padding:.55rem .7rem!important;box-shadow:0 8px 24px rgba(0,0,0,.16)}
[data-testid="stMetricLabel"]{font-size:.68rem!important;color:#94a3b8!important;font-weight:700!important}
[data-testid="stMetricValue"]{font-size:1.18rem!important;font-weight:850!important;letter-spacing:-.02em}
.v5-card{background:linear-gradient(180deg,#0c1725 0%,#0a131f 100%)!important;border:1px solid #213652!important;border-radius:12px!important;padding:10px 12px!important;box-shadow:0 10px 28px rgba(0,0,0,.15)!important}
.v5-note{background:#0c1d31!important;border:1px solid #17395f!important;border-left:3px solid #258cff!important;border-radius:9px!important}
.v5-warn{background:#241d0e!important;border:1px solid #493812!important;border-left:3px solid #f6b73c!important;border-radius:9px!important}
.hold-symbol{font-size:1.05rem!important;font-weight:900!important;letter-spacing:-.02em!important}
.hold-sub{font-size:.66rem!important;color:#7890ac!important}
.hold-head{font-size:.64rem!important;color:#8190a5!important;text-transform:uppercase!important;letter-spacing:.04em!important}
.hold-val{font-size:.94rem!important;font-weight:800!important}
.stButton>button{border-radius:8px!important;border:1px solid #2a3a51!important;background:#101a28!important;font-weight:750!important;transition:.15s ease!important}
.stButton>button:hover{border-color:#258cff!important;color:#fff!important;transform:translateY(-1px)!important}
.stButton>button[kind="primary"]{background:linear-gradient(180deg,#1988ff,#0764d8)!important;border-color:#2c92ff!important;box-shadow:0 5px 16px rgba(16,116,255,.24)!important}
[data-testid="stExpander"]{border:1px solid #20344e!important;border-radius:10px!important;background:#0a1420!important;overflow:hidden!important}
[data-testid="stExpander"] summary{font-weight:800!important;background:#0d1826!important}
[data-testid="stDataFrame"]{border:1px solid #20334b!important;border-radius:10px!important;overflow:hidden!important}
[data-baseweb="input"]{background:#0d1724!important;border-color:#273a53!important;border-radius:8px!important}
[data-baseweb="select"]>div{background:#0d1724!important;border-color:#273a53!important;border-radius:8px!important}
.stTabs [data-baseweb="tab-list"]{background:#09111b!important;border-bottom:1px solid #203044!important;padding:.18rem!important;border-radius:9px 9px 0 0!important}
.stTabs [data-baseweb="tab"]{font-weight:750!important;border-radius:7px!important}
/* make trading top two columns feel like panels */
div[data-testid="column"]:has(h3){min-width:0}
/* cleaner alerts */
div[data-testid="stAlert"]{border-radius:9px!important;border:1px solid #26384e!important;padding:.45rem .65rem!important}
/* compact top controls */
@media (min-width:1100px){.block-container{padding-top:.55rem!important}.stButton button{min-height:2rem!important}}
'''

def main():
    s=APP.read_text()
    if 'V11 VISUAL OVERHAUL' not in s:
        marker='</style>'
        if marker not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: style close')
        s=s.replace(marker, CSS+'\n</style>', 1)

    # Friendlier registration wording: user should not need to think in codes only.
    s=s.replace("text_input('종목코드',placeholder='SOXL / NVDA / 005930'", "text_input('종목명 / 코드',placeholder='삼성전자 / 005930 / SOXL / NVDA'")
    s=s.replace("text_input('종목코드', placeholder='SOXL / NVDA / 005930'", "text_input('종목명 / 코드', placeholder='삼성전자 / 005930 / SOXL / NVDA'")
    s=s.replace('실제 보유 종목','보유주식 관리')
    s=s.replace('보유주식 직접 등록','보유주식 등록')

    # Card language polish without changing trading logic.
    s=s.replace('### ⚡ 지금 단타 후보','### ⚡ 지금 단타 후보 TOP 5')
    s=s.replace('### 🛡 보유주식 관리','### 🛡 보유주식 관리')

    APP.write_text(s)
    print('PREOPEN_UI_VISUAL_OVERHAUL_V11_OK')

if __name__=='__main__':
    main()
