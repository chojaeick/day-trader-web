from pathlib import Path
import re

APP = Path('app_v5.py')


def replace_between(src, start_pat, end_pat, replacement, label):
    m = re.search(start_pat + r'.*?(?=' + end_pat + r')', src, re.S)
    if not m:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return src[:m.start()] + replacement + src[m.end():]


def main():
    s = APP.read_text()

    if not re.search(r'^import html\s*$', s, re.M):
        s = s.replace('import os\n', 'import os\nimport html\n', 1)

    render_trading = r'''def render_trading(market):
    status=get_market_status(market)
    rows=tracker_rows(status)
    finders=finder_rows(status)
    active=rows if rows else finders
    session=status.get('session') or status.get('market_session') or '-'
    standby=standby_candidates(status,market,5) if not active else []
    source=(active or standby)[:5]

    pos_rows,_=position_rows()
    managed=sum(1 for p in pos_rows if str(p.get('market') or '').upper() in {'',market})

    a,b,c,d=st.columns(4)
    a.metric('시장','미국장' if market=='USA' else '국장')
    b.metric('세션',session)
    c.metric('후보',len(source) if source else 0)
    d.metric('관리',managed)

    left,right=st.columns([1.0,1.3],gap='medium')
    selected=None
    with left:
        title='⚡ 지금 단타 후보 TOP 5' if active else '🕘 최근 단타 후보 TOP 5'
        sub='후보를 선택하면 오른쪽에 상세 평가가 표시됩니다.' if active else 'NORMAL/CLOSED 상태 · 마지막 TOP5 기록 참고용'
        badge='● LIVE' if active else '● STANDBY'
        st.markdown(f'<div class="v23-badge">{badge}</div><div class="v23-title">{title}</div><div class="v23-sub">{sub}</div>',unsafe_allow_html=True)

        if source:
            head='<div class="v23-grid v23-grid-head"><div>종목명</div><div>코드</div><div>현재가</div><div>Power</div><div>판단</div></div>'
            body=[]
            labels=[]
            lookup={}
            for r in source:
                sym=str(r.get('symbol') or '-').upper()
                name=resolve_display_name(market,sym,r.get('name') or '')
                price=money(r.get('price') or r.get('current_price'),market)
                pv=r.get('power'); ptxt='-' if pv is None else f'{f(pv):+.1f}'
                act=action_ko(action_of(r))
                cls=' v23-positive' if pv is not None and f(pv)>=0 else (' v23-negative' if pv is not None else '')
                body.append(
                    '<div class="v23-grid v23-grid-row">'
                    f'<div class="v23-stock">{html.escape(str(name))}</div>'
                    f'<div class="v23-code">{html.escape(sym)}</div>'
                    f'<div>{html.escape(str(price))}</div>'
                    f'<div class="{cls.strip()}">{html.escape(ptxt)}</div>'
                    f'<div>{html.escape(str(act))}</div>'
                    '</div>'
                )
                label=f'{name} · {sym} · {act} · Power {ptxt}'
                labels.append(label);lookup[label]=r
            st.markdown('<div class="v23-table">'+head+''.join(body)+'</div>',unsafe_allow_html=True)
            sel=st.selectbox('후보 선택',labels,key=f'sel23_{market}',label_visibility='collapsed')
            selected=lookup[sel]
        else:
            st.info('현재 후보 데이터가 없습니다. DAYTRADE를 켜거나 장 시작 후 갱신을 기다려주세요.')

    with right:
        if selected:
            render_selected_detail(selected,market)
        else:
            st.markdown('<div class="v5-section"><b>선택 종목 없음</b><div class="v5-section-sub">후보가 생성되면 상세 평가가 표시됩니다.</div></div>',unsafe_allow_html=True)

    st.divider()
    render_positions(market,rows)

'''
    s = replace_between(s, r'def render_trading\(market\):\n', r'def render_portfolio\(market\):', render_trading, 'render_trading')
    s = re.sub(r'^runtime_mode_bar\(\)\s*\n', '', s, count=1, flags=re.M)

    anchor = "if 'v5_market' not in st.session_state:\n"
    if anchor not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: market state anchor')
    idx=s.index(anchor)
    prefix=s[:idx]
    suffix=s[idx:]
    tail_start=max(0,len(prefix)-2200)
    head=prefix[:tail_start]
    tail=prefix[tail_start:]
    tail=re.sub(r"(?m)^st\.title\([^\n]*DAY TRADER V5[^\n]*\)\s*\n?",'',tail)
    tail=re.sub(r"(?m)^st\.caption\([^\n]*DECISION TERMINAL[^\n]*\)\s*\n?",'',tail)
    tail=re.sub(r"(?ms)^st\.markdown\((?:f|r)?'''[^']*DAY TRADER V5.*?'''[^\n]*\)\s*\n?",'',tail)
    tail=re.sub(r'(?ms)^st\.markdown\((?:f|r)?""".*?DAY TRADER V5.*?"""[^\n]*\)\s*\n?','',tail)
    header="st.markdown('<div class=\"v23-header\"><span class=\"v23-bolt\">⚡</span><span>DAY TRADER V5</span><span class=\"v23-ver\">v23</span></div><div class=\"v23-tagline\">DECISION TERMINAL · MANUAL ORDER · 실시간 연결 유지 · 단타 분석은 필요할 때 가속</div>',unsafe_allow_html=True)\n"
    s=head+tail+header+suffix

    css = """st.markdown(r'''\n<style>\n/* ===== V23 FORCED REBUILD ===== */\n.block-container{padding-top:1.15rem!important;padding-left:1rem!important;padding-right:1rem!important;max-width:1560px!important}\n.v23-header{display:flex;align-items:baseline;gap:.55rem;font-size:2rem;font-weight:950;letter-spacing:-.045em;line-height:1.15;margin:.15rem 0 .05rem;color:#f4f8ff}\n.v23-bolt{color:#ffc21c;font-size:1.8rem}.v23-ver{font-size:.65rem;color:#4d9cff;font-weight:850;letter-spacing:0}\n.v23-tagline{color:#8797ac;font-size:.72rem;margin-bottom:.42rem}\n.v23-badge{font-size:.67rem;color:#7d90a8;font-weight:800;margin-bottom:.05rem}\n.v23-title{font-size:1.22rem;font-weight:900;letter-spacing:-.025em;margin-bottom:.08rem}.v23-sub{font-size:.67rem;color:#788ba3;margin-bottom:.35rem}\n.v23-table{border:1px solid #203a59;border-radius:11px;overflow:hidden;background:#09121d;margin-bottom:.35rem}\n.v23-grid{display:grid;grid-template-columns:minmax(150px,2.2fr) minmax(68px,.7fr) minmax(88px,.9fr) minmax(65px,.68fr) minmax(72px,.72fr);align-items:center;column-gap:8px;padding:0 10px}\n.v23-grid-head{height:34px;background:#121a27;color:#8ea0b7;font-size:.67rem;font-weight:800;border-bottom:1px solid #2a3443}\n.v23-grid-row{min-height:38px;font-size:.78rem;font-weight:700;border-bottom:1px solid #222c39}.v23-grid-row:last-child{border-bottom:0}\n.v23-grid-row>div{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.v23-stock{font-weight:850;color:#f3f7ff}.v23-code{color:#a8b6c8;font-size:.72rem}.v23-positive{color:#20d87a}.v23-negative{color:#ff5267}\n[data-testid=\"stDataFrame\"]{max-width:100%!important}\n[data-testid=\"stExpander\"] summary{min-height:2rem!important}\n[data-testid=\"stHorizontalBlock\"]{gap:.55rem!important}\nhr{margin:.38rem 0!important}\n@media(max-width:1050px){.v23-grid{grid-template-columns:1.8fr .72fr .88fr .65fr .72fr}.v23-header{font-size:1.75rem}}\n</style>\n''',unsafe_allow_html=True)\n"""
    if 'V23 FORCED REBUILD' not in s:
        api_anchor='def api(path, timeout=10):\n'
        if api_anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        s=s.replace(api_anchor,css+'\n'+api_anchor,1)

    APP.write_text(s)
    print('PREOPEN_UI_FORCE_REBUILD_V23_OK')


if __name__=='__main__':
    main()
