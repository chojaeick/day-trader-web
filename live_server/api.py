from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv
import time
import uuid
import asyncio, logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .config import Settings, FALLBACK_UNIVERSE, _symbols
from .db import DB
from .kiwoom import KiwoomClient
from .analytics import ticks_to_bars, multi_timeframe_signal, position_from_ticks, screener_rows, shadow_screener_rows, compare_current_shadow, context_for
from .validation import HistoricalValidator, LiveTop10Validator
from .archive import RankingArchive
from .preopen import PreOpenReportStore, build_usa_preopen_report, build_korea_preopen_report
from .news_ai import analyze_news_with_openai, analyze_news_resilient
from .korea import KoreaMarketAdapter
from .recommendation import build_usa_final_recommendations, build_korea_final_recommendations
from .v4_engine import CleanEngine
from .premarket_briefing import build_premarket_briefing
import os
import re
import requests
import sqlite3

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
s=Settings(); db=DB(s.db_path); k=KiwoomClient(s,db); validator=HistoricalValidator(k,s.db_path); live_validator=LiveTop10Validator(s.db_path); archive=RankingArchive(s.db_path); preopen_store=PreOpenReportStore(s.db_path); korea=KoreaMarketAdapter(k); tasks=[]
manual_scan_state={'last_started_monotonic':0.0,'last_result':None}




v4=CleanEngine(s.db_path)
k.disable_minute_recovery_daytrade=True  # V208 frozen19 DAYTRADE load shed; FE websocket remains enabled

# V171_FROZEN19_PAPER_FEED: isolated replay-equivalent USA paper universe.
FROZEN_USA_PAPER_SYMBOLS=(
    'AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR',
    'QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM'
)
k.frozen_paper_symbols=list(FROZEN_USA_PAPER_SYMBOLS)
v4._frozen_universe_loop_enabled=True
_frozen_usa_paper_state={'enabled':True,'symbols':list(FROZEN_USA_PAPER_SYMBOLS),'rows':[],
                         'updated_at':None,'errors':0,'evaluations':0,'paper_events':0}
_frozen_usa_last_bar={}
_frozen_usa_ctx_window_cache={}
_frozen_usa_seen_tick_min={}  # V197 FE minute gate


# V5 runtime load mode. Connectivity/WebSocket stays alive in both modes;
# only heavy Finder/Tracker analysis cadence changes.
runtime_mode={
    # V156: persist intended trading runtime across service restarts.
    # Can still be overridden explicitly with DAY_TRADER_RUNTIME_MODE=NORMAL.
    'mode':str(os.getenv('DAY_TRADER_RUNTIME_MODE','DAYTRADE') or 'DAYTRADE').upper(),
    'updated_at':datetime.now(timezone.utc).isoformat(),
}
if runtime_mode['mode'] not in ('NORMAL','DAYTRADE'):
    runtime_mode['mode']='DAYTRADE'

def _runtime_profile():
    daytrade=runtime_mode.get('mode')=='DAYTRADE'
    return {
        'mode':'DAYTRADE' if daytrade else 'NORMAL',
        'tracker_seconds':5 if daytrade else 60,
        'finder_seconds':30 if daytrade else 180,
        'korea_tracker_seconds':10 if daytrade else 120,
        'loop_seconds':2 if daytrade else 5,
        'streaming':'ALWAYS_ON',
    }

# V4.6.2.3 warm diagnostics: in-memory operational state only.
# This is intentionally not persisted and does not affect Finder scoring.
bridge_warm_status={}

# V2.2.1: manual briefing generation is asynchronous so the browser never
# has to hold a multi-minute HTTP request open. Scheduled PREOPEN generation
# remains server-side and is not limited by browser timeouts.
briefing_jobs={}
briefing_job_lock=asyncio.Lock()

def _briefing_job_view(job:dict):
    if not job:
        return None
    return {
        'job_id':job.get('job_id'),
        'market':job.get('market'),
        'label':job.get('label'),
        'status':job.get('status'),
        'stage':job.get('stage'),
        'progress':job.get('progress'),
        'created_at':job.get('created_at'),
        'started_at':job.get('started_at'),
        'finished_at':job.get('finished_at'),
        'report_id':job.get('report_id'),
        'trade_date':job.get('trade_date'),
        'error':job.get('error'),
        'detail':job.get('detail'),
        'updated_at':job.get('updated_at'),
        'elapsed_sec':job.get('elapsed_sec'),
    }

async def _run_briefing_job(job_id:str):
    job=briefing_jobs[job_id]
    job['status']='RUNNING'; job['stage']='SCANNING'; job['progress']=10
    job['started_at']=datetime.now(timezone.utc).isoformat()
    try:
        # The report builder performs discovery -> premarket probes -> News AI
        # -> archive. Stages are exposed conservatively around the long task.
        def _job_progress(stage,pct,detail=''):
            job['stage']=stage
            job['progress']=max(0,min(99,int(pct)))
            job['detail']=detail
            job['updated_at']=datetime.now(timezone.utc).isoformat()

        result=await generate_usa_preopen_report(
            scheduled=False,
            label=job.get('label') or 'MANUAL_PREOPEN',
            progress_cb=_job_progress
        )
        job['stage']='SAVING'; job['progress']=95
        job['report_id']=result.get('id')
        job['trade_date']=result.get('trade_date')
        job['status']='COMPLETE'; job['stage']='COMPLETE'; job['progress']=100
        job['finished_at']=datetime.now(timezone.utc).isoformat()
        job['elapsed_sec']=round((datetime.now(timezone.utc)-datetime.fromisoformat(job['started_at'])).total_seconds(),1)
    except Exception as e:
        logging.exception('async manual briefing generation failed')
        job['status']='FAILED'; job['stage']='FAILED'; job['progress']=100
        job['error']=str(e)
        job['finished_at']=datetime.now(timezone.utc).isoformat()

async def _start_manual_briefing_job(market:str='USA'):
    async with briefing_job_lock:
        # Prevent duplicate expensive OpenAI jobs from repeated clicks.
        for j in briefing_jobs.values():
            if j.get('market')==market and j.get('status') in ('QUEUED','RUNNING'):
                return j, False
        job_id=uuid.uuid4().hex[:12]
        job={
            'job_id':job_id,'market':market,'label':'MANUAL_PREOPEN',
            'status':'QUEUED','stage':'QUEUED','progress':0,
            'created_at':datetime.now(timezone.utc).isoformat(),
            'started_at':None,'finished_at':None,
            'report_id':None,'trade_date':None,'error':None
        }
        briefing_jobs[job_id]=job
        asyncio.create_task(_run_briefing_job(job_id))
        return job, True

async def checkpoint_forever():
    done=set()
    while True:
        now=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
        day=now.strftime('%Y-%m-%d'); minute=now.hour*60+now.minute
        targets={'T-10':9*60+20,'T-1':9*60+29,'T+7':9*60+37,'T+30':10*60,'T+60':10*60+30,'CLOSE':15*60+59}
        for label,target in targets.items():
            key=(day,label)
            if key not in done and target<=minute<=target+1 and now.weekday()<5:
                rows=screener_rows(db.quotes(),db.daily_metrics(),10)
                if rows:
                    captured=datetime.now(timezone.utc).isoformat()
                    db.save_ranking_snapshot(day,label,rows,captured)
                    qmap={q.get('symbol'):q for q in db.quotes()}
                    archive.save(day,label,'CURRENT',rows,captured,
                                 float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                                 float((qmap.get('SMH') or {}).get('change_pct') or 0))
                    shadow_rows=shadow_screener_rows(db.quotes(),db.daily_metrics(),10)
                    if shadow_rows:
                        archive.save(day,label,'SHADOW',shadow_rows,captured,
                                     float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                                     float((qmap.get('SMH') or {}).get('change_pct') or 0))
                    done.add(key)
        await asyncio.sleep(20)


async def generate_usa_preopen_report(scheduled:bool=False, label:str='PREOPEN_30', progress_cb=None):
    # Force a fresh market-wide discovery at the official snapshot time.
    scan=None
    try:
        scan=await k.manual_discover_now()
    except Exception as e:
        logging.warning('preopen discovery refresh failed; using current universe: %s', e)

    if progress_cb:
        try: progress_cb('SCANNING',15,'Universe refresh complete')
        except Exception: pass

    current=screener_rows(db.quotes(),db.daily_metrics(),10)
    shadow=shadow_screener_rows(db.quotes(),db.daily_metrics(),10)
    if not current:
        raise RuntimeError('preopen screener rows not ready')

    # Timestamp-check actual 1-minute data for the names that matter to this report.
    probe_symbols=[]
    for r in current+shadow:
        sym=str(r.get('symbol') or '').upper()
        if sym and sym not in probe_symbols:
            probe_symbols.append(sym)
    for sym in ('QQQ','SMH'):
        if sym not in probe_symbols:
            probe_symbols.append(sym)

    probes={}
    for sym in probe_symbols:
        try:
            probes[sym]=await asyncio.to_thread(k.premarket_probe,sym,k.active_exchange(sym))
        except Exception as e:
            logging.warning('premarket freshness probe %s failed: %s',sym,e)
            probes[sym]={'symbol':sym,'data_mode':'UNAVAILABLE','is_fresh_premarket':False,'error':str(e)}

    if progress_cb:
        try: progress_cb('PREMARKET_PROBE',25,'Premarket freshness probes complete')
        except Exception: pass

    news_symbols=[]
    for r in current+shadow:
        sym=str(r.get('symbol') or '').upper()
        if sym and sym not in news_symbols:
            news_symbols.append(sym)
    news_context={
        'qqq_premarket':(probes.get('QQQ') or {}).get('premarket_change_pct'),
        'smh_premarket':(probes.get('SMH') or {}).get('premarket_change_pct'),
        'qqq_data_mode':(probes.get('QQQ') or {}).get('data_mode'),
        'smh_data_mode':(probes.get('SMH') or {}).get('data_mode'),
    }
    def _news_progress(done,total,symbol,status):
        if progress_cb:
            # 30 -> 82 across TOP5, with a visible step after every symbol
            pct=30+int((done/max(1,total))*52)
            try: progress_cb('NEWS_SEARCH_AI',pct,f'{symbol} {status} ({done}/{total})')
            except Exception: pass

    news_result=await asyncio.to_thread(analyze_news_resilient,news_symbols[:5],news_context,_news_progress)
    if progress_cb:
        try: progress_cb('BUILDING_REPORT',86,'News analysis complete; building report')
        except Exception: pass

    report=build_usa_preopen_report(
        current,shadow,db.quotes(),db.daily_metrics(),probes,
        len(getattr(s,'symbols',[]) or []),
        scheduled=scheduled,label=label,news_result=news_result
    )
    report['extra']['scan']=scan
    if progress_cb:
        try: progress_cb('SAVING',92,'Saving briefing and ranking archive')
        except Exception: pass
    rid=preopen_store.save(report)

    # Also freeze the exact CURRENT/SHADOW ranking in the regular Archive.
    qmap={q.get('symbol'):q for q in db.quotes()}
    captured=report['generated_at']
    archive.save(report['trade_date'],label,'CURRENT',current,captured,
                 float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                 float((qmap.get('SMH') or {}).get('change_pct') or 0))
    if shadow:
        archive.save(report['trade_date'],label,'SHADOW',shadow,captured,
                     float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                     float((qmap.get('SMH') or {}).get('change_pct') or 0))
    return {'ok':True,'id':rid,**report}


def _korea_expected_window_live():
    kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
    # Accept only the actual pre-open auction window for scoring.
    # 08:20~08:59 KST keeps manual tests near the target while preventing stale/off-hours data.
    mins=kst.hour*60+kst.minute
    return kst.weekday()<5 and (8*60+20) <= mins <= (8*60+59)

async def generate_korea_preopen_report(scheduled:bool=False,label:str='PREOPEN_30'):
    # Always refresh the domestic multi-source universe first.
    discovery=await asyncio.to_thread(korea.discover,50)
    expected=None
    expected_error=None
    try:
        expected=await asyncio.to_thread(korea.expected_execution_snapshot)
    except Exception as e:
        expected_error=str(e)
        logging.warning('KOREA ka10029 expected-execution unavailable; GAMMA fallback: %s',e)

    report=build_korea_preopen_report(
        discovery,
        expected,
        scheduled=scheduled,
        label=label,
        expected_window_live=_korea_expected_window_live()
    )
    if expected_error:
        report.setdefault('extra',{})['expected_error']=expected_error
    rid=preopen_store.save(report)
    report['id']=rid
    return report

async def preopen_scheduler_forever():
    done=set()
    while True:
        try:
            now_utc=datetime.now(timezone.utc)

            # USA: 09:00 ET, 30 minutes before regular open.
            et=now_utc.astimezone(ZoneInfo('America/New_York'))
            us_day=et.strftime('%Y-%m-%d')
            us_key=('USA',us_day,'PREOPEN_30')
            us_target=int(getattr(s,'preopen_usa_hour_et',9))*60+int(getattr(s,'preopen_usa_minute_et',0))
            us_minute=et.hour*60+et.minute
            us_enabled=bool(getattr(s,'preopen_usa_enabled',True))
            if us_enabled and et.weekday()<5 and us_key not in done and us_target<=us_minute<=us_target+2:
                try:
                    await generate_usa_preopen_report(scheduled=True,label='PREOPEN_30')
                    done.add(us_key)
                    logging.info('scheduled USA PREOPEN_30 report saved for %s',us_day)
                except Exception as e:
                    logging.exception('scheduled USA PREOPEN_30 failed: %s',e)

            # KOREA: 08:30 KST. The snapshot is saved even when ka10029 is
            # unavailable; in that case the report is marked GAMMA_FALLBACK.
            kst=now_utc.astimezone(ZoneInfo('Asia/Seoul'))
            kr_day=kst.strftime('%Y-%m-%d')
            kr_key=('KOREA',kr_day,'PREOPEN_30')
            kr_target=8*60+30
            kr_minute=kst.hour*60+kst.minute
            if kst.weekday()<5 and kr_key not in done and kr_target<=kr_minute<=kr_target+2:
                try:
                    await generate_korea_preopen_report(scheduled=True,label='PREOPEN_30')
                    done.add(kr_key)
                    logging.info('scheduled KOREA PREOPEN_30 report saved for %s',kr_day)
                except Exception as e:
                    logging.exception('scheduled KOREA PREOPEN_30 failed: %s',e)
        except Exception:
            logging.exception('preopen scheduler loop failed')
        await asyncio.sleep(20)


async def v4_engine_forever():
    # V203_DAYTRADE_LOAD_SHED: frozen19 paper is the DAYTRADE authority.
    # Keep NORMAL legacy engine behavior unchanged.
    while _runtime_profile().get('mode')=='DAYTRADE':
        await asyncio.sleep(30)
    last={'USA':0.0,'KOREA':0.0}
    last_tracker={'USA':0.0,'KOREA':0.0}
    warmed_usa=set()
    bridge_warmed=set()
    bridge_warm_task=None

    async def warm_usa_symbols(symbols):
        # Prime only newly entering heavy-tracker names. This avoids showing
        # DATA_INVALID for a fresh Finder rotation while waiting for the
        # once-per-minute recovery loop.
        nonlocal warmed_usa
        wanted=[str(x or '').upper() for x in symbols if x]
        new_syms=[x for x in wanted if x not in warmed_usa]
        if not new_syms:
            return
        for sym in new_syms[:5]:
            try:
                ex=k.active_exchange(sym)
                await asyncio.to_thread(k.quote,sym,ex)
                await asyncio.to_thread(k.daily_metrics,sym,ex)
                inserted,bars=await asyncio.to_thread(k.backfill_symbol,sym,ex,80)
                logging.info('V4 tracker warmup %s/%s: bars=%s inserted=%s',sym,ex,bars,inserted)
            except Exception as e:
                logging.warning('V4 tracker warmup %s failed: %s',sym,e)
            await asyncio.sleep(0.12)
        warmed_usa.update(new_syms)

    async def warm_bridge_candidates(candidates,discovery):
        # V4.6.2.1: prepare data only. This does NOT add symbols to live Finder.
        # Warm the highest Screener-eligible names absent from Discovery plus
        # the core leveraged/inverse ETFs whose data must always be auditable.
        nonlocal bridge_warmed
        d=discovery if isinstance(discovery,dict) else {}
        seen=set()
        for key in ('rows','extreme_rows','quality_risk_rows'):
            seen.update(
                str(r.get('symbol') or '').upper()
                for r in (d.get(key) or []) if r.get('symbol')
            )

        misses=[
            r for r in (candidates or [])
            if r.get('eligible')
            and str(r.get('symbol') or '').upper()
            and str(r.get('symbol') or '').upper() not in seen
        ]
        misses.sort(
            key=lambda r:(float(r.get('score') or 0),abs(float(r.get('change_pct') or 0))),
            reverse=True
        )

        wanted=[]
        for r in misses[:8]:
            sym=str(r.get('symbol') or '').upper()
            if sym and sym not in wanted:wanted.append(sym)
        for sym in ('SOXS','SQQQ','SOXL','TQQQ'):
            if sym not in wanted:wanted.append(sym)

        new_syms=[x for x in wanted if x not in bridge_warmed]
        if not new_syms:return

        for sym in new_syms[:12]:
            started=datetime.now(timezone.utc).isoformat()
            state={
                'symbol':sym,'status':'RUNNING','last_attempt':started,
                'exchange':None,
                'quote_ok':False,'daily_ok':False,'minute_ok':False,
                'minute_bars':None,'inserted':None,
                'failed_step':None,'error_short':None,'error':None
            }
            bridge_warm_status[sym]=state

            # V4.6.2.4 fault isolation:
            # QUOTE / DAILY / MINUTE are independent. DAILY is supporting data;
            # a daily failure must not prevent minute backfill or make usable
            # quote+minute data look like a total warm failure.
            try:
                ex=k.active_exchange(sym)
                state['exchange']=ex
            except Exception as e:
                ex=None
                state['status']='QUOTE_FAILED'
                state['failed_step']='EXCHANGE'
                state['error_short']=str(e)[:160]
                state['error']=str(e)

            if ex:
                try:
                    await asyncio.to_thread(k.quote,sym,ex)
                    state['quote_ok']=True
                except Exception as e:
                    state['failed_step']='QUOTE'
                    state['error_short']=str(e)[:160]
                    state['error']=str(e)
                    logging.warning('V4 bridge quote warm %s failed: %s',sym,e)

                try:
                    await asyncio.to_thread(k.daily_metrics,sym,ex)
                    state['daily_ok']=True
                except Exception as e:
                    # Supporting-data warning only; continue to minute warm.
                    if state.get('failed_step') is None:
                        state['failed_step']='DAILY'
                        state['error_short']=str(e)[:160]
                        state['error']=str(e)
                    logging.warning('V4 bridge daily warm %s failed: %s',sym,e)

                try:
                    inserted,bars=await asyncio.to_thread(k.backfill_symbol,sym,ex,80)
                    state['minute_bars']=bars
                    state['inserted']=inserted
                    state['minute_ok']=bool(int(bars or 0)>=6)
                except Exception as e:
                    state['minute_ok']=False
                    if state.get('failed_step') in (None,'DAILY'):
                        state['failed_step']='MINUTE'
                        state['error_short']=str(e)[:160]
                        state['error']=str(e)
                    logging.warning('V4 bridge minute warm %s failed: %s',sym,e)

            # Evaluate actual data now in DB, not only API call success.
            try:
                q_now=db.quote(sym) or {}
                bars_now=await asyncio.to_thread(lambda: len(ticks_to_bars(db.ticks(sym,2500),1)))
                price_now=float(q_now.get('price') or 0)
            except Exception:
                bars_now=int(state.get('minute_bars') or 0)
                price_now=0.0

            usable=bool(price_now>0 and bars_now>=6)
            state['minute_bars']=bars_now
            state['usable_now']=usable

            if usable and state.get('daily_ok'):
                state['status']='READY'
            elif usable:
                state['status']='READY_DAILY_WARN'
            elif state.get('quote_ok') and not state.get('minute_ok'):
                state['status']='MINUTE_FAILED'
                if state.get('failed_step') is None:
                    state['failed_step']='MINUTE'
            elif not state.get('quote_ok'):
                state['status']='QUOTE_FAILED'
                if state.get('failed_step') is None:
                    state['failed_step']='QUOTE'
            else:
                state['status']='PARTIAL'

            state['finished_at']=datetime.now(timezone.utc).isoformat()

            # Usable quote+minute data is enough to stop wasteful retries even when
            # daily is unavailable. Daily status remains visible as a warning.
            if usable:
                bridge_warmed.add(sym)

            logging.info(
                'V4 bridge warmup %s/%s status=%s quote=%s daily=%s minute=%s bars=%s inserted=%s',
                sym,state.get('exchange'),state.get('status'),
                state.get('quote_ok'),state.get('daily_ok'),state.get('minute_ok'),
                state.get('minute_bars'),state.get('inserted')
            )
            await asyncio.sleep(0.12)

    while True:
        try:
            now=time.monotonic()
            profile=_runtime_profile()
            if profile['mode']=='NORMAL':
                await asyncio.sleep(profile['loop_seconds'])
                continue
            # V122: KOREA safety runs in a dedicated task, isolated from USA latency.
            # This shared loop now handles USA work only; do not duplicate KOREA refresh here.

            # USA discovery/analysis follows independently.
            if now-last['USA']>=profile['finder_seconds']:
                usa_candidates=await asyncio.to_thread(lambda: screener_rows(db.quotes(),db.daily_metrics(),40))
                finder=await asyncio.to_thread(
                    lambda: v4.build_usa_finder(usa_candidates,k.discovery,5,db=db)
                )
                # Candidate data warming runs asynchronously so subsequent loops are not
                # blocked by the bridge warm task itself.
                if bridge_warm_task is None or bridge_warm_task.done():
                    bridge_warm_task=asyncio.create_task(
                        await asyncio.to_thread(warm_bridge_candidates,usa_candidates,k.discovery)
                    )
                finder_syms=[r.get('symbol') for r in (finder.get('rows') or [])]
                light_syms=[r.get('symbol') for r in (finder.get('light_rows') or [])]
                await warm_usa_symbols(finder_syms)
                logging.info(
                    'V4 light tracker: %s',
                    ','.join(x for x in light_syms[:20] if x)
                )
                active=set(finder_syms)
                try:
                    active.update(p.get('symbol') for p in v4.store.positions('USA') if p.get('symbol'))
                except Exception:
                    pass
                warmed_usa.intersection_update(active)
                last['USA']=now

            # Heavy USA analysis is cadence-controlled. Streaming and Kiwoom
            # connectivity are NOT affected by runtime mode.
            if now-last_tracker['USA']>=profile['tracker_seconds']:
                await asyncio.to_thread(v4.refresh_usa_tracker,db)
                last_tracker['USA']=time.monotonic()
        except Exception:
            logging.exception('V4 engine loop failed')
        await asyncio.sleep(_runtime_profile()['loop_seconds'])

