#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil, py_compile, re

ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server/v4_engine.py'
APP=ROOT/'app.py'
stamp=datetime.now().strftime('%Y%m%d_%H%M%S')

for f in (ENGINE,APP):
    if not f.exists():
        raise SystemExit(f'PATCH ABORTED missing {f}')

engine=ENGINE.read_text()
app=APP.read_text()

if 'KR_SHADOW_PROTO_V2' not in engine:
    fn_start=engine.find('    def refresh_korea_tracker(self,korea):')
    if fn_start < 0:
        raise SystemExit('PATCH ABORTED: refresh_korea_tracker not found')
    fn_end=engine.find('\n    def ', fn_start+10)
    if fn_end < 0:
        fn_end=len(engine)
    block=engine[fn_start:fn_end]

    rows_pos=block.find('            rows.append({')
    if rows_pos < 0:
        raise SystemExit('PATCH ABORTED: rows.append dict not found in refresh_korea_tracker')

    proto = '''
            # KR_SHADOW_PROTO_V2
            # Prototype-only decision layer. Production state/direction stay unchanged.
            proto_action='WATCH'
            proto_reason='Shadow Gate 조건 대기'
            data_ok=bool(gate.get('data_ok'))
            setup_n=int(_f(gate.get('setup_count')))
            trigger_n=int(_f(gate.get('trigger_count')))
            proto_conf=round(_clip(
                (setup_n/4.0)*35 +
                (trigger_n/4.0)*45 +
                min(abs(power),100)/100.0*20,
                0,100
            ),1)

            has_pos=bool(pmap.get(sym))
            if not data_ok:
                proto_action='DATA_WAIT'
                proto_reason='1분/5분 Shadow Gate 데이터 준비 중'
            elif has_pos:
                if gate_ready and shadow_direction=='SHORT':
                    proto_action='EXIT_REVIEW'
                    proto_reason='보유중 + SHORT Shadow Gate READY · 수동 청산 검토'
                elif gate_ready and shadow_direction=='LONG' and power>=40 and delta>=0:
                    proto_action='ADD_REVIEW'
                    proto_reason='보유중 + LONG Gate READY + Power 유지/상승 · 추매 검토'
                elif shadow_direction=='LONG':
                    proto_action='HOLD'
                    proto_reason='LONG 구조 유지 · 보유 관찰'
                else:
                    proto_action='HOLD_WATCH'
                    proto_reason='보유중 · 방향 확정 전 관찰'
            else:
                if gate_ready and shadow_direction=='LONG':
                    proto_action='BUY_REVIEW'
                    proto_reason='LONG Shadow Gate READY · 수동 진입 검토'
                elif gate_ready and shadow_direction=='SHORT':
                    proto_action='AVOID'
                    proto_reason='SHORT Shadow Gate READY · 신규매수 회피'
                else:
                    proto_action='WATCH'
                    proto_reason='Setup/Trigger 추가 확인 대기'

'''
    block=block[:rows_pos]+proto+block[rows_pos:]

    m=re.search(r"(?m)^(\s*'risk':risk,\s*)$", block)
    if not m:
        raise SystemExit('PATCH ABORTED: KR row risk key not found')
    replacement = m.group(1) + "\n\n                'prototype_engine':'KR_SHADOW_PROTO_V2',\n                'prototype_action':proto_action,\n                'prototype_confidence':proto_conf,\n                'prototype_reason':proto_reason,"
    block=block[:m.start()]+replacement+block[m.end():]
    engine=engine[:fn_start]+block+engine[fn_end:]

