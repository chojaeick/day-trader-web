#!/usr/bin/env python3
"""V138: build a clean frozen-USA Williams paper replication module.

Does NOT patch legacy Korea Williams mock logic. It creates an isolated USA evaluator
matching the V136 frozen spec, so Korea's 5-minute hold / 1.5% stop / structure exit
cannot contaminate the USA paper path.
"""
from pathlib import Path

OUT=Path('/home/ubuntu/day-trader-api/live_server/williams_usa_frozen.py')
CODE=r'''from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re

ET=ZoneInfo("America/New_York")

@dataclass(frozen=True)
class FrozenWilliamsSpec:
    volume_ratio: float=1.5
    cci_min: float=100.0
    hard_stop_pct: float=-1.0
    combo_bars: int=2
    start_minute: int=9*60+30
    end_minute: int=11*60

SPEC=FrozenWilliamsSpec()

def _parse_dt(ts):
    if isinstance(ts, datetime):
        x=ts
    else:
        s=str(ts).strip()
        if s.endswith('Z'):
            s=s[:-1]+'+00:00'
        try:
            x=datetime.fromisoformat(s)
        except Exception:
            digits=''.join(ch for ch in s if ch.isdigit())
            if len(digits)>=14:
                x=datetime.strptime(digits[:14],'%Y%m%d%H%M%S')
            elif len(digits)>=12:
                x=datetime.strptime(digits[:12],'%Y%m%d%H%M')
            else:
                m=re.search(r'(\d{1,2}):(\d{2})',s)
                if not m:
                    raise ValueError(f'unsupported timestamp: {ts!r}')
                return None,int(m.group(1))*60+int(m.group(2))
    return x,None

def _et_minute(ts):
    x,minute=_parse_dt(ts)
    if minute is not None:
        return minute
    if x.tzinfo is None:
        # historical DB et_time strings are already ET-local unless an offset is present
        return int(x.hour)*60+int(x.minute)
    x=x.astimezone(ET)
    return int(x.hour)*60+int(x.minute)

def entry_signal(*, ts, prev_crossed, cross_now, rsi2, day_open, prev_high, prev_low,
                 volume, prior10_volume_avg, cci20, macd_hist, prev_macd_hist):
    trigger=float(day_open)+0.5*(float(prev_high)-float(prev_low))
    vol_ratio=(float(volume)/float(prior10_volume_avg)) if prior10_volume_avg else 0.0
    m=_et_minute(ts)
    ok=bool(
        (not bool(prev_crossed)) and bool(cross_now)
        and float(rsi2)>50.0
        and SPEC.start_minute<=m<=SPEC.end_minute
        and vol_ratio>=SPEC.volume_ratio
        and float(cci20)>SPEC.cci_min
        and float(macd_hist)>float(prev_macd_hist)
    )
    return {'signal':ok,'trigger':trigger,'volume_ratio':vol_ratio,'et_minute':m}

def exit_signal(*, entry_price, price, macd, signal, cci20, prev_cci20,
                prev_macd=None, prev_signal=None, weak_run=0):
    pnl=(float(price)/float(entry_price)-1.0)*100.0 if entry_price else 0.0
    hard=bool(pnl<=SPEC.hard_stop_pct)
    weak=bool(float(macd)<float(signal) and float(cci20)<float(prev_cci20))
    run=(int(weak_run)+1) if weak else 0
    combo=bool(run>=SPEC.combo_bars)
    return {'exit':bool(hard or combo),'hard_stop':hard,'combo_two_bar':combo,
            'weak_run':run,'pnl_pct':pnl,'forced_min_hold':False}
'''
OUT.write_text(CODE)
print('=== V138 FROZEN USA REPLICATION BUILDER ===')
print('WROTE',OUT)
print('STDLIB_ONLY=YES')
print('ISOLATED_FROM_KOREA_MOCK=YES')
print('FORCED_MIN_HOLD=NONE')
print('HARD_STOP=-1.0%')
print('EXIT=MACD_BELOW_SIGNAL_AND_CCI_FALLING_2BAR')
print('NEXT=RUN_V139_REPLAY_EQUIVALENCE_TEST')
