#!/usr/bin/env python3
"""
DAY TRADER V4 — KR PROTOTYPE DECISION V1 PATCH

Goal:
Monday KR prototype without disturbing production state logic.

What it changes:
1) live_server/v4_engine.py
   - Adds prototype_action / prototype_confidence / prototype_reason
     to each KR Tracker row.
   - DOES NOT change authoritative state/direction.
   - DOES NOT place orders.

2) app.py
   - Adds KR Prototype Decision panel to Trading tab.
   - Shows BUY REVIEW / ADD REVIEW / HOLD / EXIT REVIEW / AVOID / WATCH.
   - Explicit SHADOW / MANUAL ORDER ONLY label.

No service restart is performed.
Creates timestamped backups and syntax-checks both files.
"""

from pathlib import Path
from datetime import datetime
import shutil, py_compile, sys

ROOT=Path("/home/ubuntu/day-trader-api")
ENGINE=ROOT/"live_server/v4_engine.py"
APP=ROOT/"app.py"

stamp=datetime.now().strftime("%Y%m%d_%H%M%S")

for f in (ENGINE,APP):
    if not f.exists():
        raise SystemExit(f"PATCH ABORTED missing {f}")

engine=ENGINE.read_text()
app=APP.read_text()

if "KR_SHADOW_PROTO_V1" in engine:
    print("ENGINE_ALREADY_PATCHED")
else:
    anchor="""            reason='KR 5m Setup≥3/4 + 1m Trigger=4/4 Shadow Gate · 라이브 방향 미검증'
"""
    if anchor not in engine:
        raise SystemExit("PATCH ABORTED: KR reason anchor not found")

    insert="""            # KR_SHADOW_PROTO_V1
            # Prototype-only decision layer.
            # Authoritative production state/direction remain unchanged.
            proto_action='WATCH'
            proto_reason='Shadow Gate 조건 대기'
            proto_conf=0.0

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

"""
    engine=engine.replace(anchor,insert+anchor,1)

    row_anchor="""                'state':state,
                'risk':risk,
"""
    row_insert="""                'state':state,
                'risk':risk,

                # KR_SHADOW_PROTO_V1: display/recommendation only.
                'prototype_engine':'KR_SHADOW_PROTO_V1',
                'prototype_action':proto_action,
                'prototype_confidence':proto_conf,
                'prototype_reason':proto_reason,
"""
    if row_anchor not in engine:
        raise SystemExit("PATCH ABORTED: KR row state anchor not found")
    engine=engine.replace(row_anchor,row_insert,1)

if "KR_PROTOTYPE_DECISION_V1" in app:
    print("APP_ALREADY_PATCHED")
else:
    anchor="""    if m=='KOREA':st.info('국장은 현재 체결강도 기반 Power까지만 사용합니다. 검증된 1분/5분봉 Gate 연결 전에는 ENTRY 신호를 내지 않습니다.')
"""
    if anchor not in app:
        raise SystemExit("PATCH ABORTED: app KR info anchor not found")

    insert=r"""
    # KR_PROTOTYPE_DECISION_V1
    if m=='KOREA':
        st.markdown('## 🧪 KR Prototype Decision')
        st.caption('SHADOW PROTOTYPE · MANUAL ORDER ONLY · production state/direction unchanged')

        proto_priority={
            'EXIT_REVIEW':0,
            'BUY_REVIEW':1,
            'ADD_REVIEW':2,
            'HOLD':3,
            'HOLD_WATCH':4,
            'AVOID':5,
            'WATCH':6,
            'DATA_WAIT':99,
        }

        proto_rows=sorted(
            rows,
            key=lambda rr:(
                proto_priority.get(str(rr.get('prototype_action') or 'WATCH'),50),
                -f(rr.get('prototype_confidence')),
                -abs(f(rr.get('power')))
            )
        )

        if proto_rows:
            pr=proto_rows[0]
            pa=str(pr.get('prototype_action') or 'WATCH')
            pc=f(pr.get('prototype_confidence'))
            psym=pr.get('symbol') or '-'
            pname=pr.get('name') or psym

            action_ko={
                'BUY_REVIEW':'진입 검토',
                'ADD_REVIEW':'추매 검토',
                'HOLD':'보유',
                'HOLD_WATCH':'보유 관찰',
                'EXIT_REVIEW':'청산 검토',
                'AVOID':'신규매수 회피',
                'WATCH':'관찰',
                'DATA_WAIT':'데이터 대기',
            }.get(pa,pa)

            p1,p2,p3,p4=st.columns(4)
            p1.metric('Prototype 행동',action_ko)
            p2.metric('1순위 종목',psym)
            p3.metric('Prototype 신뢰도',f'{pc:.0f}/100')
            p4.metric('Power',f"{f(pr.get('power')):+.0f}",
                      delta=f"{f(pr.get('power_delta')):+.0f}")

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
                        '행동':action_ko if r is pr else {
                            'BUY_REVIEW':'진입 검토',
                            'ADD_REVIEW':'추매 검토',
                            'HOLD':'보유',
                            'HOLD_WATCH':'보유 관찰',
                            'EXIT_REVIEW':'청산 검토',
                            'AVOID':'신규매수 회피',
                            'WATCH':'관찰',
                            'DATA_WAIT':'데이터 대기',
                        }.get(str(r.get('prototype_action') or 'WATCH'),
                              str(r.get('prototype_action') or 'WATCH')),
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

"""
    app=app.replace(anchor,anchor+insert,1)

# backups
for f in (ENGINE,APP):
    bak=f.with_name(f.name+f".pre_kr_proto_v1_{stamp}.bak")
    shutil.copy2(f,bak)
    print("BACKUP",bak)

ENGINE.write_text(engine)
APP.write_text(app)

# syntax validation
py_compile.compile(str(ENGINE),doraise=True)
py_compile.compile(str(APP),doraise=True)

print("PATCH_OK")
print("ENGINE",ENGINE)
print("APP",APP)
print("NO_RESTART_PERFORMED")
print("NEXT: inspect diff, then restart only after approval.")
