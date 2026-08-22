#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil, py_compile, re

ROOT=Path('/home/ubuntu/day-trader-api')
F=ROOT/'live_server/kakao_live_alert.py'
stamp=datetime.now().strftime('%Y%m%d_%H%M%S')

if not F.exists():
    raise SystemExit('PATCH ABORTED: kakao_live_alert.py missing')

src=F.read_text()

if 'KR_PROTO_ALERT_V1' in src:
    print('ALREADY_PATCHED')
else:
    old='API = "http://127.0.0.1:8000/api/v4/USA/status"'
    if old not in src:
        raise SystemExit('PATCH ABORTED: USA API anchor not found')
    src=src.replace(
        old,
        old + '\nKOREA_API = "http://127.0.0.1:8000/api/v4/KOREA/status"',
        1
    )

    anchor='\ndef grade_of(row):'
    if anchor not in src:
        raise SystemExit('PATCH ABORTED: grade_of anchor not found')

    helpers = '''
# KR_PROTO_ALERT_V1
KR_PROTO_TARGETS = {
    "BUY_REVIEW": 1,
    "ADD_REVIEW": 2,
    "EXIT_REVIEW": 3,
}

def get_korea_rows():
    r = requests.get(KOREA_API, timeout=10)
    r.raise_for_status()
    d = r.json()
    session = str(d.get("session") or "").upper()
    rows = (d.get("tracker") or {}).get("rows") or []
    return session, rows

def kr_action_of(row):
    return str(row.get("prototype_action") or "WATCH").upper()

def valid_kr_alert(row):
    action = kr_action_of(row)
    if action not in KR_PROTO_TARGETS:
        return False
    if str(row.get("prototype_engine") or "") != "KR_SHADOW_PROTO_V2":
        return False
    return True

def make_kr_text(row):
    action = kr_action_of(row)
    action_ko = {
        "BUY_REVIEW": "진입 검토",
        "ADD_REVIEW": "추매 검토",
        "EXIT_REVIEW": "청산 검토",
    }.get(action, action)

    symbol = str(row.get("symbol") or "-")
    name = str(row.get("name") or symbol)
    price = float(row.get("price") or 0)
    power = float(row.get("power") or 0)
    delta = float(row.get("power_delta") or 0)
    conf = float(row.get("prototype_confidence") or 0)
    comp = row.get("components") or {}

    return (
        f"[KR Prototype] {action_ko}\n"
        f"{name} ({symbol})\n"
        f"현재가 {price:,.0f}원\n"
        f"신뢰도 {conf:.0f}/100\n"
        f"Shadow {comp.get('shadow_direction') or '-'} · "
        f"5m Setup {comp.get('shadow_setup_count')}/4 · "
        f"1m Trigger {comp.get('shadow_trigger_count')}/4\n"
        f"Power {power:+.0f} (Δ {delta:+.0f})\n"
        f"{row.get('prototype_reason') or '-'}\n"
        f"※ Shadow Prototype / 수동주문 전용"
    )

'''
    src=src.replace(anchor, '\n'+helpers+anchor, 1)

    pat=r"(def scheduler\(\):\s*\n\s*last_grade\s*=\s*\{\}\s*\n\s*last_sent\s*=\s*\{\})"
    m=re.search(pat, src)
    if not m:
        raise SystemExit('PATCH ABORTED: scheduler state anchor not found')
    repl=m.group(1) + '\n    last_kr_action = {}\n    last_kr_sent = {}'
    src=src[:m.start()] + repl + src[m.end():]

    sched_start=src.find('def scheduler():')
    if sched_start < 0:
        raise SystemExit('PATCH ABORTED: scheduler not found')
    sched_end=src.find('\n\nif __name__', sched_start)
    if sched_end < 0:
        sched_end=len(src)
    block=src[sched_start:sched_end]

    except_pos=block.find('\n        except Exception as e:')
    if except_pos < 0:
        raise SystemExit('PATCH ABORTED: scheduler except anchor not found')

    kr_loop = '''

            # KR_PROTO_ALERT_V1
            # Reuse existing Kakao sender/service. KR regular session only.
            kr_session, kr_rows = get_korea_rows()
            if kr_session == "REGULAR":
                kr_symbols = set()

                for row in kr_rows:
                    symbol = str(row.get("symbol") or "").upper()
                    if not symbol:
                        continue

                    kr_symbols.add(symbol)
                    action = kr_action_of(row)
                    prev_action = last_kr_action.get(symbol)

                    if valid_kr_alert(row):
                        key = (symbol, action)
                        last_time = last_kr_sent.get(key, 0)

                        transitioned = prev_action != action
                        cooldown_ok = (now_ts - last_time) >= COOLDOWN_SEC

                        if transitioned and cooldown_ok:
                            text = make_kr_text(row)
                            send_text(text)
                            last_kr_sent[key] = now_ts
                            print("KR PROTOTYPE ALERT SENT", symbol, action, flush=True)

                    last_kr_action[symbol] = action

                for symbol in list(last_kr_action):
                    if symbol not in kr_symbols:
                        last_kr_action.pop(symbol, None)

'''
    block=block[:except_pos]+kr_loop+block[except_pos:]
    src=src[:sched_start]+block+src[sched_end:]

bak=F.with_name(F.name+f'.pre_kr_proto_alert_{stamp}.bak')
shutil.copy2(F,bak)
F.write_text(src)

py_compile.compile(str(F), doraise=True)

ns={}
exec(compile(src, str(F), 'exec'), ns)
buy={
    'prototype_engine':'KR_SHADOW_PROTO_V2',
    'prototype_action':'BUY_REVIEW',
    'symbol':'005930','name':'삼성전자','price':90000,
    'prototype_confidence':88,'power':72,'power_delta':8,
    'prototype_reason':'LONG Shadow Gate READY · 수동 진입 검토',
    'components':{'shadow_direction':'LONG','shadow_setup_count':4,'shadow_trigger_count':4},
}
watch={
    'prototype_engine':'KR_SHADOW_PROTO_V2',
    'prototype_action':'WATCH',
    'symbol':'005930','name':'삼성전자',
}

print('BACKUP',bak)
print('SYNTAX_OK True')
print('KR_VALID_BUY',ns['valid_kr_alert'](buy))
print('KR_VALID_WATCH',ns['valid_kr_alert'](watch))
print('KR_TEXT_OK','[KR Prototype]' in ns['make_kr_text'](buy))
print('USA_API_PRESERVED',ns.get('API')=='http://127.0.0.1:8000/api/v4/USA/status')
print('KOREA_API_ADDED',ns.get('KOREA_API')=='http://127.0.0.1:8000/api/v4/KOREA/status')
print('PATCH_OK')
print('NEXT restart day-trader-live-alert.service only')