async def frozen_usa_paper_forever():
    """V171: frozen 19 feed/evaluation loop; paper ledger only, once per completed 1m bar."""
    await asyncio.sleep(8)
    while True:
        try:
            if _runtime_profile().get('mode')!='DAYTRADE':
                await asyncio.sleep(2)
                continue
            out=[]
            # V215_INCREMENTAL_STATE: keep endpoint/trading telemetry live while
            # the 19-symbol sweep is still running. Strategy evaluation is unchanged.
            def _v215_publish(rec):
                sym=str((rec or {}).get('symbol') or '').upper()
                current=list(_frozen_usa_paper_state.get('rows') or [])
                by={str(x.get('symbol') or '').upper():dict(x) for x in current if isinstance(x,dict) and x.get('symbol')}
                if sym:
                    by[sym]=dict(rec)
                ordered=[]
                for s0 in FROZEN_USA_PAPER_SYMBOLS:
                    if s0 in by:
                        ordered.append(by[s0])
                _frozen_usa_paper_state['rows']=ordered
                _frozen_usa_paper_state['updated_at']=datetime.now(timezone.utc).isoformat()
            for sym in FROZEN_USA_PAPER_SYMBOLS:
                rec={'symbol':sym,'ctx':False,'eval_reason':None,'bar':None,'ticks':0,'paper_event':False}
                try:
                    # V197: FE emits many trades/sec. Avoid rebuilding 40k ticks every 2 sec.
                    latest_ticks=await asyncio.to_thread(db.ticks,sym,1)
                    if not latest_ticks:
                        rec['eval_reason']='NO_TICKS'; out.append(rec); _v215_publish(rec); continue
                    _lt=latest_ticks[-1]
                    try:
                        _lts=(_lt.get('ts') if isinstance(_lt,dict) else _lt[0])
                    except Exception:
                        _lts=str(_lt)
                    _tick_min=str(_lts)[:16]
                    if _frozen_usa_seen_tick_min.get(sym)==_tick_min:
                        old_rec=next((x for x in (_frozen_usa_paper_state.get('rows') or []) if x.get('symbol')==sym),None)
                        out.append(dict(old_rec or rec)); continue
                    _frozen_usa_seen_tick_min[sym]=_tick_min
                    # 12k is enough to preserve the pre-FE sparse history during today's transition,
                    # while removing the pathological 19x40k/2sec workload.
                    ticks=await asyncio.to_thread(db.ticks,sym,5000)  # V203 FE window: enough for >=25 completed 1m bars at observed rates
                    rec['ticks']=len(ticks or [])
                    if not ticks:
                        rec['eval_reason']='NO_TICKS'; out.append(rec); _v215_publish(rec); continue
                    b1=await asyncio.to_thread(ticks_to_bars,ticks,1)
                    if b1 is None or len(b1)<26:
                        rec['eval_reason']='BARS_LT_26'; out.append(rec); _v215_publish(rec); continue

                    # Replay parity: evaluate only a completed minute bar, never the still-forming bar.
                    bars=b1
                    try:
                        last_t=bars.iloc[-1].get('time')
                        now_min=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                        if str(last_t)[:16]==now_min and len(bars)>26:
                            bars=bars.iloc[:-1]
                    except Exception:
                        pass
                    if bars is None or len(bars)<25:
                        rec['eval_reason']='COMPLETED_BARS_LT_25'; out.append(rec); _v215_publish(rec); continue
                    bar_key=str(bars.iloc[-1].get('time'))
                    rec['bar']=bar_key
                    if _frozen_usa_last_bar.get(sym)==bar_key:
                        old=next((x for x in (_frozen_usa_paper_state.get('rows') or []) if x.get('symbol')==sym),None)
                        out.append(dict(old or rec)); _v215_publish(dict(old or rec)); continue

                    price=float(bars.iloc[-1].get('close') or 0)
                    row={'market':'USA','symbol':sym,'price':price,'session':'REGULAR'}
                    # V218: reuse the smallest previously successful history window.
                    # Context math itself is unchanged; only data-window selection is cached.
                    _cached_lim=int(_frozen_usa_ctx_window_cache.get(sym) or 0)
                    if _cached_lim and len(ticks or [])<_cached_lim:
                        _ticks=await asyncio.to_thread(db.ticks,sym,_cached_lim)
                        _b1=await asyncio.to_thread(ticks_to_bars,_ticks,1)
                        if _b1 is not None and len(_b1)>=26:
                            _bars=_b1
                            try:
                                _last_t=_bars.iloc[-1].get('time')
                                _now_min=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                                if str(_last_t)[:16]==_now_min and len(_bars)>26:
                                    _bars=_bars.iloc[:-1]
                            except Exception:
                                pass
                            ticks=_ticks; b1=_b1; bars=_bars
                    ctx=v4._v161_wire_usa_frozen_ctx(row,bars)
                    if not (isinstance(ctx,dict) and ctx.get('entry_args')):
                        _tries=[]
                        if _cached_lim: _tries.append(_cached_lim)
                        for _lim in (12000,24000,40000,80000):
                            if _lim not in _tries: _tries.append(_lim)
                        # V254B_SOXL_160K: deeper prior-session history only for SOXL.
                        if sym == 'SOXL':
                            for _lim in (120000,160000):
                                if _lim not in _tries: _tries.append(_lim)
                        for _lim in _tries:
                            if len(ticks or [])>=_lim and _lim!=_cached_lim: continue
                            _ticks=await asyncio.to_thread(db.ticks,sym,_lim)
                            _b1=await asyncio.to_thread(ticks_to_bars,_ticks,1)
                            if _b1 is None or len(_b1)<26: continue
                            _bars=_b1
                            try:
                                _last_t=_bars.iloc[-1].get('time')
                                _now_min=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                                if str(_last_t)[:16]==_now_min and len(_bars)>26:
                                    _bars=_bars.iloc[:-1]
                            except Exception:
                                pass
                            _ctx=v4._v161_wire_usa_frozen_ctx(row,_bars)
                            if isinstance(_ctx,dict) and _ctx.get('entry_args'):
                                ticks=_ticks; b1=_b1; bars=_bars; ctx=_ctx
                                _frozen_usa_ctx_window_cache[sym]=_lim
                                rec['ctx_fallback']=True; rec['ctx_tick_limit']=_lim; rec['bars']=len(_bars)
                                break
                    elif not _cached_lim:
                        # Base window already sufficient. Cache current tick count capped at 12000.
                        _frozen_usa_ctx_window_cache[sym]=min(max(len(ticks or []),2500),12000)
                    rec['ctx_window_cached']=int(_frozen_usa_ctx_window_cache.get(sym) or 0)
                    row['williams_frozen_ctx']=ctx
                    rec['ctx']=bool(isinstance(ctx,dict) and ctx.get('entry_args'))
                    if isinstance(ctx,dict):
                        rec['ctx_keys']=sorted(list(ctx.keys()))[:30]
                        rec['ctx_missing']=[k for k in ('entry_args','exit_args') if not ctx.get(k)]
                    try: rec['bars']=int(len(bars))
                    except Exception: rec['bars']=0
                    paper_result=v4._paper_williams_step('USA',row)
                    ev=row.get('williams_frozen_eval') or {}
                    rec['eval_reason']=ev.get('reason')
                    rec['entry']=bool(ev.get('entry'))
                    rec['exit']=bool(ev.get('exit'))
                    rec['paper_event']=paper_result is not None
                    if paper_result is not None:
                        _frozen_usa_paper_state['paper_events']=int(_frozen_usa_paper_state.get('paper_events') or 0)+1
                    _frozen_usa_paper_state['evaluations']=int(_frozen_usa_paper_state.get('evaluations') or 0)+1
                    _frozen_usa_last_bar[sym]=bar_key
                    out.append(rec)
                    _v215_publish(rec)
                except Exception as e:
                    rec['eval_reason']='ERROR'; rec['error']=str(e)[:300]; out.append(rec); _v215_publish(rec)
                    _frozen_usa_paper_state['errors']=int(_frozen_usa_paper_state.get('errors') or 0)+1
            _frozen_usa_paper_state['rows']=out
            _frozen_usa_paper_state['updated_at']=datetime.now(timezone.utc).isoformat()
        except Exception as e:
            _frozen_usa_paper_state['errors']=int(_frozen_usa_paper_state.get('errors') or 0)+1
            _frozen_usa_paper_state['last_error']=str(e)[:500]
            logging.exception('V171 frozen USA paper loop failed')
        await asyncio.sleep(2)

async def korea_safety_forever():
    """V122: dedicated KOREA finder/tracker safety cadence, independent of USA work."""
    last_finder=0.0
    last_tracker=0.0
    while True:
        try:
            profile=_runtime_profile()
            kr_open=False
            try:
                kr_open=bool(korea._kst_market_open())
            except Exception:
                pass

            if profile['mode']=='DAYTRADE' or kr_open:
                now=time.monotonic()
                if now-last_finder>=max(300,profile['finder_seconds']):
                    v4.build_korea_finder(korea.discovery,5)
                    last_finder=time.monotonic()
                if now-last_tracker>=profile['korea_tracker_seconds']:
                    await asyncio.to_thread(v4.refresh_korea_tracker,korea)
                    last_tracker=time.monotonic()
        except Exception:
            logging.exception('V122 KOREA safety loop failed')
        await asyncio.sleep(max(1,min(2,_runtime_profile()['loop_seconds'])))

# V123: independent mock-account emergency hard-stop watchdog.
async def williams_mock_hard_stop_forever():
    """Protect Kiwoom MOCK holdings even when tracker/chart work is blocked."""
    import time as _time
    pending={}
    while True:
        try:
            profile=_runtime_profile()
            kr_open=False
            try:
                kr_open=bool(korea._kst_market_open())
            except Exception:
                pass
            if profile.get('mode')=='DAYTRADE' and kr_open:
                from live_server.kiwoom_mock_broker import KiwoomMockBroker
                b=KiwoomMockBroker()
                if b.cfg.order_enable:
                    bal=await asyncio.to_thread(
                        b.request_account,
                        'kt00004',
                        {'qry_tp':'0','dmst_stex_tp':'KRX'}
                    )
                    now=_time.monotonic()
                    live=set()
                    for x in (bal.get('stk_acnt_evlt_prst') or []):
                        sym=str(x.get('stk_cd') or '').replace('A','').zfill(6)
                        try: qty=int(str(x.get('rmnd_qty') or '0').replace(',',''))
                        except Exception: qty=0
                        try: avg=float(str(x.get('avg_prc') or '0').replace(',',''))
                        except Exception: avg=0.0
                        try: cur=abs(float(str(x.get('cur_prc') or '0').replace(',','')))
                        except Exception: cur=0.0
                        if not sym or qty<=0:
                            continue
                        live.add(sym)
                        if avg<=0 or cur<=0 or cur>avg*0.985:
                            continue
                        last=float(pending.get(sym,0.0) or 0.0)
                        if last and (now-last)<60.0:
                            continue
                        r=await asyncio.to_thread(b.sell_market,sym,qty)
                        pending[sym]=_time.monotonic()
                        logging.warning(
                            'WILLIAMS_MOCK_HARD_STOP_WATCHDOG_SELL sym=%s qty=%s avg=%s cur=%s loss_pct=%.4f order_no=%s',
                            sym,qty,avg,cur,((cur/avg)-1.0)*100.0,
                            r.get('ord_no') or r.get('order_no')
                        )
                    for sym in list(pending):
                        if sym not in live:
                            pending.pop(sym,None)
        except Exception:
            logging.exception('V123 mock hard-stop watchdog failed')
        await asyncio.sleep(5)

async def korea_discovery_forever():
    """Keep Korea discovery ready without requiring a browser button click.

    Refresh once during startup and then every ~10 minutes in the useful
    pre-open/regular-session daytime window. Overnight it sleeps without
    repeatedly hitting ranking APIs.
    """
    while True:
        try:
            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
            mins=kst.hour*60+kst.minute
            useful=kst.weekday()<5 and (8*60) <= mins <= (15*60+40)
            if useful:
                await asyncio.to_thread(korea.discover,50)
        except Exception:
            logging.exception('KOREA periodic discovery refresh failed')
        await asyncio.sleep(600)


async def korea_intraday_pulse_forever():
    while True:
        try:
            if korea._kst_market_open():
                await asyncio.to_thread(korea.refresh_intraday_pulse,10,False)
        except Exception:
            logging.exception('KOREA intraday pulse refresh failed')
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not s.app_key or not s.app_secret:
        logging.error('KIWOOM_APP_KEY / KIWOOM_APP_SECRET missing')
    else:
        try:
            await asyncio.to_thread(k.discover_universe)
        except Exception as e:
            logging.warning('startup USA universe discovery failed; using fallback universe: %s', e)
        try:
            await asyncio.to_thread(korea.discover,50)
        except Exception as e:
            logging.warning('startup KOREA universe discovery failed; manual/periodic retry will remain available: %s', e)
        if _runtime_profile().get('mode')=='DAYTRADE':
            # V232: keep USA frozen19 isolation, but restore the proven Korea Williams path.
            # Do NOT start v4_engine_forever here; V203 intentionally sheds that legacy heavy loop in DAYTRADE.
            tasks.extend([
                asyncio.create_task(k.websocket_forever()),
                asyncio.create_task(frozen_usa_paper_forever()),  # V213B_RESTORED_FROZEN
                asyncio.create_task(preopen_scheduler_forever()),  # V232_KOREA_RESTORE
                asyncio.create_task(korea_discovery_forever()),    # V232_KOREA_RESTORE
                asyncio.create_task(korea_intraday_pulse_forever()), # V232_KOREA_RESTORE
                asyncio.create_task(korea_safety_forever()),       # V232_KOREA_RESTORE
                asyncio.create_task(williams_mock_hard_stop_forever()), # V232_KOREA_RESTORE
                asyncio.create_task(daytrade_entry_auto_forever()), # V232_KOREA_RESTORE
            ])
        else:
            # NORMAL mode: preserve legacy startup behavior unchanged.
            tasks.extend([asyncio.create_task(k.websocket_forever()),
                           # V180 disabled: second USA websocket session is rejected/closed by Kiwoom (1000 OK Bye).
                           # asyncio.create_task(k.frozen19_websocket_forever()),asyncio.create_task(k.snapshot_poll_forever()),
                          asyncio.create_task(k.daily_refresh_forever()),asyncio.create_task(k.backfill_forever_once()),
                          asyncio.create_task(k.discovery_forever()),asyncio.create_task(checkpoint_forever()),
                          asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),
                          asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),
                          asyncio.create_task(williams_mock_hard_stop_forever()),
                          asyncio.create_task(v4_engine_forever()),
                           asyncio.create_task(frozen_usa_paper_forever())])
            tasks.append(asyncio.create_task(fujimoto_auto_forever()))
            tasks.append(asyncio.create_task(fujimoto_auto_v4_forever()))
            tasks.append(asyncio.create_task(daytrade_entry_auto_forever()))


    yield
    for t in tasks: t.cancel()


# V2.1.1 hotfix: the Streamlit process loaded .env, but the FastAPI/systemd
# process did not. Load the backend project .env explicitly before any API
# client reads OPENAI_API_KEY.
_BACKEND_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_BACKEND_ENV, override=True)