if 'KR_PROTOTYPE_DECISION_V2' not in app:
    anchor="    live_now=bool(tr.get('is_live'))"
    pos=app.find(anchor)
    if pos < 0:
        raise SystemExit('PATCH ABORTED: app live_now anchor not found')
    insert_pos=pos+len(anchor)
    panel = '''

    # KR_PROTOTYPE_DECISION_V2
    if m=='KOREA':
        st.markdown('## 🧪 KR Prototype Decision')
        st.caption('SHADOW PROTOTYPE · MANUAL ORDER ONLY · production state/direction unchanged')

        proto_priority={
            'EXIT_REVIEW':0,'BUY_REVIEW':1,'ADD_REVIEW':2,'HOLD':3,
            'HOLD_WATCH':4,'AVOID':5,'WATCH':6,'DATA_WAIT':99,
        }
        proto_rows=sorted(
            rows,
            key=lambda rr:(
                proto_priority.get(str(rr.get('prototype_action') or 'WATCH'),50),
                -f(rr.get('prototype_confidence')),
                -abs(f(rr.get('power')))
            )
        )

        label_map={
            'BUY_REVIEW':'진입 검토','ADD_REVIEW':'추매 검토','HOLD':'보유',
            'HOLD_WATCH':'보유 관찰','EXIT_REVIEW':'청산 검토',
            'AVOID':'신규매수 회피','WATCH':'관찰','DATA_WAIT':'데이터 대기',
        }

        if proto_rows:
            pr=proto_rows[0]
            pa=str(pr.get('prototype_action') or 'WATCH')
            pc=f(pr.get('prototype_confidence'))
            psym=pr.get('symbol') or '-'
            pname=pr.get('name') or psym
            action_ko=label_map.get(pa,pa)

            p1,p2,p3,p4=st.columns(4)
            p1.metric('Prototype 행동',action_ko)
            p2.metric('1순위 종목',psym)
            p3.metric('Prototype 신뢰도',f'{pc:.0f}/100')
            p4.metric('Power',f"{f(pr.get('power')):+.0f}",delta=f"{f(pr.get('power_delta')):+.0f}")

            if pa in ('BUY_REVIEW','ADD_REVIEW'):
                st.success(f"**{pname} ({psym})** · {action_ko}")
            elif pa=='EXIT_REVIEW':
                st.error(f"**{pname} ({psym})** · {action_ko}")
            elif pa=='AVOID':
                st.warning(f"**{pname} ({psym})** · {action_ko}")
            else:
                st.info(f"**{pname} ({psym})** · {action_ko}")

            st.caption(str(pr.get('prototype_reason') or '-'))

            with st.expander('Prototype 전체 Tracker'):
                st.dataframe(
                    pd.DataFrame([{
                        '순위':r.get('tracker_rank'),
                        '종목':r.get('symbol'),
                        '종목명':r.get('name'),
                        '행동':label_map.get(str(r.get('prototype_action') or 'WATCH'),str(r.get('prototype_action') or 'WATCH')),
                        '신뢰도':r.get('prototype_confidence'),
                        'Shadow방향':(r.get('components') or {}).get('shadow_direction'),
                        '5m Setup':(r.get('components') or {}).get('shadow_setup_count'),
                        '1m Trigger':(r.get('components') or {}).get('shadow_trigger_count'),
                        'Power':r.get('power'),
                        'ΔPower':r.get('power_delta'),
                        '사유':r.get('prototype_reason'),
                    } for r in proto_rows]),
                    use_container_width=True,
                    hide_index=True
                )
        else:
            st.info('KR Tracker 후보 준비 중')
'''
    app=app[:insert_pos]+panel+app[insert_pos:]

for f in (ENGINE,APP):
    bak=f.with_name(f.name+f'.pre_kr_proto_v2_{stamp}.bak')
    shutil.copy2(f,bak)
    print('BACKUP',bak)

ENGINE.write_text(engine)
APP.write_text(app)

py_compile.compile(str(ENGINE),doraise=True)
py_compile.compile(str(APP),doraise=True)

e=ENGINE.read_text()
a=APP.read_text()
checks=[
    ('ENGINE_PROTO_MARKER','KR_SHADOW_PROTO_V2' in e),
    ('ENGINE_ACTION_KEY',"'prototype_action':proto_action" in e),
    ('ENGINE_DIRECTION_STILL_BLOCKED',"'direction':'UNVERIFIED'" in e),
    ('APP_PANEL_MARKER','KR_PROTOTYPE_DECISION_V2' in a),
    ('APP_MANUAL_ONLY','MANUAL ORDER ONLY' in a),
]
for name,ok in checks:
    print(name,ok)
    if not ok:
        raise SystemExit(f'POSTCHECK FAILED {name}')

print('PATCH_OK')
print('NO_RESTART_PERFORMED')
