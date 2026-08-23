from pathlib import Path
import re

APP=Path('app_v5.py')


def replace_once(s, pat, repl, label, flags=re.S):
    m=re.search(pat,s,flags)
    if not m:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return s[:m.start()]+repl+s[m.end():]


def main():
    s=APP.read_text()

    css=r'''
st.markdown('''
<style>
:root{--bg:#07111f;--panel:#0c1727;--panel2:#0a1422;--line:#1d314c;--txt:#eef5ff;--muted:#7f91aa;--blue:#2b8cff;--cyan:#2db9ff;--green:#20d77a;--red:#ff4e63;--amber:#ffb326;--purple:#8b5cf6}
.block-container{max-width:1600px!important;padding:1.15rem 1.45rem 1.2rem!important}
.v12-hero{display:flex;align-items:center;gap:12px;margin-bottom:4px}.v12-bolt{font-size:2rem}.v12-title{font-size:2.15rem;font-weight:950;letter-spacing:-.04em}.v12-sub{font-size:.78rem;color:var(--muted)}
.v12-section-title{font-size:1.15rem;font-weight:900;letter-spacing:-.02em;margin:4px 0 2px}.v12-section-sub{font-size:.72rem;color:var(--muted);margin-bottom:6px}
.v12-card{border:1px solid var(--line);background:linear-gradient(180deg,var(--panel) 0%,var(--panel2) 100%);border-radius:14px;padding:12px 14px}
.v12-name{font-size:1rem;font-weight:850}.v12-code{font-size:.68rem;color:var(--muted)}
.v12-chip{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid var(--line);font-size:.67rem;font-weight:800}.v12-chip-blue{color:#69b2ff;background:#0c2440}.v12-chip-purple{color:#b99aff;background:#20133a}.v12-chip-green{color:#5ceba5;background:#0b2b1f}
.v12-good{color:var(--green)!important}.v12-bad{color:var(--red)!important}.v12-muted{color:var(--muted)!important}
[data-testid="stMetric"]{border:1px solid var(--line);border-radius:12px;padding:.55rem .7rem;background:#0b1625}
[data-testid="stMetricLabel"]{font-size:.68rem!important;color:var(--muted)!important}[data-testid="stMetricValue"]{font-size:1.02rem!important;font-weight:850!important}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden}
div[data-testid="stExpander"]{border:1px solid var(--line)!important;border-radius:12px!important;background:#0a1422!important}
.stButton>button{border-radius:10px!important;font-weight:800!important;min-height:2.1rem}
[data-baseweb="select"]>div,[data-testid="stTextInput"] input,[data-testid="stNumberInput"] input{border-radius:10px!important}
</style>
''',unsafe_allow_html=True)
'''
    if '--panel2:#0a1422' not in s:
        anchor="st.set_page_config(page_title='DAY TRADER V5', page_icon='📈', layout='wide', initial_sidebar_state='collapsed')\n"
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: page config')
        s=s.replace(anchor,anchor+css,1)

    # helper for nicer display labels
    if 'def display_name_for_holding(' not in s:
        anchor='def normalize_position(p,live=None):\n'
        helper=r'''def display_name_for_holding(sym,market,live=None,quote=None):
    for src in (live or {}, quote or {}):
        name=str(src.get('name') or src.get('stk_nm') or '').strip()
        if name and name.upper()!=str(sym).upper():
            return name
    # known legacy aliases kept readable until full master lookup is wired
    aliases={
        '379800':'KODEX 미국S&P500',
        '449180':'KODEX 미국S&P500(H)',
        '0193T0':'KODEX SK하이닉스단일종목레버리지',
        '024840':'KBI메탈',
    }
    return aliases.get(str(sym).upper(),str(sym))

'''
        s=s.replace(anchor,helper+anchor,1)

    # registration form wording: search-first UX, code still accepted
    s=s.replace("st.caption('실제 상장 여부를 확인한 종목만 등록됩니다. 국장은 6자리 코드를 Kiwoom으로 직접 검증합니다.')",
                "st.caption('종목명 또는 종목코드로 검색해 등록합니다. 확인된 종목만 장부에 들어갑니다.')")
    s=s.replace("a.text_input('종목명 / 코드',placeholder='삼성전자 / KODEX / 005930 / SOXL'",
                "a.text_input('종목명 / 코드',placeholder='삼성전자 / KODEX 미국S&P500 / 005930 / SOXL'")

    # replace holdings renderer with name-first compact rows
    pat=r'def render_positions\(market,tracker\):.*?\n(?=def render_trading)'
    repl=r'''def render_positions(market,tracker):
    st.markdown('<div class="v12-section-title">🛡 보유주식 관리</div><div class="v12-section-sub">종목명 중심 표시 · 단타/중장기 전환 · 실시간 손익 확인</div>',unsafe_allow_html=True)
    render_manual_holding(market,'holdings')
    pos_rows,_=position_rows(); shown=0
    for raw in pos_rows:
        if str(raw.get('market') or '').upper() not in {'',market}: continue
        sym=raw.get('symbol') or (raw.get('position') or {}).get('symbol') or '-'
        live=next((r for r in tracker if str(r.get('symbol')).upper()==str(sym).upper()),None)
        quote=quote_snapshot(sym,market) if not live else {}
        p=normalize_position(raw,live or quote); shown+=1
        current_type=holding_profile(market,sym)
        name=display_name_for_holding(sym,market,live,quote)
        pct='-' if p['pct'] is None else f"{p['pct']:+.2f}%"
        pnl_cls='v12-good' if (p['pnl'] or 0)>=0 else 'v12-bad'
        if live: judgment=action_ko(action_of(live))
        elif current_type=='LONG_TERM': judgment='중장기 평가대기'
        else: judgment='단타 대기'
        c0,c1,c2,c3,c4,c5,c6,c7=st.columns([1.55,.8,.85,.72,.72,1.05,.9,.5])
        c0.markdown(f'<div class="v12-name">{name}</div><div class="v12-code">{sym} · {p["qty"]:,.0f}주</div>',unsafe_allow_html=True)
        c1.markdown(f'<div class="v12-code">현재가</div><b>{money(p["cur"],market,"-")}</b>',unsafe_allow_html=True)
        c2.markdown(f'<div class="v12-code">평균가</div><b>{money(p["avg"],market,"-")}</b>',unsafe_allow_html=True)
        c3.markdown(f'<div class="v12-code">손익</div><b class="{pnl_cls}">{money(p["pnl"],market,"-")}</b>',unsafe_allow_html=True)
        c4.markdown(f'<div class="v12-code">수익률</div><b class="{pnl_cls}">{pct}</b>',unsafe_allow_html=True)
        chip='v12-chip-blue' if current_type=='SHORT_TERM' else 'v12-chip-purple'
        c5.markdown(f'<span class="v12-chip {chip}">{"단타" if current_type=="SHORT_TERM" else "중장기"}</span><br><b>{judgment}</b>',unsafe_allow_html=True)
        new_label=c6.selectbox('유형',['단타','중장기'],index=0 if current_type=='SHORT_TERM' else 1,key=f'kind_{market}_{sym}',label_visibility='collapsed')
        new_type='SHORT_TERM' if new_label=='단타' else 'LONG_TERM'
        if new_type!=current_type and c6.button('적용',key=f'kind_apply_{market}_{sym}',use_container_width=True):
            rr=set_holding_profile(market,sym,new_type)
            if rr.get('ok'): st.rerun()
            else: st.error(f'구분 변경 실패: {rr}')
        if c7.button('삭제',key=f'del_{market}_{sym}',use_container_width=True):
            close_px=p['avg'] or p['cur'] or 1.0
            rr=post('/api/v4/position/sell',{'market':market,'symbol':sym,'qty':p['qty'],'price':close_px,'note':'V5 manual ledger remove'})
            if rr.get('ok'): st.rerun()
            else: st.error(f"삭제 실패: {rr.get('error') or rr}")
        with st.expander(f'{name} 상세 엔진 평가',expanded=False):
            if live: st.dataframe(engine_matrix(live),hide_index=True,use_container_width=True,height=220)
            else:
                st.info('중장기 평가엔진 연결 대기' if current_type=='LONG_TERM' else 'DAYTRADE 활성화 시 단타 엔진 평가 재개')
                st.dataframe(engine_matrix(None),hide_index=True,use_container_width=True,height=220)
        st.divider()
    if shown==0: st.info('등록된 실제 보유종목이 없습니다.')

'''
    s=replace_once(s,pat,repl,'render_positions')

    # hero title polish, if existing title string present
    s=s.replace("st.markdown('<div class=\"v5-title\">DAY TRADER V5</div><div class=\"v5-sub\">",
                "st.markdown('<div class=\"v12-hero\"><div class=\"v12-bolt\">⚡</div><div><div class=\"v12-title\">DAY TRADER V5</div><div class=\"v12-sub\">")

    APP.write_text(s)
    print('PREOPEN_UI_DASHBOARD_V12_OK')

if __name__=='__main__':
    main()