app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)

# V188_READONLY_KIWOOM_RANK_SYMBOL_PROBE
@app.get('/api/v4/USA/debug/rank-symbol/{symbol}')
def v188_rank_symbol_probe(symbol:str):
    symbol=str(symbol or '').upper().strip()
    out={'ok':True,'symbol':symbol,'hits':[],'quote_matrix':{}}
    methods=[('usa20530', lambda: k.ranking_today_volume('0')),
             ('usa20910_up', lambda: k.ranking_change_rate('1')),
             ('usa20910_dn', lambda: k.ranking_change_rate('4')),
             ('usa20520', lambda: k.ranking_volume_surge())]
    for name,fn in methods:
        try:
            rows=fn() or []
            for r in rows:
                if str(r.get('stk_cd') or '').upper().strip()==symbol:
                    out['hits'].append({'source':name,'row':r})
        except Exception as e:
            out.setdefault('errors',[]).append({'source':name,'error':repr(e)})
    for ex in ('ND','NY','NA'):
        try:
            out['quote_matrix'][ex]=k.quote(symbol,ex)
        except Exception as e:
            out['quote_matrix'][ex]={'error':repr(e)}
    return out



@app.get('/api/v5/daytrade-entry/KOREA')
async def v5_daytrade_entry_korea(limit:int=10, eval_limit:int=5, max_pages:int=1):
    return await asyncio.to_thread(korea.daytrade_entry_v12,limit,eval_limit,max_pages)



# ===== MARKET GATE V2 PROBE =====
def _mg_probe_post(api_id, path, bodies):
    out=[]
    for body in bodies:
        try:
            r=requests.post(
                s.rest_base+path,
                headers=k.headers(api_id),
                json=body,
                timeout=20,
            )
            try: d=r.json()
            except Exception: d={'_text':r.text[:1000]}
            out.append({
                'body':body,
                'http_status':r.status_code,
                'return_code':d.get('return_code') if isinstance(d,dict) else None,
                'return_msg':d.get('return_msg') if isinstance(d,dict) else None,
                'keys':list(d.keys())[:40] if isinstance(d,dict) else [],
                'sample':{kk:(vv[:2] if isinstance(vv,list) else vv) for kk,vv in list(d.items())[:20]} if isinstance(d,dict) else d,
            })
        except Exception as e:
            out.append({'body':body,'error':str(e)[:300]})
    return out

@app.get('/api/v5/market-gate-probe/KOREA')
async def v5_market_gate_probe_korea():
    def _run():
        # Use multiple conservative body candidates because Kiwoom field names differ by TR.
        flow_bodies=[
            {'mrkt_tp':'001','amt_qty_tp':'1','base_dt_tp':'0','stex_tp':'3'},
            {'mrkt_tp':'001','amt_qty_tp':'1','stex_tp':'3'},
            {'mrkt_tp':'001','stex_tp':'3'},
            {'mrkt_tp':'101','amt_qty_tp':'1','base_dt_tp':'0','stex_tp':'3'},
            {'mrkt_tp':'101','amt_qty_tp':'1','stex_tp':'3'},
            {'mrkt_tp':'101','stex_tp':'3'},
        ]
        index_bodies=[
            {'mrkt_tp':'001','inds_cd':'001'},
            {'mrkt_tp':'001','inds_cd':'101'},
            {'mrkt_tp':'001','sector_cd':'001'},
            {'mrkt_tp':'101','inds_cd':'101'},
            {'mrkt_tp':'101','sector_cd':'101'},
        ]
        return {
            'ok':True,
            'version':'MARKET_GATE_V2_PROBE',
            'ka10051':_mg_probe_post('ka10051','/api/dostk/sect',flow_bodies),
            'ka20009':_mg_probe_post('ka20009','/api/dostk/sect',index_bodies),
            'note':'Diagnostic only. No Market Gate scoring change yet.'
        }
    return await asyncio.to_thread(_run)



@app.get('/api/v5/market-gate/KOREA')
async def v5_market_gate_korea():
    return await asyncio.to_thread(korea.market_gate_v21)



@app.get('/api/v5/fujimoto-swing/KOREA/{symbol}')
async def v5_fujimoto_swing_korea(symbol:str):
    return await asyncio.to_thread(korea.fujimoto_swing_daily_v1,symbol)



# ===== FUJIMOTO TRACKER V2 NONBLOCKING =====
_fujimoto_tracker_v2_cursor=0

