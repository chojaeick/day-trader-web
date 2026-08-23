from pathlib import Path
import re

APP=Path('app_v5.py')

HELPERS=r'''

# ===== UI FUJIMOTO SWING ROLE SPLIT V60 =====
@st.cache_data(ttl=300,show_spinner=False)
def fujimoto_swing_ui(symbol):
    sym=str(symbol or '').strip().upper()
    if not sym:
        return {}
    x=api(f'/api/v5/fujimoto-swing/KOREA/{sym}',12)
    return x if isinstance(x,dict) and x.get('ok') else {}


def fujimoto_swing_state_ko(state):
    m={
        'STRONG_ENTRY':'강한 진입 후보',
        'ENTRY_READY':'진입 준비',
        'PREPARE':'관찰/준비',
        'WATCH':'관찰',
        'EXIT_REVIEW':'매도 검토',
    }
    s=str(state or '').upper()
    return m.get(s,s or '-')


def fujimoto_swing_table(symbol):
    d=fujimoto_swing_ui(symbol)
    if not d:
        return pd.DataFrame([{'엔진':'Fujimoto Swing','기간':'2~10일','점수':'-','상태':'데이터 대기','RSI':'-','판단':'연결 대기'}])
    return pd.DataFrame([{
        '엔진':'Fujimoto Swing',
        '기간':'2~10일',
        '점수':f"{int(d.get('score'))}/100" if d.get('score') is not None else '-',
        '상태':fujimoto_swing_state_ko(d.get('state')),
        'RSI':('-' if d.get('rsi') is None else round(float(d.get('rsi')),1)),
        '판단':('매도 검토' if d.get('exit_review') else '보유/진입 판단'),
    }])
'''


def main():
    s=APP.read_text()

    if 'UI FUJIMOTO SWING ROLE SPLIT V60' not in s:
        m=re.search(r'\ndef render_trading\(market\):',s)
        if not m:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading')
        s=s[:m.start()]+HELPERS+s[m.start():]

    # Remove Fujimoto-based intraday TOP5 override. Daytrade candidates return to the original source.
    s=re.sub(r"\n\s*if market=='KOREA':\s*\n\s*source=fujimoto_top5_rows_korea\(source,5\)\s*",'\n',s,count=1)

    # Replace v59 Fujimoto-aware top5 table call with the original recommendation table for trading.
    # Keep v59 helper defined for rollback/debug, but do not use it for live daytrade ranking.
    s=s.replace('st.dataframe(recommendation_table_fujimoto_v59(source,market),use_container_width=True,hide_index=True,height=205)',
                'st.dataframe(recommendation_table(source,market),use_container_width=True,hide_index=True,height=205)')

    # Add swing evaluation inside each Korea holding expander. Idempotent marker prevents duplicates.
    marker="# FUJIMOTO_SWING_HOLDING_V60"
    if marker not in s:
        pat=r"(with st\.expander\(f'\{sym\} 상세 엔진 평가',expanded=False\):\n)(.*?)(\n\s*st\.divider\(\))"
        m=re.search(pat,s,re.S)
        if not m:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: holding expander')
        body=m.group(2)
        add="\n            # FUJIMOTO_SWING_HOLDING_V60\n            if market=='KOREA':\n                st.caption('Fujimoto Swing · 일봉 RSI(14)+MACD(12,26,9) · 목표 보유 2~10영업일')\n                st.dataframe(fujimoto_swing_table(sym),hide_index=True,use_container_width=True,height=90)\n"
        body=body.rstrip()+add
        s=s[:m.start()]+m.group(1)+body+m.group(3)+s[m.end():]

    APP.write_text(s)
    print('UI_FUJIMOTO_SWING_ROLE_SPLIT_V60_OK')

if __name__=='__main__':
    main()
