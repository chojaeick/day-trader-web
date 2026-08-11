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
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
s=Settings(); db=DB(s.db_path); k=KiwoomClient(s,db); validator=HistoricalValidator(k,s.db_path); live_validator=LiveTop10Validator(s.db_path); archive=RankingArchive(s.db_path); preopen_store=PreOpenReportStore(s.db_path); korea=KoreaMarketAdapter(k); tasks=[]
manual_scan_state={'last_started_monotonic':0.0,'last_result':None}
v4=CleanEngine(s.db_path)

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
    last={'USA':0.0,'KOREA':0.0}
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
                bars_now=len(ticks_to_bars(db.ticks(sym,2500),1))
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
            if now-last['USA']>=30:
                usa_candidates=screener_rows(db.quotes(),db.daily_metrics(),40)
                finder=v4.build_usa_finder(
                    usa_candidates,
                    k.discovery,5,db=db
                )
                # Candidate data warming runs asynchronously so live Finder/Tracker
                # is never blocked by extra quote/daily/minute requests.
                if bridge_warm_task is None or bridge_warm_task.done():
                    bridge_warm_task=asyncio.create_task(
                        warm_bridge_candidates(usa_candidates,k.discovery)
                    )
                finder_syms=[r.get('symbol') for r in (finder.get('rows') or [])]
                light_syms=[r.get('symbol') for r in (finder.get('light_rows') or [])]
                await warm_usa_symbols(finder_syms)
                logging.info(
                    'V4 light tracker: %s',
                    ','.join(x for x in light_syms[:20] if x)
                )
                # Keep the cache bounded to names that are still relevant plus positions.
                active=set(finder_syms)
                try:
                    active.update(p.get('symbol') for p in v4.store.positions('USA') if p.get('symbol'))
                except Exception:
                    pass
                warmed_usa.intersection_update(active)
                last['USA']=now

            if now-last['KOREA']>=300:
                v4.build_korea_finder(korea.discovery,5); last['KOREA']=now

            v4.refresh_usa_tracker(db)
            v4.refresh_korea_tracker(korea)
        except Exception:
            logging.exception('V4 engine loop failed')
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
        tasks.extend([asyncio.create_task(k.websocket_forever()),asyncio.create_task(k.snapshot_poll_forever()),
                      asyncio.create_task(k.daily_refresh_forever()),asyncio.create_task(k.backfill_forever_once()),
                      asyncio.create_task(k.discovery_forever()),asyncio.create_task(checkpoint_forever()),
                      asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),
                      asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(v4_engine_forever())])
    yield
    for t in tasks: t.cancel()


# V2.1.1 hotfix: the Streamlit process loaded .env, but the FastAPI/systemd
# process did not. Load the backend project .env explicitly before any API
# client reads OPENAI_API_KEY.
_BACKEND_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_BACKEND_ENV, override=True)

app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])





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
    if market=='USA': return v4.refresh_usa_tracker(db)
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