@app.get('/api/v5/fujimoto-tracker-v2/KOREA')
async def v5_fujimoto_tracker_v2_korea(batch_size:int=2,limit:int=10,max_pages:int=1,cache_ttl_sec:int=180):
    def _run():
        import time as _time
        global _fujimoto_tracker_v2_cursor
        snap=korea.momentum_rank_snapshot_v54()
        candidates=[]
        for r in list(snap.get('rows') or []):
            nm=str(r.get('name') or '').strip(); up=nm.upper()
            if '스팩' in nm or 'SPAC' in up: continue
            if ' ETN' in (' '+up) or up.endswith('ETN'): continue
            if nm.endswith('우') or nm.endswith('우B') or '우선주' in nm or re.search(r'\d+우B$',nm): continue
            lane=min(int(r.get('value_rank') or 9999),int(r.get('volume_rank') or 9999))
            row=dict(r)
            row['finder_rank_score']=max(0.0,100.0-min(lane,100)*0.6)
            cached=_fujimoto_overlay_cache.get(row.get('symbol')) or {}
            row['cached_fujimoto_score']=cached.get('score')
            row['cached_at']=cached.get('_cached_at')
            row['cached_trade_priority']=(round(row['finder_rank_score']*0.40+float(cached.get('score'))*0.60,1)
                                          if cached.get('score') is not None else None)
            candidates.append(row)

        if not candidates:
            return {'ok':True,'version':'FUJIMOTO_TRACKER_V2','count':0,'rows':[]}

        candidates.sort(key=lambda x:(x.get('cached_trade_priority') is not None,
                                      x.get('cached_trade_priority') or -1,
                                      x.get('finder_rank_score') or 0),reverse=True)
        watch_pool=candidates[:max(10,min(int(limit)*2,30))]
        now=_time.time()
        ttl=max(30,min(int(cache_ttl_sec),900))

        # One HTTP request must stay lightweight. Rotate at most 2 fresh Kiwoom chart calls.
        bs=max(1,min(int(batch_size),2,len(watch_pool)))
        start=int(_fujimoto_tracker_v2_cursor)%len(watch_pool)
        scan_order=[watch_pool[(start+i)%len(watch_pool)] for i in range(len(watch_pool))]
        fetch_targets=[]; cache_hits=0
        for r in scan_order:
            sym=r.get('symbol')
            cached=_fujimoto_overlay_cache.get(sym) or {}
            age=(now-float(cached.get('_cached_at') or 0)) if cached.get('_cached_at') else 10**9
            if cached.get('score') is not None and age<=ttl:
                cache_hits+=1
                # hydrate tracker state from recent cache if not already present
                if sym not in _fujimoto_tracker_state:
                    eng=evaluate_fujimoto_engine_v1([],previous_state='WATCH',position_open=False) if False else cached
                    _fujimoto_tracker_state[sym]={
                        'state':cached.get('engine_state') or cached.get('state') or 'WATCH',
                        'position_open':False,'signal':cached.get('signal') or 'NONE',
                        'score':cached.get('score'),'updated_at':datetime.now(timezone.utc).isoformat(),
                        'engine':cached,
                    }
                continue
            fetch_targets.append(r)
            if len(fetch_targets)>=bs: break

        fetched=0
        for r in fetch_targets:
            sym=r.get('symbol')
            prev=_fujimoto_tracker_state.get(sym) or {'state':'WATCH','position_open':False}
            try:
                d=korea.canonical_minute_bars(sym,max_pages=1)
                eng=evaluate_fujimoto_engine_v1(
                    d.get('bars') or [],previous_state=prev.get('state') or 'WATCH',
                    position_open=bool(prev.get('position_open')))
                _fujimoto_tracker_state[sym]={
                    'state':eng.get('engine_state') or 'WATCH','position_open':bool(prev.get('position_open')),
                    'signal':eng.get('signal'),'score':eng.get('score'),
                    'updated_at':datetime.now(timezone.utc).isoformat(),'engine':eng,
                }
                sc=dict(eng); sc['_cached_at']=now; _fujimoto_overlay_cache[sym]=sc
                fetched+=1
            except Exception as e:
                _fujimoto_tracker_state[sym]={
                    'state':'DATA_INVALID','position_open':bool(prev.get('position_open')),
                    'signal':'NONE','score':None,'error':str(e)[:180],
                    'updated_at':datetime.now(timezone.utc).isoformat()
                }
            _time.sleep(0.15)

        _fujimoto_tracker_v2_cursor=(start+max(1,len(fetch_targets)))%len(watch_pool)

        rows=[]
        for r in watch_pool:
            sym=r.get('symbol'); st=_fujimoto_tracker_state.get(sym) or {}
            eng=st.get('engine') or _fujimoto_overlay_cache.get(sym) or {}
            score=st.get('score') if st.get('score') is not None else eng.get('score')
            row=dict(r)
            row.update({
                'fujimoto_score':score,
                'engine_state':st.get('state') or eng.get('engine_state') or eng.get('state') or 'NOT_EVALUATED',
                'signal':st.get('signal') or eng.get('signal') or 'NONE',
                'position_open':bool(st.get('position_open')),
                'transition':eng.get('transition'),'actionable':bool(eng.get('actionable')),
                'entry_reasons':eng.get('entry_reasons') or [],'exit_reasons':eng.get('exit_reasons') or [],
                'rsi':eng.get('rsi'),'macd':eng.get('macd'),'macd_signal':eng.get('macd_signal'),'macd_hist':eng.get('macd_hist'),
                'latest_bar_time':eng.get('latest_bar_time'),
                'trade_priority':round(r['finder_rank_score']*0.40+float(score)*0.60,1) if score is not None else None,
            })
            rows.append(row)
        rows.sort(key=lambda x:(x.get('trade_priority') is not None,x.get('trade_priority') or -1,x.get('finder_rank_score') or 0),reverse=True)
        rows=rows[:max(1,min(int(limit),20))]
        return {
            'ok':True,'version':'FUJIMOTO_TRACKER_V2_NONBLOCKING','rank_status':snap.get('status'),
            'signal_only':True,'order_placement':False,'watch_pool_count':len(watch_pool),
            'evaluated_count':sum(1 for r in rows if r.get('fujimoto_score') is not None),
            'count':len(rows),'cursor':_fujimoto_tracker_v2_cursor,
            'fresh_fetch_count':fetched,'cache_hit_count':cache_hits,
            'max_fresh_fetch_per_call':2,'rows':rows,
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
    return await asyncio.to_thread(_run)



# ===== FUJIMOTO TRACKER V1 =====
_fujimoto_tracker_state={}
_fujimoto_tracker_cursor=0

@app.get('/api/v5/fujimoto-tracker/KOREA')
async def v5_fujimoto_tracker_korea(batch_size:int=5,limit:int=10,max_pages:int=1):
    def _run():
        import time as _time
        global _fujimoto_tracker_cursor

        snap=korea.momentum_rank_snapshot_v54()
        candidates=[]
        for r in list(snap.get('rows') or []):
            nm=str(r.get('name') or '').strip(); up=nm.upper()
            if '스팩' in nm or 'SPAC' in up: continue
            if ' ETN' in (' '+up) or up.endswith('ETN'): continue
            if nm.endswith('우') or nm.endswith('우B') or '우선주' in nm or re.search(r'\d+우B$',nm): continue
            lane=min(int(r.get('value_rank') or 9999),int(r.get('volume_rank') or 9999))
            row=dict(r)
            row['finder_rank_score']=max(0.0,100.0-min(lane,100)*0.6)
            cached=_fujimoto_overlay_cache.get(row.get('symbol')) or {}
            row['cached_fujimoto_score']=cached.get('score')
            row['cached_trade_priority']=(round(row['finder_rank_score']*0.40+float(cached.get('score'))*0.60,1)
                                          if cached.get('score') is not None else None)
            candidates.append(row)

        if not candidates:
            return {'ok':True,'version':'FUJIMOTO_TRACKER_V1','count':0,'rows':[]}

        # First preference: already scored high-priority names. Unscored names remain in rank order.
        candidates.sort(key=lambda x:(x.get('cached_trade_priority') is not None,
                                      x.get('cached_trade_priority') or -1,
                                      x.get('finder_rank_score') or 0),reverse=True)
        watch_pool=candidates[:max(10,min(int(limit)*2,30))]

        bs=max(1,min(int(batch_size),6,len(watch_pool)))
        start=int(_fujimoto_tracker_cursor)%len(watch_pool)
        batch=[watch_pool[(start+i)%len(watch_pool)] for i in range(bs)]
        _fujimoto_tracker_cursor=(start+bs)%len(watch_pool)
        now=_time.time()

        for r in batch:
            sym=r.get('symbol')
            prev=_fujimoto_tracker_state.get(sym) or {'state':'WATCH','position_open':False}
            try:
                d=korea.canonical_minute_bars(sym,max_pages=max(1,min(int(max_pages),2)))
                eng=evaluate_fujimoto_engine_v1(
                    d.get('bars') or [],
                    previous_state=prev.get('state') or 'WATCH',
                    position_open=bool(prev.get('position_open'))
                )
                # Tracker is signal-only. ENTRY does not flip position_open automatically.
                _fujimoto_tracker_state[sym]={
                    'state':eng.get('engine_state') or 'WATCH',
                    'position_open':bool(prev.get('position_open')),
                    'signal':eng.get('signal'),
                    'score':eng.get('score'),
                    'updated_at':datetime.now(timezone.utc).isoformat(),
                    'engine':eng,
                }
                # Reuse score in Finder overlay cache.
                sc=dict(eng); sc['_cached_at']=now; _fujimoto_overlay_cache[sym]=sc
            except Exception as e:
                _fujimoto_tracker_state[sym]={
                    'state':'DATA_INVALID','position_open':bool(prev.get('position_open')),
                    'signal':'NONE','score':None,'error':str(e)[:180],
                    'updated_at':datetime.now(timezone.utc).isoformat()
                }
            _time.sleep(0.20)

        rows=[]
        for r in watch_pool:
            sym=r.get('symbol')
            st=_fujimoto_tracker_state.get(sym) or {}
            eng=st.get('engine') or {}
            score=st.get('score')
            row=dict(r)
            row.update({
                'fujimoto_score':score,
                'engine_state':st.get('state') or 'NOT_EVALUATED',
                'signal':st.get('signal') or 'NONE',
                'position_open':bool(st.get('position_open')),
                'transition':eng.get('transition'),
                'actionable':bool(eng.get('actionable')),
                'entry_reasons':eng.get('entry_reasons') or [],
                'exit_reasons':eng.get('exit_reasons') or [],
                'rsi':eng.get('rsi'),'macd':eng.get('macd'),'macd_signal':eng.get('macd_signal'),'macd_hist':eng.get('macd_hist'),
                'latest_bar_time':eng.get('latest_bar_time'),
                'trade_priority':round(r['finder_rank_score']*0.40+float(score)*0.60,1) if score is not None else None,
            })
            rows.append(row)

        state_order={'ENTRY':0,'ENTRY_READY':1,'PREPARE':2,'HOLD':3,'PARTIAL_EXIT':4,'EXIT':5,'WATCH':6,'NOT_EVALUATED':7,'DATA_INVALID':8}
        rows.sort(key=lambda x:(x.get('trade_priority') is not None,
                                x.get('trade_priority') or -1,
                                -state_order.get(x.get('engine_state'),9)),reverse=True)
        rows=rows[:max(1,min(int(limit),20))]
        return {
            'ok':True,'version':'FUJIMOTO_TRACKER_V1','rank_status':snap.get('status'),
            'signal_only':True,'order_placement':False,
            'watch_pool_count':len(watch_pool),'evaluated_count':sum(1 for r in rows if r.get('fujimoto_score') is not None),
            'count':len(rows),'cursor':_fujimoto_tracker_cursor,'rows':rows,
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
    return await asyncio.to_thread(_run)

@app.post('/api/v5/fujimoto-tracker/KOREA/{symbol}/position')
async def v5_fujimoto_tracker_position(symbol:str,open:bool=False):
    cur=_fujimoto_tracker_state.get(symbol) or {'state':'WATCH','position_open':False}
    cur['position_open']=bool(open)
    if open and cur.get('state') in ('ENTRY','ENTRY_READY','PREPARE','WATCH'):
        cur['state']='HOLD'
    if not open and cur.get('state') in ('HOLD','PARTIAL_EXIT','EXIT'):
        cur['state']='WATCH'
    cur['updated_at']=datetime.now(timezone.utc).isoformat()
    _fujimoto_tracker_state[symbol]=cur
    return {'ok':True,'symbol':symbol,'state':cur.get('state'),'position_open':cur.get('position_open'),'signal_only':True}



from .fujimoto import evaluate_fujimoto_engine_v1

@app.get('/api/v5/fujimoto-engine/KOREA/{symbol}')
async def v5_fujimoto_engine_korea(symbol:str,max_pages:int=2,previous_state:str='WATCH',position_open:bool=False):
    def _run():
        d=korea.canonical_minute_bars(symbol,max_pages=max(1,min(int(max_pages),3)))
        out=evaluate_fujimoto_engine_v1(d.get('bars') or [],previous_state=previous_state,position_open=position_open)
        out['symbol']=symbol
        out['source']='KIWOOM_KA10080_CANONICAL_1M'
        return out
    return await asyncio.to_thread(_run)



# ===== V55 FINDER + FUJIMOTO OVERLAY =====
_fujimoto_overlay_cache={}
_fujimoto_overlay_cursor=0

@app.get('/api/v5/korea-finder-fujimoto-v55')
async def v55_korea_finder_fujimoto(batch_size:int=6,limit:int=40,max_pages:int=1):
    def _run():
        import time as _time
        global _fujimoto_overlay_cursor
        snap=korea.momentum_rank_snapshot_v54()
        candidates=list(snap.get('rows') or [])

        # Operational exclusions. ETFs remain eligible.
        cleaned=[]
        for r in candidates:
            nm=str(r.get('name') or '').strip(); up=nm.upper()
            reason=None
            if '스팩' in nm or 'SPAC' in up: reason='SPAC'
            elif ' ETN' in (' '+up) or up.endswith('ETN'): reason='ETN'
            elif nm.endswith('우') or nm.endswith('우B') or '우선주' in nm or re.search(r'\d+우B$',nm): reason='PREFERRED'
            if reason: continue
            cleaned.append(dict(r))

        if not cleaned:
            return {'ok':True,'status':snap.get('status'),'candidate_count':0,'finder_count':0,'rows':[]}

        # Do not create a large synchronous request: rotate only a small batch.
        bs=max(1,min(int(batch_size),8,len(cleaned)))
        start=int(_fujimoto_overlay_cursor)%len(cleaned)
        batch=[cleaned[(start+i)%len(cleaned)] for i in range(bs)]
        _fujimoto_overlay_cursor=(start+bs)%len(cleaned)
        now=_time.time()

        for r in batch:
            sym=r.get('symbol')
            try:
                d=korea.canonical_minute_bars(sym,max_pages=max(1,min(int(max_pages),2)))
                score=evaluate_fujimoto_v1(d.get('bars') or [])
                score['_cached_at']=now
                _fujimoto_overlay_cache[sym]=score
            except Exception as e:
                _fujimoto_overlay_cache[sym]={'ok':False,'score':None,'state':'DATA_INVALID','reason':str(e)[:180],'_cached_at':now}
            _time.sleep(0.20)

        rows=[]
        for r in cleaned:
            sym=r.get('symbol')
            f=_fujimoto_overlay_cache.get(sym) or {}
            row=dict(r)
            row['fujimoto_score']=f.get('score')
            row['fujimoto_state']=f.get('state') or 'NOT_EVALUATED'
            row['fujimoto_actionable']=bool(f.get('actionable'))
            row['rsi']=f.get('rsi'); row['macd']=f.get('macd'); row['macd_signal']=f.get('macd_signal'); row['macd_hist']=f.get('macd_hist')
            row['ma20']=f.get('ma20'); row['latest_bar_time']=f.get('latest_bar_time')
            # Rank lane remains separate from Fujimoto.  Trade priority only combines after score exists.
            lane_rank=min(int(row.get('value_rank') or 9999),int(row.get('volume_rank') or 9999))
            row['finder_rank_score']=max(0.0,100.0-min(lane_rank,100)*0.6)
            if f.get('score') is not None:
                row['trade_priority']=round(row['finder_rank_score']*0.40+float(f.get('score'))*0.60,1)
            else:
                row['trade_priority']=None
            rows.append(row)

        rows.sort(key=lambda x:(x.get('trade_priority') is not None,x.get('trade_priority') or -1,x.get('finder_rank_score') or 0),reverse=True)
        rows=rows[:max(1,min(int(limit),100))]
        evaluated=sum(1 for r in rows if r.get('fujimoto_score') is not None)
        return {
            'ok':True,
            'version':'KOREA_FINDER_FUJIMOTO_V55',
            'rank_status':snap.get('status'),
            'rank_scope':snap.get('rank_scope'),
            'candidate_count':len(cleaned),
            'evaluated_count':evaluated,
            'finder_count':len(rows),
            'cursor':_fujimoto_overlay_cursor,
            'scoring':'trade_priority = finder_rank_score*0.40 + fujimoto_score*0.60',
            'rows':rows,
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
    return await asyncio.to_thread(_run)



from .fujimoto import evaluate_fujimoto_v1

@app.get('/api/v5/fujimoto-score/KOREA/{symbol}')
async def v5_fujimoto_score_korea(symbol:str,max_pages:int=2):
    def _run():
        d=korea.canonical_minute_bars(symbol,max_pages=max(1,min(int(max_pages),3)))
        out=evaluate_fujimoto_v1(d.get('bars') or [])
        out['symbol']=symbol
        out['source']='KIWOOM_KA10080_CANONICAL_1M'
        return out
    return await asyncio.to_thread(_run)



@app.get('/api/v5/korea-momentum-ranks-v54')
async def v54_korea_momentum_ranks():
    return await asyncio.to_thread(korea.momentum_rank_snapshot_v54)



@app.get('/api/v5/korea-momentum-ranks-v53')
async def v53_korea_momentum_ranks():
    return await asyncio.to_thread(korea.momentum_rank_snapshot_v53)

@app.get('/api/v5/korea-momentum-finder-v53')
async def v53_korea_momentum_finder(batch_size:int=6,limit:int=40):
    return await asyncio.to_thread(korea.momentum_finder_v53,batch_size,limit)



@app.get('/api/v5/korea-momentum-finder-v52')
async def v52_korea_momentum_finder(batch_size:int=20,limit:int=40):
    return await asyncio.to_thread(korea.momentum_finder_v52,batch_size,limit)



@app.get('/api/v5/korea-momentum-finder-v51')
async def v51_korea_momentum_finder(batch_size:int=20,limit:int=40):
    return await asyncio.to_thread(korea.broad_momentum_finder_v51,batch_size,limit)


@app.get('/api/v5/korea-momentum-finder')
async def v48_korea_momentum_finder(batch_size:int=20,limit:int=40):
    return await asyncio.to_thread(korea.broad_momentum_finder_v48,batch_size,limit)



@app.get('/api/v5/korea-momentum-original')
async def v47_korea_momentum_original(batch_size:int=20):
    return await asyncio.to_thread(korea.original_momentum_scan_v47,batch_size)



# ===== V46 KIWOOOM SAVED CONDITION SEARCH (KOREA) =====
async def _v46_condition_ws_request(seq=None):
    import json as _json
    import asyncio as _asyncio
    import websockets as _websockets

    token=await _asyncio.to_thread(k.get_token)
    async with _websockets.connect(k.s.ws_url,ping_interval=None,close_timeout=5) as ws:
        await ws.send(_json.dumps({'trnm':'LOGIN','token':token}))
        while True:
            d=_json.loads(await _asyncio.wait_for(ws.recv(),timeout=20))
            if d.get('trnm')=='PING':
                await ws.send(_json.dumps(d)); continue
            if d.get('trnm')=='LOGIN':
                if d.get('return_code')!=0:
                    raise RuntimeError(f"LOGIN failed: {d}")
                break

        # Official API requires the saved condition list to be loaded first.
        await ws.send(_json.dumps({'trnm':'CNSRLST'}))
        while True:
            d=_json.loads(await _asyncio.wait_for(ws.recv(),timeout=20))
            if d.get('trnm')=='PING':
                await ws.send(_json.dumps(d)); continue
            if d.get('trnm')=='CNSRLST':
                if d.get('return_code')!=0:
                    raise RuntimeError(f"CNSRLST failed: {d}")
                raw=d.get('data') or []
                conditions=[]
                for x in raw:
                    if isinstance(x,(list,tuple)) and len(x)>=2:
                        conditions.append({'seq':str(x[0]),'name':str(x[1])})
                    elif isinstance(x,dict):
                        conditions.append({'seq':str(x.get('seq') or ''),'name':str(x.get('name') or '')})
                break

        if seq is None:
            return {'ok':True,'conditions':conditions,'count':len(conditions)}

        seq=str(seq)
        if seq not in {x['seq'] for x in conditions}:
            return {'ok':False,'reason':'CONDITION_SEQ_NOT_FOUND','seq':seq,'conditions':conditions}

        rows=[]; cont_yn='N'; next_key=''; pages=0
        while pages<20:
            req={'trnm':'CNSRREQ','seq':seq,'search_type':'0','stex_tp':'K','cont_yn':cont_yn,'next_key':next_key}
            await ws.send(_json.dumps(req))
            while True:
                d=_json.loads(await _asyncio.wait_for(ws.recv(),timeout=30))
                if d.get('trnm')=='PING':
                    await ws.send(_json.dumps(d)); continue
                if d.get('trnm')=='CNSRREQ':
                    break
            if d.get('return_code')!=0:
                raise RuntimeError(f"CNSRREQ failed: {d}")
            pages+=1
            for x in d.get('data') or []:
                if not isinstance(x,dict):
                    continue
                sym=str(x.get('9001') or x.get('stk_cd') or '').strip()
                if sym.startswith('A') and len(sym)>=7:
                    sym=sym[1:7]
                rows.append({
                    'symbol':sym,
                    'name':str(x.get('302') or x.get('stk_nm') or '').strip(),
                    'price':abs(_v5_num(x.get('10'))),
                    'change_pct':float(str(x.get('12') or '0').replace(',','').replace('+','') or 0),
                    'volume':abs(_v5_num(x.get('13'))),
                    'raw':x,
                })
            cont_yn=str(d.get('cont_yn') or 'N').upper()
            next_key=str(d.get('next_key') or '')
            if cont_yn!='Y' or not next_key:
                break

        # de-duplicate while preserving server order
        seen=set(); uniq=[]
        for r in rows:
            if not r['symbol'] or r['symbol'] in seen: continue
            seen.add(r['symbol']); uniq.append(r)
        name=next((x['name'] for x in conditions if x['seq']==seq),'')
        return {'ok':True,'seq':seq,'name':name,'count':len(uniq),'pages':pages,'rows':uniq}

@app.get('/api/v5/korea-condition-list')
async def v46_korea_condition_list():
    return await _v46_condition_ws_request(None)

@app.get('/api/v5/korea-condition-run/{seq}')
async def v46_korea_condition_run(seq:str):
    return await _v46_condition_ws_request(seq)



@app.get('/api/v5/momentum-cache/USA')
def v44_momentum_cache_usa():
    import time as _time
    rows=[]
    now=_time.time()
    cache=getattr(k,'_momentum_daily_cache',{}) or {}
    for key,feat in cache.items():
        try:
            symbol,exchange=key
        except Exception:
            continue
        feat=feat or {}
        age=now-float(feat.get('_cached_at',0) or 0)
        if age>1800:
            continue
        rows.append({
            'symbol':symbol,'exchange':exchange,
            'ok':feat.get('ok'),'reason':feat.get('reason'),
            'daily_rows':feat.get('daily_rows'),'pages':feat.get('pages'),
            'macd':feat.get('macd'),'macd_signal':feat.get('macd_signal'),
            'macd_above_signal':bool(feat.get('macd_above_signal')),
            'macd_zero_cross_bars_ago':feat.get('macd_zero_cross_bars_ago'),
            'macd_cross_5':bool(feat.get('macd_cross_5')),
            'momentum_type':feat.get('momentum_type'),
            'momentum_fresh':bool(feat.get('momentum_fresh')),
            'momentum_continuation':bool(feat.get('momentum_continuation')),
            'high_52w_gap_pct':feat.get('high_52w_gap_pct'),
            'near_52w_high':bool(feat.get('near_52w_high')),
            'momentum_match':bool(feat.get('momentum_match')),
            'age_sec':round(age,1),
        })
    rows.sort(key=lambda r:(0 if r.get('momentum_match') else 1, r.get('symbol') or ''))
    ok=[r for r in rows if r.get('ok')]
    return {
        'ok':True,
        'cursor':getattr(k,'_v43_momentum_cursor',None),
        'cached_count':len(rows),
        'feature_ok_count':len(ok),
        'feature_fail_count':len(rows)-len(ok),
        'macd_cross_5_count':sum(1 for r in ok if r.get('macd_cross_5')),
        'near_52w_count':sum(1 for r in ok if r.get('near_52w_high')),
        'momentum_fresh_count':sum(1 for r in ok if r.get('momentum_fresh')),
        'momentum_continuation_count':sum(1 for r in ok if r.get('momentum_continuation')),
        'momentum_match_count':sum(1 for r in ok if r.get('momentum_match')),
        'formula':'52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100) AND (FRESH:MACD0_CROSS_5 OR CONTINUATION:MACD_GT_0_AND_GT_SIGNAL)',
        'rows':rows,
    }

@app.get('/api/v5/momentum-diagnostic/USA')
def v40_momentum_diagnostic_usa():
    try:
        volume=k.ranking_today_volume('0')
        dollar=k.ranking_today_volume('1')
        return {'ok':True,**k.v40_momentum_diagnostic(volume,dollar)}
    except Exception as e:
        return {'ok':False,'error':str(e)}
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])

# ===== V28 LONG-TERM MONTHLY HISTORY FEED =====
def _v28_num(v):
    try:
        return abs(float(str(v).replace(',','').replace('+','').strip()))
    except Exception:
        return 0.0

def _v28_monthly_from_daily(rows):
    cleaned=[]
    for x in rows or []:
        if not isinstance(x,dict):
            continue
        dt=str(x.get('dt') or x.get('date') or x.get('stk_dt') or x.get('base_dt') or '').strip().replace('-','')
        close=_v28_num(x.get('cur_prc') if x.get('cur_prc') is not None else x.get('close'))
        if len(dt)>=8 and close>0:
            cleaned.append((dt[:8],close))
    cleaned=sorted(set(cleaned),key=lambda z:z[0])
    by_month={}
    for dt,close in cleaned:
        by_month[dt[:6]]=(dt,close)
    out=[]
    for m,(dt,close) in sorted(by_month.items()):
        out.append({'month':f'{m[:4]}-{m[4:6]}','date':dt,'close':close})
    return out

@app.get('/api/v5/monthly-history/{market}/{symbol}')
def v28_monthly_history(market:str,symbol:str):
    market=str(market or '').upper().strip()
    symbol=str(symbol or '').upper().strip()
    if not symbol:
        raise HTTPException(status_code=400,detail='symbol required')
    try:
        if market=='KOREA':
            code=symbol.split('_',1)[0]
            rows=[]; next_key=''; pages=0
            # ka10081 domestic daily chart; two pages is comfortably > 10 months.
            while pages<3:
                hdr=k.headers('ka10081')
                if next_key:
                    hdr['cont-yn']='Y'; hdr['next-key']=next_key
                body={'stk_cd':code,'base_dt':datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'),'upd_stkpc_tp':'1'}
                r=requests.post(s.rest_base+'/api/dostk/chart',headers=hdr,json=body,timeout=25)
                d=r.json()
                if d.get('return_code') not in (None,0):
                    raise RuntimeError(f"ka10081 {code}: {d.get('return_code')} {d.get('return_msg')}")
                raw=d.get('stk_dt_pole_chart_qry') or d.get('stk_dt_chart_qry') or []
                if not raw:
                    for v in d.values():
                        if isinstance(v,list): raw=v; break
                rows.extend(raw or [])
                pages+=1
                cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
                next_key=r.headers.get('next-key') or r.headers.get('Next-Key') or ''
                if cont!='Y' or not next_key: break
            mon=_v28_monthly_from_daily(rows)
            return {'ok':len(mon)>=10,'market':market,'symbol':code,'source':'KIWOOM_KA10081','months':mon[-24:],'count':len(mon)}

        if market=='USA':
            mon,pages,source=_v35_us_history_with_local_fallback(symbol,'month')
            return {'ok':len(mon)>=10,'market':market,'symbol':symbol,'source':source,'months':mon[-36:],'count':len(mon),'pages':pages}

        raise HTTPException(status_code=400,detail='market must be USA or KOREA')
    except HTTPException:
        raise
    except Exception as e:
        return {'ok':False,'market':market,'symbol':symbol,'source':'KIWOOM','months':[],'count':0,'error':str(e)}






# ===== V35 USA HISTORY ROBUST FALLBACK =====
def _v35_us_daily_rows(symbol,days=1400,max_pages=24):
    from datetime import timedelta as _td
    symbol=str(symbol or '').upper().strip()
    ex=k.active_exchange(symbol)
    start=(datetime.now(timezone.utc)-_td(days=int(days))).strftime('%Y%m%d')
    rows=[]; next_key=''; pages=0; last_meta={}
    while pages<max_pages:
        hdr=k.headers('usa06012')
        if next_key:
            hdr['cont-yn']='Y'; hdr['next-key']=next_key
        r=requests.post(
            s.rest_base+'/api/us/chart',
            headers=hdr,
            json={'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'},
            timeout=25,
        )
        d=r.json(); last_meta=d if isinstance(d,dict) else {}
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"usa06012 {symbol}: {d.get('return_code')} {d.get('return_msg')}")
        raw=d.get('result_list') or d.get('data') or []
        if isinstance(raw,dict): raw=list(raw.values())
        rows.extend(x for x in raw if isinstance(x,dict))
        pages+=1
        cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or d.get('cont_yn') or d.get('cont-yn') or '').upper()
        nk=(r.headers.get('next-key') or r.headers.get('Next-Key') or d.get('next_key') or d.get('next-key') or '')
        next_key=str(nk or '')
        if cont!='Y' or not next_key:
            break
    return rows,pages,last_meta

def _v35_us_history_with_local_fallback(symbol,kind='month'):
    rows,pages,meta=_v35_us_daily_rows(symbol,days=(500 if kind=='month' else 180),max_pages=(8 if kind=='month' else 4))
    if kind=='month':
        built=_v28_monthly_from_daily(rows)
        if len(built)>=10:
            return built,pages,'KIWOOM_USA06012_PAGED'
    else:
        built=_v33_weekly_from_daily(rows)
        if len(built)>=10:
            return built,pages,'KIWOOM_USA06012_WEEKLY_PAGED'

    # Broker can return only a short window for some US names. Reuse the local
    # daily_history archive when available so evaluation does not become empty.
    try:
        con=sqlite3.connect(s.db_path,timeout=5)
        con.row_factory=sqlite3.Row
        cols=[r[1] for r in con.execute('PRAGMA table_info(daily_history)').fetchall()]
        sym_col=next((x for x in ('symbol','ticker','code') if x in cols),None)
        date_col=next((x for x in ('trade_date','date','day','ts','datetime') if x in cols),None)
        close_col=next((x for x in ('close','close_price','price','last_price') if x in cols),None)
        if sym_col and date_col and close_col:
            q=f'SELECT "{date_col}" as dt, "{close_col}" as cur_prc FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}"'
            local=[dict(r) for r in con.execute(q,(str(symbol).upper(),)).fetchall()]
        else:
            local=[]
        con.close()
    except Exception:
        local=[]

    if local:
        if kind=='month':
            built=_v28_monthly_from_daily(local)
            if len(built)>=10:
                return built,pages,'LOCAL_DB_DAILY_HISTORY'
        else:
            built=_v33_weekly_from_daily(local)
            if len(built)>=10:
                return built,pages,'LOCAL_DB_DAILY_HISTORY_WEEKLY'
    return built,pages,'KIWOOM_USA06012_INSUFFICIENT'

# ===== V34 USA DAILY HISTORY PAGINATION =====
def _v34_us_daily_rows(symbol,days=900,max_pages=12):
    from datetime import timedelta as _td
    symbol=str(symbol or '').upper().strip()
    ex=k.active_exchange(symbol)
    start=(datetime.now(timezone.utc)-_td(days=int(days))).strftime('%Y%m%d')
    rows=[]; next_key=''; pages=0
    while pages<max_pages:
        hdr=k.headers('usa06012')
        if next_key:
            hdr['cont-yn']='Y'
            hdr['next-key']=next_key
        r=requests.post(
            s.rest_base+'/api/us/chart',
            headers=hdr,
            json={'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'},
            timeout=25,
        )
        d=r.json()
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"usa06012 {symbol}: {d.get('return_code')} {d.get('return_msg')}")
        raw=d.get('result_list') or d.get('data') or []
        if isinstance(raw,dict):
            raw=list(raw.values())
        rows.extend(x for x in raw if isinstance(x,dict))
        pages+=1
        cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
        next_key=r.headers.get('next-key') or r.headers.get('Next-Key') or ''
        if cont!='Y' or not next_key:
            break
    return rows,pages

