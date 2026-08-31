from pathlib import Path

ROOT=Path('/home/ubuntu/day-trader-api')
api_p=ROOT/'live_server/api.py'
app_p=ROOT/'app_v5.py'

# ---------- API: truthful runtime readiness endpoint ----------
s=api_p.read_text()
if 'V5_AUTO_RUNTIME_STATUS_V1' not in s:
    # keep imports local to endpoint to avoid import-anchor fragility
    block=r'''
# ===== V5_AUTO_RUNTIME_STATUS_V1 =====
@app.get('/api/v5/auto-runtime/{market}')
def v5_auto_runtime_status(market: str):
    """Truthful market automation status.

    RUNNING is deliberately fail-closed: ARMED alone is never enough.
    We only report RUNNING when the switch is on AND runtime evidence says
    live market data + engine + order execution path are all available.
    """
    import subprocess
    import requests as _rq
    from datetime import datetime, timezone

    m=str(market or '').upper()
    if m not in ('KOREA','USA'):
        raise HTTPException(status_code=400, detail='market must be KOREA or USA')

    armed=bool(is_market_auto_enabled(m))
    base='http://127.0.0.1:8000'

    def _get(path, timeout=3):
        try:
            r=_rq.get(base+path, timeout=timeout)
            if r.status_code != 200:
                return {'ok':False,'http_status':r.status_code}
            x=r.json()
            return x if isinstance(x,dict) else {'ok':False,'error':'non-dict response'}
        except Exception as e:
            return {'ok':False,'error':str(e)}

    core=_get(f'/api/v4/{m}/status')
    broker_connected=None
    stream_live=False
    engine_ready=False
    order_path=False
    session_open=False
    pulse='UNKNOWN'
    detail={}

    if m=='KOREA':
        gate=_get('/api/v5/market-gate/KOREA')
        entry=_get('/api/v5/daytrade-entry/KOREA?max_pages=1&max_candidates=1')
        session_open=bool(gate.get('regular_open'))
        pulse=str(gate.get('pulse_status') or 'UNKNOWN').upper()
        stream_live=(pulse=='LIVE')
        engine_ready=bool(entry.get('ok')) and not bool(entry.get('error'))
        # KR route is currently signal-only unless backend explicitly reports placement enabled.
        order_path=bool(entry.get('order_placement')) and not bool(entry.get('signal_only',False))
        # Do NOT fabricate broker connectivity. Use explicit runtime fields only if present.
        for src in (core,gate,entry):
            for key in ('broker_connected','connected','api_connected'):
                if key in src:
                    broker_connected=bool(src.get(key)); break
            if broker_connected is not None: break
        detail={'gate':gate,'entry':{k:entry.get(k) for k in ('ok','signal_only','order_placement','version')}}
    else:
        # US executor is a dedicated service today. Non-active means not actually executing.
        try:
            p=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True,timeout=2)
            svc=(p.stdout or '').strip()
        except Exception as e:
            svc=f'unknown:{e}'
        session=str(core.get('session') or core.get('market_session') or core.get('session_name') or '').upper()
        session_open=session not in ('','CLOSED','OFF','MARKET_CLOSED')
        pulse=str(core.get('pulse_status') or core.get('stream_status') or core.get('status') or 'UNKNOWN').upper()
        # explicit fields win; otherwise do not call stale/unknown data LIVE
        live_flag=core.get('streaming_live')
        if live_flag is None: live_flag=core.get('live')
        stream_live=bool(live_flag) if live_flag is not None else pulse in ('LIVE','OPEN','CONNECTED','STREAMING')
        engine_ready=(svc=='active')
        order_path=(svc=='active')
        for key in ('broker_connected','connected','api_connected'):
            if key in core:
                broker_connected=bool(core.get(key)); break
        detail={'executor_service':svc}

    checks={
        'armed':armed,
        'broker_connected':broker_connected,
        'stream_live':bool(stream_live),
        'engine_ready':bool(engine_ready),
        'order_path':bool(order_path),
        'session_open':bool(session_open),
    }
    # Broker connectivity must be explicitly TRUE; unknown is not accepted as running.
    running=all((armed, broker_connected is True, stream_live, engine_ready, order_path, session_open))

    if not armed:
        state='OFF'; reason='AUTO_SWITCH_OFF'
    elif running:
        state='RUNNING'; reason='ALL_RUNTIME_CHECKS_OK'
    elif not session_open:
        state='ARMED_WAITING'; reason='MARKET_NOT_OPEN'
    else:
        state='NOT_READY'; reason='RUNTIME_CHECK_FAILED'

    return {
        'ok':True,'market':m,'state':state,'running':running,'reason':reason,
        'checks':checks,'pulse_status':pulse,'core_status':core,
        'detail':detail,'checked_at':datetime.now(timezone.utc).isoformat()
    }
# ===== /V5_AUTO_RUNTIME_STATUS_V1 =====

'''
    idx=s.find('@app.')
    if idx<0: raise SystemExit('API_ROUTE_ANCHOR_NOT_FOUND')
    s=s[:idx]+block+s[idx:]
    api_p.write_text(s)

