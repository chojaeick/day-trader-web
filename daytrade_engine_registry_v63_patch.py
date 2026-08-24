from pathlib import Path
import re

API=Path('live_server/api.py')
APP=Path('app_v5.py')

API_BLOCK=r'''

# ===== DAYTRADE ENGINE REGISTRY V63 =====
from pathlib import Path as _DTPath
import json as _dtjson

_DAYTRADE_ENGINE_STATE_FILE=_DTPath('/home/ubuntu/day-trader-api/.daytrade_core_engine.json')
_DAYTRADE_ENGINE_REGISTRY=[
    {'id':'momentum','name':'모멘텀','legacy_name':'Core','role':'주도주/자금집중 모멘텀','selectable':True,'status':'ACTIVE'},
    {'id':'fujimoto','name':'후지모토','legacy_name':'Fujimoto','role':'RSI+MACD 모멘텀 전환','selectable':True,'status':'ACTIVE'},
    {'id':'ma20','name':'20이평선','legacy_name':'MA20','role':'20MA 추세/눌림 재상승','selectable':True,'status':'ACTIVE'},
    {'id':'ethan','name':'Ethan','legacy_name':'Ethan','role':'과매도/V-zone 반전','selectable':True,'status':'ACTIVE'},
    {'id':'jared','name':'Jared','legacy_name':'Jared 3/4','role':'압축 후 구조적 돌파','selectable':True,'status':'ACTIVE'},
    {'id':'predator','name':'프리데터','legacy_name':'Predator','role':'거래량/가격/수급 가속','selectable':True,'status':'ACTIVE'},
    {'id':'hayaki','name':'하이아키','legacy_name':'Hayaki','role':'사용자 정의 예정','selectable':False,'status':'DEFINITION_PENDING'},
]

def _daytrade_load_core_engine():
    try:
        d=_dtjson.loads(_DAYTRADE_ENGINE_STATE_FILE.read_text())
        eid=str(d.get('engine') or '').strip().lower()
        if any(x['id']==eid and x.get('selectable') for x in _DAYTRADE_ENGINE_REGISTRY):
            return eid
    except Exception:
        pass
    return 'momentum'

def _daytrade_save_core_engine(engine):
    _DAYTRADE_ENGINE_STATE_FILE.write_text(_dtjson.dumps({'engine':engine},ensure_ascii=False))

@app.get('/api/v5/daytrade-engine-registry/KOREA')
async def v5_daytrade_engine_registry_korea():
    selected=_daytrade_load_core_engine()
    return {
        'ok':True,
        'version':'DAYTRADE_ENGINE_REGISTRY_V63',
        'selected_core_engine':selected,
        'engines':[dict(x,selected=(x['id']==selected)) for x in _DAYTRADE_ENGINE_REGISTRY],
        'evaluation_policy':'Finder-selected symbols are evaluated by every connected engine; selected core engine supplies the primary daytrade decision.',
        'order_placement':False,
    }

@app.post('/api/v5/daytrade-engine-core/KOREA')
async def v5_daytrade_engine_core_korea(engine:str):
    eid=str(engine or '').strip().lower()
    row=next((x for x in _DAYTRADE_ENGINE_REGISTRY if x['id']==eid),None)
    if not row:
        return {'ok':False,'error':'UNKNOWN_ENGINE','engine':eid}
    if not row.get('selectable'):
        return {'ok':False,'error':'ENGINE_NOT_READY','engine':eid,'status':row.get('status')}
    _daytrade_save_core_engine(eid)
    return {'ok':True,'selected_core_engine':eid,'name':row.get('name'),'order_placement':False}
'''

APP_HELPERS=r'''

# ===== UI DAYTRADE ENGINE REGISTRY V63 =====
def daytrade_engine_registry_ui():
    try:
        r=requests.get(f'{API_BASE}/api/v5/daytrade-engine-registry/KOREA',timeout=3)
        return r.json() if r.ok else {}
    except Exception:
        return {}

def daytrade_set_core_engine_ui(engine_id):
    try:
        r=requests.post(f'{API_BASE}/api/v5/daytrade-engine-core/KOREA',params={'engine':engine_id},timeout=3)
        return r.json() if r.ok else {}
    except Exception:
        return {}

def daytrade_engine_name_ko(name):
    return {
        'Core':'모멘텀','Momentum':'모멘텀',
        'Fujimoto':'후지모토',
        'MA20':'20이평선',
        'Jared 3/4':'Jared','Jared':'Jared',
        'Predator':'프리데터',
        'Hayaki':'하이아키',
    }.get(str(name),str(name))
'''

UI_BLOCK=r'''
    # ===== DAYTRADE CORE ENGINE SELECTOR V63 =====
    if market=='KOREA':
        _ereg=daytrade_engine_registry_ui()
        _engines=_ereg.get('engines') or []
        _selectable=[x for x in _engines if x.get('selectable')]
        _ids=[x.get('id') for x in _selectable]
        _names={x.get('id'):x.get('name') for x in _selectable}
        _current=_ereg.get('selected_core_engine') or 'momentum'
        _idx=_ids.index(_current) if _current in _ids else 0
        if _ids:
            _chosen=st.selectbox('⚙️ 단타 코어 엔진',_ids,index=_idx,format_func=lambda x:_names.get(x,x),key='daytrade_core_engine_v63')
            if _chosen!=_current:
                _set=daytrade_set_core_engine_ui(_chosen)
                if _set.get('ok'):
                    st.success(f"코어 엔진 변경: {_names.get(_chosen,_chosen)}")
                    st.rerun()
        _pending=[x.get('name') for x in _engines if not x.get('selectable')]
        if _pending:
            st.caption('전체 평가 엔진: '+ ' / '.join([x.get('name') for x in _engines]) + ' · 정의대기: ' + ', '.join(_pending))
'''


def main():
    a=API.read_text()
    if 'DAYTRADE ENGINE REGISTRY V63' not in a:
        a += API_BLOCK
        API.write_text(a)

    p=APP.read_text()
    if not p.strip():
        raise SystemExit('PATCH_TARGET_EMPTY: app_v5.py runtime copy required')

    if 'UI DAYTRADE ENGINE REGISTRY V63' not in p:
        # helpers must live before render_trading; insert immediately before it.
        anchor='def render_trading(market):\n'
        if anchor not in p:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: render_trading')
        p=p.replace(anchor,APP_HELPERS+'\n'+anchor,1)

    # Rename legacy engine display labels without touching Fujimoto Swing.
    replacements={
        "'엔진':'Core'":"'엔진':'모멘텀'",
        "'엔진':'Fujimoto'":"'엔진':'후지모토'",
        "'엔진':'MA20'":"'엔진':'20이평선'",
        "'엔진':'Jared 3/4'":"'엔진':'Jared'",
        "'엔진':'Predator'":"'엔진':'프리데터'",
    }
    for old,new in replacements.items():
        p=p.replace(old,new)

    if 'DAYTRADE CORE ENGINE SELECTOR V63' not in p:
        # Insert selector after the four market metrics, at render_trading indentation.
        pat=re.compile(r"(\n\s{4}d\.metric\('관리',managed\)\n)")
        m=pat.search(p)
        if not m:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: managed metric')
        p=p[:m.end()]+"\n"+UI_BLOCK+p[m.end():]

    APP.write_text(p)
    print('DAYTRADE_ENGINE_REGISTRY_V63_PATCH_OK')

if __name__=='__main__':
    main()