# ===== V33 LONG-TERM WEEKLY HISTORY FALLBACK =====
def _v33_weekly_from_daily(rows):
    from datetime import datetime as _dt
    cleaned=[]
    for x in rows or []:
        if not isinstance(x,dict):
            continue
        dt=str(x.get('dt') or x.get('date') or x.get('stk_dt') or x.get('base_dt') or '').strip().replace('-','')
        close=_v28_num(x.get('cur_prc') if x.get('cur_prc') is not None else x.get('close'))
        if len(dt)>=8 and close>0:
            try:
                d=_dt.strptime(dt[:8],'%Y%m%d').date()
            except Exception:
                continue
            cleaned.append((d,close))
    cleaned=sorted(cleaned,key=lambda z:z[0])
    by_week={}
    for d,close in cleaned:
        iso=d.isocalendar()
        key=f'{iso.year}-W{iso.week:02d}'
        by_week[key]=(d,close)
    out=[]
    for w,(d,close) in sorted(by_week.items(),key=lambda kv:kv[1][0]):
        out.append({'week':w,'date':d.strftime('%Y%m%d'),'close':close})
    return out

@app.get('/api/v5/weekly-history/{market}/{symbol}')
def v33_weekly_history(market:str,symbol:str):
    market=str(market or '').upper().strip()
    symbol=str(symbol or '').upper().strip()
    if not symbol:
        raise HTTPException(status_code=400,detail='symbol required')
    try:
        if market=='KOREA':
            code=symbol.split('_',1)[0]
            rows=[]; next_key=''; pages=0
            while pages<3:
                hdr=k.headers('ka10081')
                if next_key:
                    hdr['cont-yn']='Y'; hdr['next-key']=next_key
                body={'stk_cd':code,'base_dt':datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'),'upd_stkpc_tp':'1'}
                r=requests.post(s.rest_base+'/api/dostk/chart',headers=hdr,json=body,timeout=25)
                d=r.json()
                if d.get('return_code') not in (None,0):
                    raise RuntimeError(f"ka10081 {code}: {d.get('return_code')} {d.get('return_msg')}")
                raw=d.get('stk_dt_pole_chart_qry') or d.get('stk_dt_chart_qry') or []
                if not raw:
                    for v in d.values():
                        if isinstance(v,list): raw=v; break
                rows.extend(raw or [])
                pages+=1
                cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
                next_key=r.headers.get('next-key') or r.headers.get('Next-Key') or ''
                if cont!='Y' or not next_key: break
            weeks=_v33_weekly_from_daily(rows)
            return {'ok':len(weeks)>=10,'market':market,'symbol':code,'source':'KIWOOM_KA10081_WEEKLY','weeks':weeks[-80:],'count':len(weeks)}

        if market=='USA':
            weeks,pages,source=_v35_us_history_with_local_fallback(symbol,'week')
            return {'ok':len(weeks)>=10,'market':market,'symbol':symbol,'source':source,'weeks':weeks[-100:],'count':len(weeks),'pages':pages}

        raise HTTPException(status_code=400,detail='market must be USA or KOREA')
    except HTTPException:
        raise
    except Exception as e:
        return {'ok':False,'market':market,'symbol':symbol,'source':'KIWOOM_WEEKLY','weeks':[],'count':0,'error':str(e)}



# ===== V36 USA HISTORY DIAGNOSTICS =====
@app.get('/api/v5/history-debug/USA/{symbol}')
def v36_history_debug_usa(symbol:str):
    symbol=str(symbol or '').upper().strip()
    out={'ok':True,'symbol':symbol}
    try:
        rows,pages,meta=_v35_us_daily_rows(symbol)
        out['kiwoom_pages']=pages
        out['kiwoom_rows']=len(rows)
        out['kiwoom_first']=rows[0] if rows else None
        out['kiwoom_last']=rows[-1] if rows else None
        out['kiwoom_meta_keys']=sorted(list(meta.keys()))[:80] if isinstance(meta,dict) else []
    except Exception as e:
        out['kiwoom_error']=str(e)

    try:
        con=sqlite3.connect(s.db_path,timeout=5)
        con.row_factory=sqlite3.Row
        cols=[r[1] for r in con.execute('PRAGMA table_info(daily_history)').fetchall()]
        out['daily_history_cols']=cols
        sym_col=next((x for x in ('symbol','ticker','code') if x in cols),None)
        date_col=next((x for x in ('trade_date','date','day','ts','datetime') if x in cols),None)
        close_col=next((x for x in ('close','close_price','price','last_price') if x in cols),None)
        out['picked_cols']={'symbol':sym_col,'date':date_col,'close':close_col}
        if sym_col:
            out['db_symbol_count']=con.execute(f'SELECT COUNT(*) FROM daily_history WHERE UPPER("{sym_col}")=?',(symbol,)).fetchone()[0]
        else:
            out['db_symbol_count']=0
        if sym_col and date_col and close_col:
            q=f'SELECT "{date_col}" as dt, "{close_col}" as close FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}" LIMIT 3'
            out['db_first_rows']=[dict(r) for r in con.execute(q,(symbol,)).fetchall()]
            q2=f'SELECT "{date_col}" as dt, "{close_col}" as close FROM daily_history WHERE UPPER("{sym_col}")=? ORDER BY "{date_col}" DESC LIMIT 3'
            out['db_last_rows']=[dict(r) for r in con.execute(q2,(symbol,)).fetchall()]
        con.close()
    except Exception as e:
        out['db_error']=str(e)
    return out


@app.get('/api/v4/runtime-mode')
async def get_runtime_mode():
    return {'ok':True,**_runtime_profile(),'updated_at':runtime_mode.get('updated_at')}

@app.post('/api/v4/runtime-mode/{mode}')
async def set_runtime_mode(mode:str):
    m=str(mode or '').upper()
    if m not in ('NORMAL','DAYTRADE'):
        raise HTTPException(status_code=400,detail='mode must be NORMAL or DAYTRADE')
    runtime_mode['mode']=m
    runtime_mode['updated_at']=datetime.now(timezone.utc).isoformat()
    logging.warning('V4 runtime mode changed to %s',m)
    return {'ok':True,**_runtime_profile(),'updated_at':runtime_mode['updated_at']}


def _ensure_v5_holding_profile_table():
    con=sqlite3.connect(s.db_path,timeout=15)
    try:
        con.execute('''CREATE TABLE IF NOT EXISTS v5_holding_profiles(
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            holding_type TEXT NOT NULL DEFAULT 'SHORT_TERM',
            source TEXT NOT NULL DEFAULT 'MANUAL',
            updated_at TEXT,
            PRIMARY KEY(market,symbol)
        )''')
        con.commit()
    finally:
        con.close()


def _get_holding_profile(market:str,symbol:str):
    _ensure_v5_holding_profile_table()
    con=sqlite3.connect(s.db_path,timeout=15)
    con.row_factory=sqlite3.Row
    try:
        row=con.execute('SELECT * FROM v5_holding_profiles WHERE market=? AND symbol=?',
                        (market.upper(),symbol.upper())).fetchone()
        return dict(row) if row else {
            'market':market.upper(),'symbol':symbol.upper(),
            'holding_type':'SHORT_TERM','source':'LEGACY','updated_at':None
        }
    finally:
        con.close()


def _v5_pick_scalar(obj, keys):
    if isinstance(obj,dict):
        for k in keys:
            if k in obj and obj.get(k) not in (None,''):
                return obj.get(k)
        for v in obj.values():
            x=_v5_pick_scalar(v,keys)
            if x not in (None,''):
                return x
    elif isinstance(obj,list):
        for v in obj:
            x=_v5_pick_scalar(v,keys)
            if x not in (None,''):
                return x
    return None

def _v5_num(v):
    try:
        return abs(float(str(v).replace(',','').replace('+','').strip()))
    except Exception:
        return 0.0

def _v5_korea_quote_snapshot(code):
    code=str(code or '').strip().upper()
    q=korea.quote(code)
    raw=(q or {}).get('raw') or {}
    name=_v5_pick_scalar(raw,('stk_nm','stock_name','name')) or code
    price=_v5_num(_v5_pick_scalar(raw,('cur_prc','cur_price','current_price','last','close')))
    source='KIWOOM_KA10004'
    # Some ka10004 responses are order-book centric and may omit cur_prc.
    # In that case use the latest actual 1-minute close. This is also useful
    # after market close because it returns the last recorded trade price.
    if price<=0:
        try:
            bars=korea.minute_chart(code,1,1)
            latest=(bars or {}).get('latest') or {}
            price=_v5_num(latest.get('close'))
            if price>0:
                source='KIWOOM_KA10080_LAST_CLOSE'
        except Exception:
            pass
    return {'ok':True,'valid':True,'market':'KOREA','symbol':code,
            'name':str(name).strip() or code,'price':price,'source':source,
            'checked_at':(q or {}).get('checked_at')}

# V5.14: full Korean security master for human-friendly name/code search.
# Kiwoom ka10099 returns market security lists including ETFs/ETNs.
_v5_kr_master_cache={'ts':0.0,'rows':[]}

def _v5_korea_master(force=False):
    now=time.time()
    cache=_v5_kr_master_cache
    if (not force) and cache.get('rows') and now-float(cache.get('ts') or 0)<21600:
        return cache['rows']
    merged={}
    # KOSPI / KOSDAQ / ETF / ETN
    for mrkt_tp in ('0','10','8','60'):
        try:
            r=requests.post(
                k.s.rest_base+'/api/dostk/stkinfo',
                headers=k.headers('ka10099'),
                json={'mrkt_tp':mrkt_tp},
                timeout=30,
            )
            d=r.json()
            if d.get('return_code') not in (None,0):
                continue
            raw=d.get('list') or d.get('result_list') or d.get('data') or []
            if isinstance(raw,dict):
                raw=list(raw.values())
            for x in raw:
                if not isinstance(x,dict):
                    continue
                sym=str(x.get('code') or x.get('stk_cd') or x.get('symbol') or '').strip().upper()
                name=str(x.get('name') or x.get('stk_nm') or '').strip()
                if '_' in sym:
                    sym=sym.split('_',1)[0]
                m=re.match(r'^([0-9A-Z]{6})',sym)
                sym=m.group(1) if m else sym
                if len(sym)!=6 or not re.fullmatch(r'[0-9A-Z]{6}',sym):
                    continue
                if sym not in merged or (not merged[sym].get('name') and name):
                    merged[sym]={'symbol':sym,'name':name or sym,'market_type':mrkt_tp}
        except Exception as e:
            logging.warning('V5 korea master %s failed: %s',mrkt_tp,e)
    rows=list(merged.values())
    if rows:
        cache['ts']=now; cache['rows']=rows
        try:
            meta=getattr(korea,'stock_meta',None)
            if isinstance(meta,dict):
                for row in rows:
                    meta.setdefault(row['symbol'],row)
        except Exception:
            pass
    return rows or cache.get('rows') or []

def _v5_korea_detail_name(symbol):
    sym=str(symbol or '').strip().upper()
    if not re.fullmatch(r'[0-9A-Z]{6}',sym):
        return ''
    try:
        r=requests.post(
            k.s.rest_base+'/api/dostk/stkinfo',
            headers=k.headers('ka10100'),
            json={'stk_cd':sym},
            timeout=15,
        )
        d=r.json()
        if d.get('return_code') in (None,0):
            return str(d.get('name') or d.get('stk_nm') or '').strip()
    except Exception:
        pass
    return ''

@app.get('/api/v5/korea-symbol-search')
async def v5_korea_symbol_search(q:str,limit:int=12):
    q=str(q or '').strip().upper()
    if not q:
        return {'ok':True,'rows':[]}
    lim=max(1,min(int(limit),30))
    rows=[]; seen=set()
    master=await asyncio.to_thread(_v5_korea_master,False)
    # Exact code first.
    if re.fullmatch(r'[0-9A-Z]{6}',q):
        for r in master:
            if str(r.get('symbol') or '').upper()==q:
                rows.append({'symbol':q,'name':r.get('name') or q}); seen.add(q); break
        if q not in seen:
            name=await asyncio.to_thread(_v5_korea_detail_name,q)
            try:
                snap=await asyncio.to_thread(_v5_korea_quote_snapshot,q)
            except Exception:
                snap={}
            if name or snap.get('valid'):
                rows.append({'symbol':q,'name':name or snap.get('name') or q}); seen.add(q)
    # Human name / partial code search across the full Kiwoom master.
    for r in master:
        sym=str(r.get('symbol') or '').upper()
        name=str(r.get('name') or '')
        if not sym or sym in seen:
            continue
        if q in sym or q in name.upper():
            rows.append({'symbol':sym,'name':name or sym}); seen.add(sym)
            if len(rows)>=lim:
                break
    return {'ok':True,'rows':rows[:lim],'master_count':len(master)}

@app.get('/api/v5/symbol-validate/{market}/{query}')
async def validate_v5_symbol(market:str,query:str):
    market=str(market or '').upper().strip()
    q=str(query or '').strip().upper()
    if market not in ('USA','KOREA'):
        raise HTTPException(status_code=400,detail='market must be USA or KOREA')
    if not q:
        raise HTTPException(status_code=400,detail='symbol required')

    if market=='USA':
        import re as _re
        if not _re.fullmatch(r'[A-Z][A-Z0-9.\\-]{0,9}',q):
            return {'ok':False,'valid':False,'market':market,'query':q,'reason':'INVALID_US_SYMBOL_FORMAT'}
        row=db.quote(q) or {}
        if not row:
            try:
                ex=await asyncio.to_thread(k.active_exchange,q)
                await asyncio.to_thread(k.quote,q,ex)
                row=db.quote(q) or {}
            except Exception:
                row={}
        price=float(row.get('price') or row.get('last') or row.get('close') or 0)
        if price<=0:
            return {'ok':False,'valid':False,'market':market,'query':q,'reason':'SYMBOL_NOT_CONFIRMED'}
        return {'ok':True,'valid':True,'market':market,'symbol':q,'name':row.get('name') or q,'price':price}

    import re as _re
    if not _re.fullmatch(r'[0-9A-Z]{6}',q):
        return {'ok':False,'valid':False,'market':market,'query':q,'reason':'KOREA_REQUIRES_6_CHAR_CODE'}

    # Real Kiwoom validation: a valid listed code must be accepted even when it is
    # absent from the local tracker/history cache or the market is closed.
    try:
        snap=await asyncio.to_thread(_v5_korea_quote_snapshot,q)
    except Exception as e:
        return {'ok':False,'valid':False,'market':market,'query':q,
                'reason':'SYMBOL_NOT_CONFIRMED','detail':str(e)[:180]}
    if not snap.get('valid'):
        return {'ok':False,'valid':False,'market':market,'query':q,
                'reason':'SYMBOL_NOT_CONFIRMED','detail':snap.get('error')}
    return {'ok':True,'valid':True,'market':market,'symbol':q,
            'name':snap.get('name') or q,'price':snap.get('price') or 0,
            'source':snap.get('source') or 'KIWOOM'}

@app.get('/api/v5/korea-quote/{symbol}')
async def v5_korea_quote(symbol:str):
    q=str(symbol or '').strip().upper()
    import re as _re
    if not _re.fullmatch(r'[0-9A-Z]{6}',q):
        return {'ok':False,'valid':False,'symbol':q,'reason':'KOREA_REQUIRES_6_CHAR_CODE'}
    try:
        return await asyncio.to_thread(_v5_korea_quote_snapshot,q)
    except Exception as e:
        return {'ok':False,'valid':False,'symbol':q,'error':str(e)}

@app.get('/api/v5/holding-profile/{market}/{symbol}')
async def get_holding_profile(market:str,symbol:str):
    return {'ok':True,**_get_holding_profile(market,symbol)}


@app.post('/api/v5/holding-profile')
async def set_holding_profile(payload:dict):
    market=str(payload.get('market') or '').upper().strip()
    symbol=str(payload.get('symbol') or '').upper().strip()
    holding_type=str(payload.get('holding_type') or 'SHORT_TERM').upper().strip()
    source=str(payload.get('source') or 'MANUAL').upper().strip()
    if not market or not symbol:
        raise HTTPException(status_code=400,detail='market and symbol required')
    if holding_type not in ('SHORT_TERM','LONG_TERM'):
        raise HTTPException(status_code=400,detail='holding_type must be SHORT_TERM or LONG_TERM')
    _ensure_v5_holding_profile_table()
    now=datetime.now(timezone.utc).isoformat()
    con=sqlite3.connect(s.db_path,timeout=15)
    try:
        con.execute('''INSERT INTO v5_holding_profiles(market,symbol,holding_type,source,updated_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(market,symbol) DO UPDATE SET
                         holding_type=excluded.holding_type,
                         source=excluded.source,
                         updated_at=excluded.updated_at''',
                    (market,symbol,holding_type,source,now))
        con.commit()
    finally:
        con.close()
    return {'ok':True,**_get_holding_profile(market,symbol)}





@app.post('/api/korea/pulse/refresh')
async def korea_pulse_refresh(force:bool=False):
    try:
        return await asyncio.to_thread(korea.refresh_intraday_pulse,10,force)
    except Exception as e:
        logging.exception('KOREA intraday pulse refresh failed')
        raise HTTPException(500,str(e))

@app.get('/api/korea/pulse')
def korea_pulse():
    if not korea.intraday_pulse.get('updated_at'):
        return korea.refresh_intraday_pulse(10,False)
    return korea.intraday_pulse

@app.post('/api/korea/preopen/generate')
async def korea_preopen_generate():
    try:
        return await generate_korea_preopen_report(scheduled=False,label='MANUAL_PREOPEN')
    except Exception as e:
        logging.exception('manual KOREA preopen generation failed')
        raise HTTPException(500,str(e))


@app.get('/api/recommendations/final')
def final_recommendations_usa(limit:int=Query(5,ge=1,le=5)):
    candidates=screener_rows(db.quotes(),db.daily_metrics(),10)
    def _sig(sym):
        return multi_timeframe_signal(sym,db.ticks(sym,40000),db.quotes())
    return build_usa_final_recommendations(candidates,k.discovery,_sig,limit)

@app.get('/api/korea/recommendations/final')
def final_recommendations_korea(limit:int=Query(5,ge=1,le=5)):
    return build_korea_final_recommendations(korea.discovery,korea.intraday_pulse,limit)

@app.get('/api/korea/preopen/latest')
def korea_preopen_latest():
    x=preopen_store.latest('KOREA')
    if not x:
        raise HTTPException(404,'KOREA preopen briefing not available yet')
    return x

@app.get('/api/korea/preopen/history')
def korea_preopen_history(limit:int=Query(60,ge=1,le=500)):
    return {'data':preopen_store.history('KOREA',limit)}

@app.get('/api/korea/expected')
async def korea_expected_snapshot():
    try:
        return await asyncio.to_thread(korea.expected_execution_snapshot)
    except Exception as e:
        logging.exception('KOREA expected execution snapshot failed')
        raise HTTPException(500,str(e))

@app.post('/api/korea/scan')
async def korea_scan(limit:int=40):
    try:
        data=await asyncio.to_thread(korea.discover,limit)
        return {'ok':True,**data}
    except Exception as e:
        logging.exception('korea market scan failed')
        raise HTTPException(500,str(e))

@app.get('/api/korea/universe')
def korea_universe():
    return {'ok':True,**korea.discovery}

@app.get('/api/korea/top10')
def korea_top10():
    return {'ok':True,'model':'KOREA_CURRENT_V1_GAMMA','updated_at':korea.discovery.get('updated_at'),'data':korea.discovery.get('top10') or []}

@app.get('/api/korea/status')
def korea_status():
    return korea.status()

@app.get('/api/korea/quote/{stk_cd}')
def korea_quote(stk_cd:str):
    try:
        return korea.quote(stk_cd)
    except Exception as e:
        logging.exception('korea quote probe failed')
        raise HTTPException(500,str(e))


@app.post('/api/v4/USA/v209-pause-frozen')
def v209_pause_frozen(pause:bool=True):
    global _v209_pause_frozen_loop
    _v209_pause_frozen_loop=bool(pause)
    return {'ok':True,'pause':_v209_pause_frozen_loop}

@app.get('/api/v4/USA/frozen-paper')
def v171_usa_frozen_paper_status():
    return {'ok':True,'market':'USA','mode':'PAPER_ONLY','strategy':'WILLIAMS_FROZEN_V136',**_frozen_usa_paper_state}

@app.get('/api/v4/{market}/status')
def v4_status(market:str):
    market=market.upper()
    if market not in ('USA','KOREA'): raise HTTPException(400,'market must be USA or KOREA')
    return v4.status(market)

@app.get('/api/v4/{market}/finder')
def v4_finder(market:str):
    market=market.upper()
    if market=='USA': return v4.build_usa_finder(screener_rows(db.quotes(),db.daily_metrics(),40),k.discovery,5,db=db)
    if market=='KOREA': return v4.build_korea_finder(korea.discovery,5)
    raise HTTPException(400,'market must be USA or KOREA')

@app.get('/api/v4/{market}/tracker')
def v4_tracker(market:str):
    market=market.upper()
    if market=='USA':
        # V154C: request path must never run heavy refresh_usa_tracker().
        # Background v4_engine_forever already refreshes USA tracker.
        snap=getattr(v4,'tracker',{}).get('USA') if isinstance(getattr(v4,'tracker',None),dict) else None
        if snap is None:
            snap=getattr(v4,'tracker_state',{}).get('USA') if isinstance(getattr(v4,'tracker_state',None),dict) else None
        if snap is None:
            snap=getattr(v4,'last_tracker',{}).get('USA') if isinstance(getattr(v4,'last_tracker',None),dict) else None
        if snap is None:
            # Fallback: expose existing lightweight status rather than recalculating.
            try:
                st=v4.status('USA')
                return {'ok':True,'market':'USA','cached':True,'rows':(st or {}).get('tracker') or [],'status':st}
            except Exception:
                return {'ok':True,'market':'USA','cached':True,'rows':[]}
        if isinstance(snap,dict):
            out=dict(snap); out.setdefault('cached',True); out.setdefault('market','USA'); return out
        return {'ok':True,'market':'USA','cached':True,'rows':snap if isinstance(snap,list) else []}
    if market=='KOREA': return v4.refresh_korea_tracker(korea)
    raise HTTPException(400,'market must be USA or KOREA')


@app.post('/api/v4/korea/session-audit/backfill')
def v4_korea_session_audit_backfill(date:str|None=None):
    try:
        result=v4.store.backfill_korea_validation_from_snapshots(date)
        return {'ok':True,'date':date,**result}
    except Exception as e:
        logging.exception('KOREA session audit backfill failed')
        raise HTTPException(500,str(e))

@app.get('/api/v4/korea/session-audit')
def v4_korea_session_audit(date:str|None=None):
    try:
        return v4.store.korea_session_report(date)
    except Exception as e:
        logging.exception('KOREA session audit report failed')
        raise HTTPException(500,str(e))

@app.get('/api/v4/positions')
def v4_positions(market:str|None=None): return {'data':v4.store.positions(market)}

@app.post('/api/v4/position/buy')
async def v4_position_buy(payload:dict):
    try:return {'ok':True,'position':v4.store.buy(payload.get('market'),payload.get('symbol'),payload.get('qty'),payload.get('price'),payload.get('note') or '')}
    except Exception as e:raise HTTPException(400,str(e))

@app.post('/api/v4/position/sell')
async def v4_position_sell(payload:dict):
    try:return {'ok':True,**v4.store.sell(payload.get('market'),payload.get('symbol'),payload.get('qty'),payload.get('price'),payload.get('note') or '')}
    except Exception as e:raise HTTPException(400,str(e))

@app.get('/api/v4/events')
def v4_events(market:str|None=None,limit:int=Query(50,ge=1,le=500)): return {'data':v4.store.events(market,limit)}
@app.get('/api/v4/trades')
def v4_trades(market:str|None=None,limit:int=Query(200,ge=1,le=1000)): return {'data':v4.store.trades(market,limit)}
@app.get('/api/v4/validation/snapshots')
def v4_validation_snapshots(market:str|None=None,limit:int=Query(500,ge=1,le=5000)): return {'data':v4.store.snapshots(market,limit),'note':'Baseline V4 feature snapshots for Historical/Shadow calibration.'}


@app.get('/api/v4/coverage-audit')
def v4_coverage_audit(market:str='USA'):
    market=str(market or 'USA').upper()
    if market!='USA':
        return {'market':market,'supported':False,'note':'V4.6.0 coverage audit is USA-first until verified Korea minute/discovery coverage is available.'}

    def _rows(x,key='rows'):
        return (x or {}).get(key) or []

    def _symset(rows):
        return {str(r.get('symbol') or '').upper() for r in (rows or []) if r.get('symbol')}

    def _age_seconds(row):
        raw=row.get('updated_at') or row.get('ts') or row.get('last_ts')
        if not raw:return None
        try:
            t=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
            if t.tzinfo is None:t=t.replace(tzinfo=timezone.utc)
            return round(max(0,(datetime.now(timezone.utc)-t).total_seconds()),1)
        except Exception:return None

    # Session-aware freshness semantics.
    et_now=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    et_min=et_now.hour*60+et_now.minute
    market_open=bool(
        et_now.weekday()<5
        and (9*60+30) <= et_min < (16*60)
    )
    session_mode='REGULAR' if market_open else 'MARKET_CLOSED_REFERENCE'

    discovery=k.discovery if isinstance(getattr(k,'discovery',None),dict) else {}
    discovery_rows=_rows(discovery)
    extreme_rows=_rows(discovery,'extreme_rows')
    quality_risk_rows=_rows(discovery,'quality_risk_rows')
    quality_reject_rows=_rows(discovery,'quality_reject_rows')

    screen_rows=screener_rows(db.quotes(),db.daily_metrics(),40)
    finder_obj=(getattr(v4,'finder',{}) or {}).get('USA') or {}
    finder_rows=_rows(finder_obj)
    light_rows=_rows(finder_obj,'light_rows')
    tracker_obj=(getattr(v4,'tracker',{}) or {}).get('USA') or {}
    tracker_rows=_rows(tracker_obj)

    ds=_symset(discovery_rows)
    es=_symset(extreme_rows)
    qrs=_symset(quality_risk_rows)
    ss=_symset(screen_rows)
    ls=_symset(light_rows)
    fs=_symset(finder_rows)
    hs=_symset(tracker_rows)

    dmap={str(r.get('symbol') or '').upper():r for r in discovery_rows}
    emap={str(r.get('symbol') or '').upper():r for r in extreme_rows}
    qrmap={str(r.get('symbol') or '').upper():r for r in quality_risk_rows}
    smap={str(r.get('symbol') or '').upper():r for r in screen_rows}
    lmap={str(r.get('symbol') or '').upper():r for r in light_rows}
    fmap={str(r.get('symbol') or '').upper():r for r in finder_rows}
    hmap={str(r.get('symbol') or '').upper():r for r in tracker_rows}

    quote_rows=db.quotes()
    qmap={str(r.get('symbol') or '').upper():r for r in quote_rows}

    def stage(sym):
        if sym in hs:return 'HEAVY5'
        if sym in fs:return 'FINDER'
        if sym in ls:return 'LIGHT'
        if sym in es and sym in ds:return 'EXTREME_WATCH'
        if sym in es:return 'EXTREME'
        if sym in ds:return 'DISCOVERY'
        if sym in qrs:return 'QUALITY_RISK'
        if sym in ss:return 'SCREENER'
        return 'NOT_SEEN'

    def reason(sym):
        if sym in hs:return 'Heavy Tracker active'
        if sym in fs:return 'Finder TOP5 selected'
        if sym in ls:
            r=lmap.get(sym) or {}
            return f"Light only · score={r.get('finder_score',r.get('score'))} · fresh={r.get('fresh_mode')}"
        if sym in es:
            r=emap.get(sym) or dmap.get(sym) or {}
            return (
                f"Extreme mover · quality={r.get('quality_grade','C_HIGH_RISK')} · "
                f"risk={r.get('chase_risk','EXTREME')} · "
                + ('active watch universe' if sym in ds else 'separate extreme audit row')
            )
        if sym in ds:
            r=dmap.get(sym) or {}
            grade=r.get('quality_grade')
            origin=r.get('origin')
            risk=r.get('chase_risk')
            return f"Discovery only · origin={origin} · quality={grade} · risk={risk}"
        if sym in qrs:
            r=qrmap.get(sym) or {}
            return f"Quality risk · grade={r.get('quality_grade')} · reason={r.get('quality_reasons')}"
        if sym in ss:
            r=smap.get(sym) or {}
            return f"Screener only · score={r.get('score')} · eligible={r.get('eligible')}"
        return 'Not present in current discovery/extreme/screener snapshots'

    # Current discovery-source coverage.
    source_counts={}
    for r in discovery_rows:
        src=r.get('sources')
        if isinstance(src,str):
            parts=[x.strip() for x in src.split(',') if x.strip()]
        elif isinstance(src,(set,list,tuple)):
            parts=[str(x).strip() for x in src if str(x).strip()]
        else:
            parts=[]
        for s0 in parts:
            source_counts[s0]=source_counts.get(s0,0)+1

    # Best current positive/negative movers among rows we actually know about.
    union={}
    for rows in (screen_rows,discovery_rows,extreme_rows,light_rows,finder_rows,tracker_rows):
        for r in rows or []:
            sym=str(r.get('symbol') or '').upper()
            if not sym:continue
            cur=union.setdefault(sym,{})
            cur.update({k:v for k,v in r.items() if v is not None})
            cur['symbol']=sym

    movers=[]
    for sym,r in union.items():
        try:chg=float(r.get('change_pct') or 0)
        except Exception:chg=0.0
        try:price=float(r.get('price') or (qmap.get(sym) or {}).get('price') or 0)
        except Exception:price=0.0
        movers.append({
            'symbol':sym,
            'name':r.get('name') or (qmap.get(sym) or {}).get('name') or '',
            'change_pct':round(chg,3),
            'price':price,
            'stage':stage(sym),
            'reason':reason(sym),
            'quality':r.get('quality_grade'),
            'origin':r.get('origin'),
            'fresh':r.get('fresh_mode'),
            'finder_score':r.get('finder_score'),
            'power':(hmap.get(sym) or {}).get('power'),
            'data_age_sec':_age_seconds(qmap.get(sym) or r),
        })
    movers.sort(key=lambda r:abs(r['change_pct']),reverse=True)

    inverse=[]
    core_etfs={'SOXS','SQQQ','SOXL','TQQQ'}
    for sym in ('SOXS','SQQQ','SOXL','TQQQ'):
        meta=hmap.get(sym) or fmap.get(sym) or lmap.get(sym) or dmap.get(sym) or emap.get(sym) or qrmap.get(sym) or smap.get(sym) or {}
        q=qmap.get(sym) or {}

        # V4.6.2.2: metadata determines pipeline stage, but latest quote wins for
        # market fields when Discovery carries placeholder 0/None values.
        def _live_num(key):
            try:
                qv=float(q.get(key) or 0)
            except Exception:
                qv=0.0
            if qv!=0:
                return qv
            try:
                return float(meta.get(key) or 0)
            except Exception:
                return 0.0

        inverse.append({
            'symbol':sym,
            'stage':stage(sym),
            'change_pct':_live_num('change_pct'),
            'price':_live_num('price'),
            'finder_score':(fmap.get(sym) or lmap.get(sym) or {}).get('finder_score'),
            'fresh':(fmap.get(sym) or lmap.get(sym) or {}).get('fresh_mode'),
            'power':(hmap.get(sym) or {}).get('power'),
            'quote_age_sec':_age_seconds(q) if q else None,
            'reason':reason(sym),
        })

    # Stale severity:
    # Critical = current live decision universe OR current Bridge warm targets OR core ETFs.
    # Inactive cache = old DB quote outside the decision universe.
    bridge_candidates=[]
    for r in screen_rows:
        sym=str(r.get('symbol') or '').upper()
        if not sym or not r.get('eligible') or sym in ds or sym in es or sym in qrs:
            continue
        bridge_candidates.append(r)
    bridge_candidates.sort(
        key=lambda r:(float(r.get('score') or 0),abs(float(r.get('change_pct') or 0))),
        reverse=True
    )
    bridge_syms={str(r.get('symbol') or '').upper() for r in bridge_candidates[:8]}
    critical_syms=set(fs)|set(hs)|bridge_syms|core_etfs

    critical_stale=[]
    reference_stale=[]
    inactive_stale=[]
    for sym,q in qmap.items():
        age=_age_seconds(q)
        if age is None or age<=180:
            continue
        if sym in critical_syms:
            row={
                'symbol':sym,'age_sec':age,'stage':stage(sym),'price':q.get('price'),
                'severity':'CRITICAL' if market_open else 'REFERENCE',
                'session_mode':session_mode
            }
            if market_open:
                critical_stale.append(row)
            else:
                reference_stale.append(row)
        else:
            inactive_stale.append({
                'symbol':sym,'age_sec':age,'stage':stage(sym),'price':q.get('price'),
                'severity':'INACTIVE_CACHE','session_mode':session_mode
            })
    critical_stale.sort(key=lambda x:x['age_sec'],reverse=True)
    reference_stale.sort(key=lambda x:x['age_sec'],reverse=True)
    inactive_stale.sort(key=lambda x:x['age_sec'],reverse=True)

    warm_rows=[]
    for sym in sorted(bridge_syms|core_etfs):
        st=dict(bridge_warm_status.get(sym) or {})
        q=qmap.get(sym) or {}
        bars=len(ticks_to_bars(db.ticks(sym,2500),1))
        price=float(q.get('price') or 0)
        if not st:
            st={
                'symbol':sym,'status':'PENDING' if sym in bridge_syms else 'OBSERVED',
                'last_attempt':None,'exchange':None,
                'quote_ok':bool(price>0),
                'daily_ok':bool((db.daily_metrics(sym) or {})),
                'minute_ok':bool(bars>=6),
                'minute_bars':bars,'inserted':None,
                'failed_step':None,'error_short':None,'error':None
            }
        else:
            st['minute_bars']=bars
            st['quote_ok']=bool(price>0) or bool(st.get('quote_ok'))
            st['minute_ok']=bool(bars>=6) or bool(st.get('minute_ok'))
        st['price']=price
        st['quote_age_sec']=_age_seconds(q) if q else None
        st['ready_now']=bool(price>0 and bars>=6)
        if st['ready_now'] and not st.get('daily_ok') and st.get('status') in ('FAILED','PARTIAL','MINUTE_FAILED','RUNNING'):
            st['status']='READY_DAILY_WARN'
        warm_rows.append(st)

    # V4.6.1: explain Light -> Finder cutline without changing selection logic.
    finder_cut=min([float(r.get('finder_score') or 0) for r in finder_rows],default=0.0)
    light_audit=[]
    for r in light_rows:
        sym=str(r.get('symbol') or '').upper()
        score=float(r.get('finder_score') or 0)
        selected=sym in fs
        gap=round(score-finder_cut,1) if finder_cut else None
        why=[]
        if selected:
            why.append('Finder TOP5')
        else:
            if r.get('quality')=='C_HIGH_RISK' and not r.get('extreme_continue'):
                why.append('Extreme continuation 미충족')
            if gap is not None and gap<0:
                why.append(f'Finder 컷 대비 {gap:+.1f}')
            if str(r.get('fresh_mode') or 'WATCH')=='WATCH':
                why.append('Fresh WATCH')
            if not r.get('break_3m_high'):
                why.append('3분 고가돌파 없음')
            if float(r.get('ret_5m') or 0)<=0:
                why.append('5분 모멘텀 비양수')
            if float(r.get('volume_accel') or 0)<1.10:
                why.append('거래량 가속 <1.10x')
            if float(r.get('fade_penalty') or 0)>0:
                why.append(f"fade -{float(r.get('fade_penalty') or 0):.1f}")
        light_audit.append({
            'light_rank':r.get('light_rank'),
            'symbol':sym,
            'name':r.get('name'),
            'finder_score':score,
            'finder_cut':round(finder_cut,1) if finder_cut else None,
            'gap_to_cut':gap,
            'selected':selected,
            'quality':r.get('quality'),
            'fresh':r.get('fresh_mode'),
            'fresh_score':r.get('fresh_score'),
            'ret_1m':r.get('ret_1m'),'ret_3m':r.get('ret_3m'),
            'ret_5m':r.get('ret_5m'),'ret_15m':r.get('ret_15m'),
            'volume_accel':r.get('volume_accel'),
            'break_3m_high':r.get('break_3m_high'),
            'fade_penalty':r.get('fade_penalty'),
            'extreme_continue':r.get('extreme_continue'),
            'reason':' · '.join(why) if why else '컷라인 경쟁'
        })

    # Screener names that are absent from current Discovery/Extreme/Risk snapshots.
    # We can identify the mismatch, but do not invent a missing upstream TR reason.
    discovery_miss=[]
    for r in screen_rows:
        sym=str(r.get('symbol') or '').upper()
        if sym in ds or sym in es or sym in qrs:
            continue
        penalties=r.get('penalties') or []
        discovery_miss.append({
            'symbol':sym,
            'score':r.get('score'),
            'change_pct':r.get('change_pct'),
            'eligible':r.get('eligible'),
            'extreme':r.get('extreme'),
            'rvol':r.get('rvol'),
            'atr_pct':r.get('atr_pct'),
            'dollar_volume':r.get('dollar_volume'),
            'penalties':' / '.join(str(x) for x in penalties),
            'diagnosis':'Screener에는 존재하지만 현재 Discovery/Extreme/Risk snapshot에는 없음 · upstream source/ranking/eligibility 원인은 현재 데이터만으로 확정 불가'
        })
    discovery_miss.sort(key=lambda r:abs(float(r.get('change_pct') or 0)),reverse=True)

    return {
        'market':'USA',
        'supported':True,
        'updated_at':datetime.now(timezone.utc).isoformat(),
        'counts':{
            'quotes':len(quote_rows),
            'screener40':len(screen_rows),
            'discovery':len(discovery_rows),
            'extreme':len(extreme_rows),
            'quality_risk':len(quality_risk_rows),
            'quality_reject':len(quality_reject_rows),
            'light':len(light_rows),
            'finder':len(finder_rows),
            'heavy':len(tracker_rows),
        },
        'source_counts':source_counts,
        'inverse':inverse,
        'top_abs_movers':movers[:25],
        'session_mode':session_mode,
        'market_open':market_open,
        'critical_stale_rows':critical_stale[:30],
        'reference_stale_rows':reference_stale[:30],
        'inactive_stale_rows':inactive_stale[:30],
        'critical_stale_count':len(critical_stale),
        'reference_stale_count':len(reference_stale),
        'inactive_stale_count':len(inactive_stale),
        'bridge_warm_symbols':sorted(bridge_syms),
        'bridge_warm_status':warm_rows,
        'finder_cut':round(finder_cut,1) if finder_cut else None,
        'light_audit':light_audit,
        'discovery_miss':discovery_miss[:30],
        'finder_symbols':sorted(fs),
        'light_symbols':sorted(ls),
        'heavy_symbols':sorted(hs),
        'note':'Coverage diagnostic only. It does not change Finder/Power/ENTRY logic or place orders.'
    }



@app.get('/api/v4/discovery-bridge-shadow')
def v4_discovery_bridge_shadow(market:str='USA'):
    market=str(market or 'USA').upper()
    if market!='USA':
        return {
            'market':market,'supported':False,
            'note':'Discovery Bridge Shadow is USA-first until verified Korea discovery/minute coverage is available.'
        }

    candidates=screener_rows(db.quotes(),db.daily_metrics(),40)
    discovery=k.discovery if isinstance(getattr(k,'discovery',None),dict) else {}
    live=(getattr(v4,'finder',{}) or {}).get('USA') or {}

    # Same Finder formula, no state mutation / no TOP5 events.
    shadow=v4.build_usa_finder(
        candidates,discovery,5,db=db,
        commit=False,shadow_allow_unknown_quality=True,
        shadow_min_recent_bars=6
    )

    live_rows=live.get('rows') or []
    live_light=live.get('light_rows') or []
    shadow_rows=shadow.get('rows') or []
    shadow_light=shadow.get('light_rows') or []

    live_f={str(r.get('symbol') or '').upper():r for r in live_rows}
    live_l={str(r.get('symbol') or '').upper():r for r in live_light}
    sh_f={str(r.get('symbol') or '').upper():r for r in shadow_rows}
    sh_l={str(r.get('symbol') or '').upper():r for r in shadow_light}

    dset={str(r.get('symbol') or '').upper() for r in (discovery.get('rows') or []) if r.get('symbol')}
    eset={str(r.get('symbol') or '').upper() for r in (discovery.get('extreme_rows') or []) if r.get('symbol')}
    qrset={str(r.get('symbol') or '').upper() for r in (discovery.get('quality_risk_rows') or []) if r.get('symbol')}

    misses=[]
    for c in candidates:
        sym=str(c.get('symbol') or '').upper()
        if not sym or sym in dset or sym in eset or sym in qrset:
            continue
        srow=sh_l.get(sym) or sh_f.get(sym)
        recent_bars=int((srow or {}).get('recent_bars') or 0)
        price=float((srow or {}).get('price') or (db.quote(sym) or {}).get('price') or 0)
        data_ready=bool(recent_bars>=6 and price>0)
        misses.append({
            'symbol':sym,
            'screener_score':c.get('score'),
            'change_pct':c.get('change_pct'),
            'eligible':c.get('eligible'),
            'rvol':c.get('rvol'),
            'atr_pct':c.get('atr_pct'),
            'dollar_volume':c.get('dollar_volume'),
            'shadow_light_rank':(srow or {}).get('light_rank'),
            'shadow_finder_rank':(sh_f.get(sym) or {}).get('rank'),
            'shadow_finder_score':(srow or {}).get('finder_score'),
            'shadow_quality':(srow or {}).get('quality'),
            'price':price,
            'recent_bars':recent_bars,
            'data_ready':data_ready,
            'fair_status':'READY' if data_ready else 'INSUFFICIENT_DATA',
            'fresh':(srow or {}).get('fresh_mode'),
            'fresh_score':(srow or {}).get('fresh_score'),
            'ret_1m':(srow or {}).get('ret_1m'),
            'ret_3m':(srow or {}).get('ret_3m'),
            'ret_5m':(srow or {}).get('ret_5m'),
            'ret_15m':(srow or {}).get('ret_15m'),
            'volume_accel':(srow or {}).get('volume_accel'),
            'break_3m_high':(srow or {}).get('break_3m_high'),
            'would_reach_light':sym in sh_l,
            'would_reach_finder':sym in sh_f,
            'note':(
                'Finder Shadow TOP5 · fair data ready' if sym in sh_f else
                'Light Shadow only · fair data ready' if sym in sh_l and data_ready else
                'Light에는 보이나 데이터 준비 부족' if sym in sh_l else
                'Shadow에서도 Light20 미진입'
            )
        })

    misses.sort(
        key=lambda r:(
            1 if r.get('would_reach_finder') else 0,
            1 if r.get('would_reach_light') else 0,
            float(r.get('shadow_finder_score') or -1),
            float(r.get('screener_score') or 0)
        ),
        reverse=True
    )

    live_syms=[str(r.get('symbol') or '').upper() for r in live_rows]
    shadow_syms=[str(r.get('symbol') or '').upper() for r in shadow_rows]
    new_shadow=[s for s in shadow_syms if s not in live_syms]
    displaced=[s for s in live_syms if s not in shadow_syms]

    comparison=[]
    for i in range(max(len(live_rows),len(shadow_rows))):
        lr=live_rows[i] if i<len(live_rows) else {}
        sr=shadow_rows[i] if i<len(shadow_rows) else {}
        comparison.append({
            'rank':i+1,
            'live_symbol':lr.get('symbol'),
            'live_score':lr.get('finder_score'),
            'shadow_symbol':sr.get('symbol'),
            'shadow_score':sr.get('finder_score'),
            'shadow_unknown_quality':sr.get('shadow_quality_unknown'),
        })

    return {
        'market':'USA','supported':True,
        'updated_at':datetime.now(timezone.utc).isoformat(),
        'live_finder':live_syms,
        'shadow_finder':shadow_syms,
        'new_shadow_entrants':new_shadow,
        'displaced_live':displaced,
        'comparison':comparison,
        'miss_rows':misses,
        'shadow_light_count':len(shadow_light),
        'data_ready_misses':sum(1 for r in misses if r.get('data_ready')),
        'insufficient_data_misses':sum(1 for r in misses if not r.get('data_ready')),
        'core_etf_readiness':[
            {
                'symbol':sym,
                'price':float((db.quote(sym) or {}).get('price') or 0),
                'minute_bars':len(ticks_to_bars(db.ticks(sym,2500),1)),
                'ready':bool(
                    float((db.quote(sym) or {}).get('price') or 0)>0
                    and len(ticks_to_bars(db.ticks(sym,2500),1))>=6
                )
            }
            for sym in ('SOXS','SQQQ','SOXL','TQQQ')
        ],
        'fair_guard':'SHADOW_UNKNOWN requires recent_bars >= 6 before Shadow Finder eligibility',
        'unknown_quality_policy':'Screener eligible + missing verified discovery quality => SHADOW_UNKNOWN, quality bonus 0',
        'note':'Shadow only. Candidate data is warmed separately; live Finder state/event/order is unchanged.'
    }


@app.get('/api/v4/validation/marks')
def v4_validation_marks(market:str|None=None,limit:int=Query(1000,ge=1,le=5000)):
    return {'data':v4.store.validation_marks(market,limit),'note':'Forward-return marks: +5/+15/+30/+60m and MFE/MAE. Heuristic diagnostics, not probabilities.'}

@app.get('/api/v4/validation/episodes')
def v4_validation_episodes(market:str|None=None,limit:int=Query(5000,ge=1,le=10000),bridge_minutes:int=Query(5,ge=1,le=15)):
    return {
        'data':v4.store.validation_episodes(market,limit,bridge_minutes),
        'note':'Signal episodes derived from minute validation snapshots. Brief inactive flickers are bridged; episode count is closer to independent signal cycles than raw snapshot count.'
    }

@app.get('/api/v4/validation/stage-anchors')
def v4_validation_stage_anchors(market:str|None=None,limit:int=Query(5000,ge=1,le=10000),bridge_minutes:int=Query(5,ge=1,le=15)):
    return {
        'data':v4.store.validation_stage_anchors(market,limit,bridge_minutes),
        'note':'First SETUP / READY / ENTRY marks within each signal Episode, with their own forward returns and MFE/MAE.'
    }

@app.get('/api/v4/validation/entry-shadow')
def v4_validation_entry_shadow(market:str|None=None,limit:int=Query(5000,ge=1,le=10000),bridge_minutes:int=Query(5,ge=1,le=15)):
    return v4.store.validation_entry_shadow(market,limit,bridge_minutes)

@app.get('/health')
def health():
    qs=db.quotes()
    return {'ok':True,'mode':'LIVE','version':'4.0','hotfix':'scan-3','symbols':s.symbols,'quotes':len(qs),'daily_metrics':len(db.daily_metrics()),'db':s.db_path,
        'news_ai_configured': bool(os.getenv('OPENAI_API_KEY')),
        'news_ai_model': os.getenv('DAYTRADER_NEWS_AI_MODEL') or 'gpt-5'}

@app.get('/api/quotes')
def quotes(): return db.quotes()

@app.get('/api/quote/{symbol}')
def quote(symbol:str):
    q=db.quote(symbol)
    if not q: raise HTTPException(404,'quote not available yet')
    m=db.daily_metric(symbol) or {}; return {**q,**m}

@app.get('/api/market-context/{symbol}')
def market_context(symbol:str):
    _,_,ctx=context_for(symbol.upper(),db.quotes()); return ctx

@app.get('/api/screener')
def screener(top_n:int=Query(10,ge=1,le=30)):
    return {'data':screener_rows(db.quotes(),db.daily_metrics(),top_n),'updated_at':datetime.now(timezone.utc).isoformat()}

@app.get('/api/screener/shadow')
def screener_shadow(top_n:int=Query(10,ge=1,le=30)):
    return {
        'data':shadow_screener_rows(db.quotes(),db.daily_metrics(),top_n),
        'model':'LIVE_CANDIDATE_V1',
        'experimental':True,
        'updated_at':datetime.now(timezone.utc).isoformat()
    }

@app.get('/api/screener/compare')
def screener_compare(top_n:int=Query(10,ge=1,le=30)):
    x=compare_current_shadow(db.quotes(),db.daily_metrics(),top_n)
    x['updated_at']=datetime.now(timezone.utc).isoformat()
    return x

@app.get('/api/ranking-history')
def ranking_history(): return {'data':db.ranking_history()}


@app.get('/api/archive/dates')
def archive_dates(limit:int=Query(120,ge=1,le=500)):
    return {'data':archive.dates(limit)}

@app.get('/api/archive/snapshots')
def archive_snapshots(trade_date:str):
    return {'data':archive.snapshots(trade_date)}

@app.get('/api/archive/ranking')
def archive_ranking(trade_date:str,label:str,model:str='CURRENT'):
    x=archive.ranking(trade_date,label,model)
    if not x: raise HTTPException(404,'archived ranking not found')
    return x

@app.get('/api/archive/recent')
def archive_recent(limit:int=Query(50,ge=1,le=500)):
    return {'data':archive.recent(limit)}

@app.get('/api/archive/save-now')
def archive_save_now(label:str='MANUAL'):
    now=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    day=now.strftime('%Y-%m-%d')
    rows=screener_rows(db.quotes(),db.daily_metrics(),10)
    if not rows: raise HTTPException(409,'screener rows not ready')
    captured=datetime.now(timezone.utc).isoformat()
    qmap={q.get('symbol'):q for q in db.quotes()}
    meta_id=archive.save(day,label,'CURRENT',rows,captured,
                         float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                         float((qmap.get('SMH') or {}).get('change_pct') or 0))
    shadow_rows=shadow_screener_rows(db.quotes(),db.daily_metrics(),10)
    shadow_id=None
    if shadow_rows:
        shadow_id=archive.save(day,label,'SHADOW',shadow_rows,captured,
                               float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                               float((qmap.get('SMH') or {}).get('change_pct') or 0))
    return {'ok':True,'id':meta_id,'shadow_id':shadow_id,'trade_date':day,'label':label.upper(),
            'models':['CURRENT','SHADOW'],'rows':len(rows),'shadow_rows':len(shadow_rows)}

@app.get('/api/bars/{symbol}')
def bars(symbol:str,minutes:int=Query(1,ge=1,le=60),limit:int=Query(200,ge=10,le=1000)):
    out=ticks_to_bars(db.ticks(symbol,40000),minutes).tail(limit)
    return {'symbol':symbol.upper(),'minutes':minutes,'data':out.assign(time=out['time'].astype(str)).to_dict('records')}

@app.get('/api/signal/{symbol}')
def signal(symbol:str):
    return multi_timeframe_signal(symbol.upper(),db.ticks(symbol,40000),db.quotes())

@app.get('/api/position/{symbol}')
def position(symbol:str,entry:float=Query(...,gt=0),side:str=Query('LONG',pattern='^(LONG|SHORT)$')):
    return position_from_ticks(symbol.upper(),db.ticks(symbol,40000),entry,side,db.quotes())

@app.get('/api/raw')
def raw(limit:int=20): return db.raw(min(limit,100))


@app.get('/api/universe')
def universe():
    return k.discovery



@app.post('/api/scan/market')
async def scan_market_now():
    cooldown=int(getattr(s,'manual_scan_cooldown_seconds',45))
    now=time.monotonic()
    elapsed=now-float(manual_scan_state.get('last_started_monotonic') or 0)
    if elapsed < cooldown:
        return {
            'ok':False,'cooldown':True,
            'retry_after':round(cooldown-elapsed,1),
            'last_result':manual_scan_state.get('last_result')
        }

    manual_scan_state['last_started_monotonic']=now
    before_top=screener_rows(db.quotes(),db.daily_metrics(),10)
    before_syms=[x.get('symbol') for x in before_top]

    res=await k.manual_discover_now()

    # Recalculate with whatever data is available after priming.
    after_top=screener_rows(db.quotes(),db.daily_metrics(),10)
    after_syms=[x.get('symbol') for x in after_top]
    changed=[x for x in after_syms if x not in before_syms]

    ny=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    label='MANUAL_SCAN_'+ny.strftime('%H%M')
    captured=datetime.now(timezone.utc).isoformat()
    qmap={q.get('symbol'):q for q in db.quotes()}
    if after_top:
        archive.save(
            ny.strftime('%Y-%m-%d'),label,'CURRENT',after_top,captured,
            float((qmap.get('QQQ') or {}).get('change_pct') or 0),
            float((qmap.get('SMH') or {}).get('change_pct') or 0)
        )
        shadow_top=shadow_screener_rows(db.quotes(),db.daily_metrics(),10)
        if shadow_top:
            archive.save(
                ny.strftime('%Y-%m-%d'),label,'SHADOW',shadow_top,captured,
                float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                float((qmap.get('SMH') or {}).get('change_pct') or 0)
            )

    out={
        **res,
        'cooldown_seconds':cooldown,
        'archive_label':label,
        'top10_before':before_syms,
        'top10_after':after_syms,
        'top10_new':changed
    }
    manual_scan_state['last_result']=out
    return out

@app.get('/api/scan/status')
def scan_status():
    cooldown=int(getattr(s,'manual_scan_cooldown_seconds',45))
    elapsed=time.monotonic()-float(manual_scan_state.get('last_started_monotonic') or 0)
    return {
        'cooldown_seconds':cooldown,
        'retry_after':max(0,round(cooldown-elapsed,1)),
        'last_result':manual_scan_state.get('last_result'),
        'last_manual_scan_at':getattr(k,'last_manual_scan_at',None)
    }


@app.post('/api/briefing/generate')
async def briefing_generate(market:str='USA'):
    market=market.upper()
    if market!='USA':
        raise HTTPException(501,'KOREA briefing requires the Korean-market data adapter; scheduled schema is already prepared')
    job,created=await _start_manual_briefing_job(market)
    return {'ok':True,'accepted':True,'created':created,**_briefing_job_view(job)}


@app.post('/api/briefing/retry-failed')
async def briefing_retry_failed(market:str='USA'):
    market=market.upper()
    if market!='USA':
        raise HTTPException(501,'KOREA briefing retry requires the Korean-market adapter')
    latest=preopen_store.latest('USA') or {}
    rows=latest.get('rows') or []
    failed=[r.get('symbol') for r in rows if r.get('news_symbol_status')=='ERROR']
    if not failed:
        return {'ok':True,'accepted':False,'reason':'NO_FAILED_NEWS_SYMBOLS','failed_symbols':[]}
    job,created=await _start_manual_briefing_job(market)
    job['label']='MANUAL_RETRY_FAILED'
    job['detail']='Retry requested after failed News AI symbols: '+','.join(failed)
    return {'ok':True,'accepted':True,'created':created,'failed_symbols':failed,**_briefing_job_view(job)}

@app.get('/api/briefing/job/{job_id}')
def briefing_job(job_id:str):
    job=briefing_jobs.get(job_id)
    if not job:
        raise HTTPException(404,'briefing job not found')
    return {'ok':True,**_briefing_job_view(job)}

@app.get('/api/briefing/job-active/{market}')
def briefing_job_active(market:str='USA'):
    market=market.upper()
    active=None
    # newest active job wins
    for j in reversed(list(briefing_jobs.values())):
        if j.get('market')==market and j.get('status') in ('QUEUED','RUNNING'):
            active=j
            break
    return {'ok':True,'active':bool(active),'job':_briefing_job_view(active) if active else None}

@app.get('/api/briefing/latest')
def briefing_latest(market:str='USA'):
    x=preopen_store.latest(market)
    if not x: raise HTTPException(404,'briefing not available yet')
    return x

@app.get('/api/briefing/history')
def briefing_history(market:str='USA',limit:int=Query(60,ge=1,le=500)):
    return {'data':preopen_store.history(market,limit)}

@app.get('/api/briefing/{report_id}')
def briefing_get(report_id:int):
    x=preopen_store.get(report_id)
    if not x: raise HTTPException(404,'briefing not found')
    return x


@app.get('/api/validation/runs')
def validation_runs(limit:int=Query(20,ge=1,le=100)):
    return {'data':validator.store.runs(limit)}

@app.get('/api/validation/result/{run_id}')
def validation_result(run_id:int):
    x=validator.store.result(run_id)
    if not x: raise HTTPException(404,'validation run not found')
    return x

@app.get('/api/validation/run')
def validation_run(days:int=Query(60,ge=20,le=260), max_symbols:int=Query(20,ge=8,le=32)):
    base=_symbols(FALLBACK_UNIVERSE)
    ordered=[]
    for sym in ['QQQ','SMH']+base:
        if sym not in ordered: ordered.append(sym)
    research=[x for x in ordered if x not in ('QQQ','SMH')][:max_symbols]
    return validator.run(['QQQ','SMH']+research,days)


@app.get('/api/validation/live')
def validation_live(trade_date:str|None=None):
    return live_validator.evaluate(trade_date)



@app.get("/api/premarket/intelligence")

async def premarket_intelligence():

    try:

        data=await asyncio.to_thread(

            build_premarket_briefing,

            s.db_path

        )

        return {

            "ok":True,

            "market":"USA",

            "generated_at":datetime.now(timezone.utc).isoformat(),

            "data":data

        }

    except Exception as e:

        logging.exception("premarket intelligence failed")

        raise HTTPException(status_code=500,detail=str(e))






# ===== FUJIMOTO AUTO RUNNER V3 =====
_fujimoto_auto_status={
    'enabled':True,'running':False,'last_started_at':None,'last_finished_at':None,
    'last_error':None,'run_count':0,'last_result':None,
    'startup_delay_sec':15
}

async def fujimoto_auto_forever():
    # Let FastAPI finish startup and health/status endpoints become available
    # before the first Kiwoom ranking/chart cycle begins.
    await asyncio.sleep(15)
    while True:
        try:
            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
            mins=kst.hour*60+kst.minute
            regular=bool(kst.weekday()<5 and 540<=mins<930)
            interval=10 if regular else 120
            if _fujimoto_auto_status.get('enabled',True):
                _fujimoto_auto_status['running']=True
                _fujimoto_auto_status['last_started_at']=datetime.now(timezone.utc).isoformat()
                try:
                    result=await v5_fujimoto_tracker_v2_korea(batch_size=2,limit=10,max_pages=1,cache_ttl_sec=(30 if regular else 180))
                    _fujimoto_auto_status['last_result']=result
                    _fujimoto_auto_status['run_count']=int(_fujimoto_auto_status.get('run_count') or 0)+1
                    _fujimoto_auto_status['last_error']=None
                except Exception as e:
                    _fujimoto_auto_status['last_error']=str(e)[:300]
                    logging.exception('Fujimoto auto runner failed')
                _fujimoto_auto_status['last_finished_at']=datetime.now(timezone.utc).isoformat()
                _fujimoto_auto_status['running']=False
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _fujimoto_auto_status['running']=False
            _fujimoto_auto_status['last_error']=str(e)[:300]
            logging.exception('Fujimoto auto outer loop failed')
            await asyncio.sleep(30)

@app.get('/api/v5/fujimoto-auto/KOREA')
async def v5_fujimoto_auto_status():
    r=_fujimoto_auto_status.get('last_result') or {}
    return {
        'ok':True,
        'enabled':bool(_fujimoto_auto_status.get('enabled',True)),
        'running':bool(_fujimoto_auto_status.get('running')),
        'run_count':int(_fujimoto_auto_status.get('run_count') or 0),
        'last_started_at':_fujimoto_auto_status.get('last_started_at'),
        'last_finished_at':_fujimoto_auto_status.get('last_finished_at'),
        'last_error':_fujimoto_auto_status.get('last_error'),
        'startup_delay_sec':int(_fujimoto_auto_status.get('startup_delay_sec') or 0),
        'rank_status':r.get('rank_status'),
        'watch_pool_count':r.get('watch_pool_count'),
        'evaluated_count':r.get('evaluated_count'),
        'cursor':r.get('cursor'),
        'fresh_fetch_count':r.get('fresh_fetch_count'),
        'cache_hit_count':r.get('cache_hit_count'),
        'rows':r.get('rows') or [],
        'order_placement':False,
        'signal_only':True,
    }

@app.post('/api/v5/fujimoto-auto/KOREA/toggle')
async def v5_fujimoto_auto_toggle(enabled:bool=True):
    _fujimoto_auto_status['enabled']=bool(enabled)
    return {'ok':True,'enabled':bool(enabled),'order_placement':False}


# ===== FUJIMOTO POSITION SYNC V4 =====
def _fujimoto_daytrade_positions():
    out={}
    try:
        with sqlite3.connect(s.db_path,timeout=5) as c:
            c.row_factory=sqlite3.Row
            exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v5_portfolio_assets'").fetchone()
            if not exists:
                return out
            rows=c.execute("""
                SELECT market,symbol,name,bucket,quantity,avg_price
                FROM v5_portfolio_assets
                WHERE active=1 AND UPPER(market)='KOREA' AND quantity>0
            """).fetchall()
            for r in rows:
                d=dict(r); bucket=str(d.get('bucket') or '').upper()
                if bucket not in ('DAYTRADE','TRADING','SCALP','SHORT_TERM'):
                    continue
                sym=str(d.get('symbol') or '').upper().strip()
                if not sym: continue
                out[sym]=d
    except Exception as e:
        logging.warning('Fujimoto daytrade position sync failed: %s',e)
    return out

# Keep original v2 implementation and wrap its position state before each evaluation.
_fujimoto_tracker_v2_original=v5_fujimoto_tracker_v2_korea

@app.get('/api/v5/fujimoto-tracker-v4/KOREA')
async def v5_fujimoto_tracker_v4_korea(batch_size:int=2,limit:int=10,max_pages:int=1,cache_ttl_sec:int=180):
    held=_fujimoto_daytrade_positions()
    for sym,p in held.items():
        cur=_fujimoto_tracker_state.get(sym) or {'state':'HOLD','position_open':True}
        cur['position_open']=True
        if cur.get('state') in (None,'WATCH','PREPARE','ENTRY_READY','ENTRY','NOT_EVALUATED'):
            cur['state']='HOLD'
        cur['position_source']='V5_PORTFOLIO_DAYTRADE'
        cur['quantity']=float(p.get('quantity') or 0)
        cur['avg_price']=float(p.get('avg_price') or 0)
        cur['updated_at']=datetime.now(timezone.utc).isoformat()
        _fujimoto_tracker_state[sym]=cur

    result=await _fujimoto_tracker_v2_original(batch_size=batch_size,limit=limit,max_pages=max_pages,cache_ttl_sec=cache_ttl_sec)
    rows=list(result.get('rows') or [])
    by={str(r.get('symbol') or '').upper():r for r in rows}
    for sym,p in held.items():
        if sym in by:
            by[sym]['position_open']=True
            by[sym]['position_source']='V5_PORTFOLIO_DAYTRADE'
            by[sym]['quantity']=float(p.get('quantity') or 0)
            by[sym]['avg_price']=float(p.get('avg_price') or 0)
    result['version']='FUJIMOTO_TRACKER_V4_POSITION_SYNC'
    result['position_sync_source']='V5_PORTFOLIO_DAYTRADE'
    result['daytrade_position_count']=len(held)
    result['daytrade_positions']=list(held.values())
    return result

@app.get('/api/v5/fujimoto-positions/KOREA')
async def v5_fujimoto_positions_korea():
    held=_fujimoto_daytrade_positions()
    return {'ok':True,'source':'V5_PORTFOLIO_DAYTRADE','count':len(held),'rows':list(held.values())}


# V4 position-aware auto loop; leaves v3 routes intact for rollback.
_fujimoto_auto_v4_status={'enabled':True,'running':False,'run_count':0,'last_error':None,'last_result':None,'last_started_at':None,'last_finished_at':None}

async def fujimoto_auto_v4_forever():
    await asyncio.sleep(20)
    while True:
        try:
            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
            mins=kst.hour*60+kst.minute
            regular=bool(kst.weekday()<5 and 540<=mins<930)
            interval=10 if regular else 120
            if _fujimoto_auto_v4_status.get('enabled',True):
                _fujimoto_auto_v4_status['running']=True
                _fujimoto_auto_v4_status['last_started_at']=datetime.now(timezone.utc).isoformat()
                try:
                    r=await v5_fujimoto_tracker_v41_korea(batch_size=2,limit=10,max_pages=1,cache_ttl_sec=(30 if regular else 180))
                    _fujimoto_auto_v4_status['last_result']=r
                    _fujimoto_auto_v4_status['run_count']+=1
                    _fujimoto_auto_v4_status['last_error']=None
                except Exception as e:
                    _fujimoto_auto_v4_status['last_error']=str(e)[:300]
                    logging.exception('Fujimoto auto v4 failed')
                _fujimoto_auto_v4_status['last_finished_at']=datetime.now(timezone.utc).isoformat()
                _fujimoto_auto_v4_status['running']=False
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _fujimoto_auto_v4_status['running']=False
            _fujimoto_auto_v4_status['last_error']=str(e)[:300]
            await asyncio.sleep(30)

@app.get('/api/v5/fujimoto-auto-v4/KOREA')
async def v5_fujimoto_auto_v4_status():
    r=_fujimoto_auto_v4_status.get('last_result') or {}
    return {
        'ok':True,'enabled':_fujimoto_auto_v4_status.get('enabled',True),'running':_fujimoto_auto_v4_status.get('running',False),
        'run_count':_fujimoto_auto_v4_status.get('run_count',0),'last_error':_fujimoto_auto_v4_status.get('last_error'),
        'last_started_at':_fujimoto_auto_v4_status.get('last_started_at'),'last_finished_at':_fujimoto_auto_v4_status.get('last_finished_at'),
        'rank_status':r.get('rank_status'),'watch_pool_count':r.get('watch_pool_count'),'evaluated_count':r.get('evaluated_count'),
        'cursor':r.get('cursor'),'fresh_fetch_count':r.get('fresh_fetch_count'),'cache_hit_count':r.get('cache_hit_count'),
        'daytrade_position_count':r.get('daytrade_position_count',0),'daytrade_positions':r.get('daytrade_positions') or [],
        'rows':r.get('rows') or [],'order_placement':False,'signal_only':True,
    }


# ===== FUJIMOTO POSITION FORCE TRACK V4.1 =====
_fujimoto_held_cursor_v41=0

@app.get('/api/v5/fujimoto-tracker-v41/KOREA')
async def v5_fujimoto_tracker_v41_korea(batch_size:int=2,limit:int=10,max_pages:int=1,cache_ttl_sec:int=180):
    """Position-first Fujimoto tracker.

    Held KOREA daytrade positions are always present in output and are refreshed
    round-robin even when they fall outside the ranking/watch pool. Total new
    Kiwoom minute-chart work stays bounded: when held positions exist, the
    ordinary v4 lane is reduced to one fresh candidate and one held position is
    refreshed per cycle.
    """
    import time as _time
    global _fujimoto_held_cursor_v41

    held=_fujimoto_daytrade_positions()

    # Reserve one of the two fresh-fetch slots for a held position.
    base_bs=1 if held else max(1,min(int(batch_size),2))
    result=await v5_fujimoto_tracker_v4_korea(
        batch_size=base_bs,limit=max(10,int(limit)),max_pages=max_pages,cache_ttl_sec=cache_ttl_sec)

    held_syms=sorted(held.keys())
    held_refreshed=0
    held_refresh_symbol=None
    if held_syms:
        idx=int(_fujimoto_held_cursor_v41)%len(held_syms)
        sym=held_syms[idx]
        p=held[sym]
        held_refresh_symbol=sym
        prev=_fujimoto_tracker_state.get(sym) or {'state':'HOLD','position_open':True}
        try:
            d=await asyncio.to_thread(korea.canonical_minute_bars,sym,1)
            eng=evaluate_fujimoto_engine_v1(
                d.get('bars') or [],
                previous_state=prev.get('state') or 'HOLD',
                position_open=True)
            now_ts=_time.time()
            _fujimoto_tracker_state[sym]={
                'state':eng.get('engine_state') or 'HOLD',
                'position_open':True,
                'signal':eng.get('signal') or 'NONE',
                'score':eng.get('score'),
                'updated_at':datetime.now(timezone.utc).isoformat(),
                'engine':eng,
                'position_source':'V5_PORTFOLIO_DAYTRADE',
                'quantity':float(p.get('quantity') or 0),
                'avg_price':float(p.get('avg_price') or 0),
            }
            sc=dict(eng); sc['_cached_at']=now_ts; _fujimoto_overlay_cache[sym]=sc
            held_refreshed=1
        except Exception as e:
            cur=_fujimoto_tracker_state.get(sym) or {}
            cur.update({
                'state':cur.get('state') or 'HOLD','position_open':True,
                'signal':cur.get('signal') or 'NONE','error':str(e)[:180],
                'updated_at':datetime.now(timezone.utc).isoformat(),
                'position_source':'V5_PORTFOLIO_DAYTRADE',
                'quantity':float(p.get('quantity') or 0),
                'avg_price':float(p.get('avg_price') or 0),
            })
            _fujimoto_tracker_state[sym]=cur
        _fujimoto_held_cursor_v41=(idx+1)%len(held_syms)

    # Force every held position into the response, even when absent from watch pool.
    rows=list(result.get('rows') or [])
    by={str(r.get('symbol') or '').upper():r for r in rows}
    for sym,p in held.items():
        st=_fujimoto_tracker_state.get(sym) or {'state':'HOLD','position_open':True}
        eng=st.get('engine') or _fujimoto_overlay_cache.get(sym) or {}
        score=st.get('score') if st.get('score') is not None else eng.get('score')
        row=by.get(sym)
        if row is None:
            row={
                'symbol':sym,'name':p.get('name') or sym,
                'value_rank':9999,'volume_rank':9999,
                'rank_sources':['HELD_POSITION'],
                'finder_rank_score':None,
                'trade_priority':None,
            }
            rows.append(row); by[sym]=row
        row.update({
            'position_open':True,
            'position_source':'V5_PORTFOLIO_DAYTRADE',
            'quantity':float(p.get('quantity') or 0),
            'avg_price':float(p.get('avg_price') or 0),
            'fujimoto_score':score,
            'engine_state':st.get('state') or eng.get('engine_state') or 'HOLD',
            'signal':st.get('signal') or eng.get('signal') or 'NONE',
            'transition':eng.get('transition'),
            'actionable':bool(eng.get('actionable')),
            'entry_reasons':eng.get('entry_reasons') or [],
            'exit_reasons':eng.get('exit_reasons') or [],
            'rsi':eng.get('rsi'),'macd':eng.get('macd'),
            'macd_signal':eng.get('macd_signal'),'macd_hist':eng.get('macd_hist'),
            'latest_bar_time':eng.get('latest_bar_time'),
            'held_force_track':True,
        })

    # Held positions first for management visibility; non-held retain priority order.
    rows.sort(key=lambda r:(
        1 if r.get('position_open') else 0,
        1 if r.get('trade_priority') is not None else 0,
        float(r.get('trade_priority') or -1e9)
    ),reverse=True)

    result['rows']=rows[:max(int(limit),len(held))]
    result['count']=len(result['rows'])
    result['version']='FUJIMOTO_TRACKER_V41_FORCE_HELD'
    result['daytrade_position_count']=len(held)
    result['daytrade_positions']=list(held.values())
    result['held_force_track_count']=len(held)
    result['held_refresh_symbol']=held_refresh_symbol
    result['held_fresh_fetch_count']=held_refreshed
    result['fresh_fetch_count']=int(result.get('fresh_fetch_count') or 0)+held_refreshed
    result['max_fresh_fetch_per_call']=2
    result['position_force_track']=True
    return result


# ===== DAYTRADE ENTRY AUTO V1.3 =====
_daytrade_entry_auto_status={
    'enabled':True,
    'running':False,
    'run_count':0,
    'last_started_at':None,
    'last_finished_at':None,
    'last_error':None,
    'last_result':None,
    'startup_delay_sec':20,
    'regular_interval_sec':30,
}

async def daytrade_entry_auto_forever():
    # Avoid competing with API/universe startup traffic.
    await asyncio.sleep(int(_daytrade_entry_auto_status.get('startup_delay_sec') or 20))
    while True:
        try:
            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
            mins=kst.hour*60+kst.minute
            regular=bool(kst.weekday()<5 and 540<=mins<930)

            # Outside KRX regular session do not hit Kiwoom ranking/chart APIs at all.
            if not regular:
                _daytrade_entry_auto_status['running']=False
                await asyncio.sleep(60)
                continue

            if not _daytrade_entry_auto_status.get('enabled',True):
                _daytrade_entry_auto_status['running']=False
                await asyncio.sleep(30)
                continue

            _daytrade_entry_auto_status['running']=True
            _daytrade_entry_auto_status['last_started_at']=datetime.now(timezone.utc).isoformat()
            try:
                result=await asyncio.to_thread(korea.daytrade_entry_v12,10,5,1)
                _daytrade_entry_auto_status['last_result']=result
                _daytrade_entry_auto_status['run_count']=int(_daytrade_entry_auto_status.get('run_count') or 0)+1
                _daytrade_entry_auto_status['last_error']=None
            except Exception as e:
                _daytrade_entry_auto_status['last_error']=str(e)[:300]
                logging.exception('Daytrade entry auto runner failed')
            finally:
                _daytrade_entry_auto_status['last_finished_at']=datetime.now(timezone.utc).isoformat()
                _daytrade_entry_auto_status['running']=False

            await asyncio.sleep(int(_daytrade_entry_auto_status.get('regular_interval_sec') or 30))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _daytrade_entry_auto_status['running']=False
            _daytrade_entry_auto_status['last_error']=str(e)[:300]
            logging.exception('Daytrade entry auto outer loop failed')
            await asyncio.sleep(30)

@app.get('/api/v5/daytrade-entry-auto/KOREA')
async def v5_daytrade_entry_auto_status():
    kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
    mins=kst.hour*60+kst.minute
    regular=bool(kst.weekday()<5 and 540<=mins<930)
    r=_daytrade_entry_auto_status.get('last_result') or {}
    return {
        'ok':True,
        'version':'DAYTRADE_ENTRY_AUTO_V1_3',
        'enabled':bool(_daytrade_entry_auto_status.get('enabled',True)),
        'running':bool(_daytrade_entry_auto_status.get('running')),
        'run_count':int(_daytrade_entry_auto_status.get('run_count') or 0),
        'last_started_at':_daytrade_entry_auto_status.get('last_started_at'),
        'last_finished_at':_daytrade_entry_auto_status.get('last_finished_at'),
        'last_error':_daytrade_entry_auto_status.get('last_error'),
        'startup_delay_sec':int(_daytrade_entry_auto_status.get('startup_delay_sec') or 0),
        'regular_interval_sec':int(_daytrade_entry_auto_status.get('regular_interval_sec') or 30),
        'regular_open':regular,
        'kst_now':kst.isoformat(),
        'market_gate':r.get('market_gate'),
        'candidate_count':r.get('candidate_count'),
        'evaluated_count':r.get('evaluated_count'),
        'entry_candidate_count':r.get('entry_candidate_count') or 0,
        'ready_count':r.get('ready_count') or 0,
        'rows':r.get('rows') or [],
        'signal_only':True,
        'order_placement':False,
        'note':'Runner calls Kiwoom only during KRX regular session; UI should read this cache endpoint.',
    }

@app.post('/api/v5/daytrade-entry-auto/KOREA/toggle')
async def v5_daytrade_entry_auto_toggle(enabled:bool=True):
    _daytrade_entry_auto_status['enabled']=bool(enabled)
    return {'ok':True,'enabled':bool(enabled),'order_placement':False}


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