# ---------- UI: replace misleading ARMED sentence with truth panel ----------
s=app_p.read_text()
old_start='# ===== V5_MARKET_AUTO_SWITCH_UI_V1 ====='
old_end='# ===== /V5_MARKET_AUTO_SWITCH_UI_V1 ====='
if 'V5_AUTO_RUNTIME_PANEL_V1' not in s:
    a=s.find(old_start); b=s.find(old_end)
    if a<0 or b<0: raise SystemExit('AUTO_SWITCH_UI_BLOCK_NOT_FOUND')
    b += len(old_end)
    new=r'''# ===== V5_MARKET_AUTO_SWITCH_UI_V1 =====
auto_state=api(f'/api/v5/auto-switch/{market}',5)
auto_on=bool(auto_state.get('enabled'))
runtime_truth=api(f'/api/v5/auto-runtime/{market}',5)
rt_state=str(runtime_truth.get('state') or ('ARMED_WAITING' if auto_on else 'OFF'))
checks=runtime_truth.get('checks') or {}

as1,as2,as3=st.columns([1.15,1.15,4.7])
if as1.button('🟢 자동매매 ON',use_container_width=True,type='primary' if auto_on else 'secondary',key=f'auto_on_{market}'):
    rr=post(f'/api/v5/auto-switch/{market}/ON',{},5)
    if rr.get('ok'): st.rerun()
    else: st.error(f'자동매매 ON 실패: {rr}')
if as2.button('🔴 긴급 OFF',use_container_width=True,type='primary' if not auto_on else 'secondary',key=f'auto_off_{market}'):
    rr=post(f'/api/v5/auto-switch/{market}/OFF',{},5)
    if rr.get('ok'): st.rerun()
    else: st.error(f'긴급 OFF 실패: {rr}')

# V5_AUTO_RUNTIME_PANEL_V1 -- never equate ARMED with actual execution.
def _dot(v):
    if v is True:return '🟢'
    if v is False:return '🔴'
    return '⚪'
if rt_state=='RUNNING':
    headline='🟢 AUTO RUNNING · 실제 실행 조건 확인됨'
elif rt_state=='ARMED_WAITING':
    headline='🟠 ARMED / WAITING · 자동매매 ON, 실행 대기'
elif rt_state=='NOT_READY':
    headline='🟡 AUTO NOT READY · 자동매매 ON, 실행 조건 미충족'
else:
    headline='⚫ AUTO OFF · 신규 진입/주문 차단'

broker_txt='확인불가' if checks.get('broker_connected') is None else ('연결' if checks.get('broker_connected') else '미연결')
status_line=(
    f"{_dot(checks.get('broker_connected'))} 브로커 {broker_txt} · "
    f"{_dot(checks.get('stream_live'))} 실시간시세 · "
    f"{_dot(checks.get('engine_ready'))} 엔진 · "
    f"{_dot(checks.get('order_path'))} 주문경로 · "
    f"{_dot(checks.get('session_open'))} 장상태"
)
as3.markdown(f"**{headline}**  \n{status_line}")
as3.caption(f"상태근거: {runtime_truth.get('reason','-')} · Pulse {runtime_truth.get('pulse_status','-')} · OFF 시 시세 연결은 유지하고 신규 주문만 차단")
# ===== /V5_MARKET_AUTO_SWITCH_UI_V1 ====='''
    s=s[:a]+new+s[b:]
    app_p.write_text(s)

print('V5_RUNTIME_TRUTH_PANEL_PATCHED')
