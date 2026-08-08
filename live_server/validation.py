from __future__ import annotations
from datetime import datetime, timezone, timedelta
from statistics import mean
from pathlib import Path
import json, sqlite3, time
import pandas as pd
import requests

SEMI={'SOXL','SOXS','SMH','NVDA','AMD','AVGO','MU','ARM','TSM','ASML','INTC','QCOM'}
INVERSE={'SOXS':'SMH','SQQQ':'QQQ'}
LEVERAGED_LONG={'SOXL':'SMH','TQQQ':'QQQ'}
def asset_group(symbol:str)->str:
    if symbol in INVERSE: return 'INVERSE_ETF'
    if symbol in LEVERAGED_LONG: return 'LEVERAGED_LONG'
    return 'STOCK'


def classify_regime(history_rows, current_index):
    """Ex-ante regime: only completed bars before the target day."""
    if current_index is None or current_index < 20:
        return {'regime':'UNKNOWN','ma20':None,'ma20_slope_pct':None,'prev_close':None}
    prev = history_rows[:current_index]
    closes = [r['close'] for r in prev[-20:]]
    ma20 = mean(closes)
    prev_close = closes[-1]
    recent5 = mean(closes[-5:])
    prior5 = mean(closes[-10:-5])
    slope = (recent5/prior5-1)*100 if prior5 else 0.0
    above = prev_close >= ma20*1.003
    below = prev_close <= ma20*0.997
    if above and slope > 0.15:
        rg='BULL'
    elif below and slope < -0.15:
        rg='BEAR'
    else:
        rg='MIXED'
    return {'regime':rg,'ma20':ma20,'ma20_slope_pct':slope,'prev_close':prev_close}

def classify_semi_regime(history_rows, current_index):
    x=classify_regime(history_rows,current_index)
    label={'BULL':'SEMI_STRONG','BEAR':'SEMI_WEAK','MIXED':'SEMI_MIXED','UNKNOWN':'SEMI_UNKNOWN'}[x['regime']]
    return {'semi_regime':label,'semi_ma20':x['ma20'],'semi_ma20_slope_pct':x['ma20_slope_pct']}

def safe_rank_corr(rows):
    if len(rows)<3:
        return None
    pred=pd.Series([r['pred_rank'] for r in rows],dtype=float)
    act=pd.Series([r['actual_rank'] for r in rows],dtype=float)
    rho=pred.corr(act,method='pearson')
    return None if pd.isna(rho) else float(rho)

def summarize_slice(rows):
    if not rows:
        return {'n':0,'days':0,'rank_corr':None,'precision_at_5':None,'top5_excess_avg':None}
    by_day={}
    for r in rows:
        by_day.setdefault(r['trade_date'],[]).append(r)
    rhos=[]; precisions=[]; top5_ex=[]
    for dr in by_day.values():
        dr=sorted(dr,key=lambda x:x['pred_rank'])
        rho=safe_rank_corr(dr)
        if rho is not None:
            rhos.append(rho)
        actual=sorted(dr,key=lambda x:x['actual_rank'])
        p5={x['symbol'] for x in dr[:5]}
        a5={x['symbol'] for x in actual[:5]}
        precisions.append(len(p5&a5)/max(1,len(p5))*100)
        top5_ex.append(mean([x['excess_pct'] for x in dr[:5]]))
    return {
        'n':len(rows),'days':len(by_day),
        'rank_corr':mean(rhos) if rhos else None,
        'precision_at_5':mean(precisions) if precisions else None,
        'top5_excess_avg':mean(top5_ex) if top5_ex else None
    }

def num(v, default=0.0):
    try: return float(str(v).replace(',','').strip())
    except Exception: return default

def _atr5(prev_rows):
    trs=[]
    for i,r in enumerate(prev_rows):
        pc=prev_rows[i-1]['close'] if i>0 else r['close']
        trs.append(max(r['high']-r['low'],abs(r['high']-pc),abs(r['low']-pc)))
    m=mean([r['close'] for r in prev_rows]) if prev_rows else 0
    return mean(trs)/m*100 if m else 0

def open_model_score(open_px, prev5, gap_pct, qqq_gap=0.0, smh_gap=0.0, symbol=''):
    closes=[r['close'] for r in prev5]
    ma5=mean(closes); slope=(closes[-1]/closes[0]-1)*100 if closes[0] else 0
    avg5vol=mean([r['volume'] for r in prev5])
    avg5dv=mean([r['close']*r['volume'] for r in prev5])
    atr=_atr5(prev5); score=0.0; parts={}
    def add(name,pts):
        nonlocal score; score+=pts; parts[name]=round(pts,2)

    add('Open>MA5',14 if open_px>ma5 else -18)

    if slope>=2:add('MA5 slope',14)
    elif slope>0:add('MA5 slope',7+min(7,slope*3.5))
    elif slope<=-5:add('MA5 slope',-14)
    else:add('MA5 slope',slope*2.2)

    if avg5dv>=1_000_000_000:add('Liquidity',18)
    elif avg5dv>=500_000_000:add('Liquidity',15)
    elif avg5dv>=150_000_000:add('Liquidity',12)
    elif avg5dv>=50_000_000:add('Liquidity',7)
    elif avg5dv>=20_000_000:add('Liquidity',3)
    else:add('Liquidity',-10)

    if 3<=atr<=8:add('ATR',12)
    elif 1.5<=atr<3 or 8<atr<=10:add('ATR',8)
    elif 1<=atr<1.5:add('ATR',4)
    elif atr>12:add('ATR',-4)

    # Direction-normalized opening momentum:
    # inverse ETFs interpret negative own gap / negative underlying market as favorable.
    dir_mult=-1 if symbol in INVERSE else 1
    effective_gap=gap_pct*dir_mult
    if .5<=effective_gap<=4:add('Gap momentum',12)
    elif 0<effective_gap<.5:add('Gap momentum',5)
    elif 4<effective_gap<=8:add('Gap momentum',7)
    elif abs(effective_gap)>12:add('Gap momentum',-12)
    elif effective_gap<0:add('Gap momentum',-6)

    raw_mg=smh_gap if (symbol in SEMI or INVERSE.get(symbol)=='SMH' or LEVERAGED_LONG.get(symbol)=='SMH') else qqq_gap
    mg=raw_mg*dir_mult
    if mg>=1:add('Market/Sector',8)
    elif mg>0:add('Market/Sector',4)
    elif mg<=-1:add('Market/Sector',-7)

    if symbol in {'SOXL','SOXS','TQQQ','SQQQ'}:add('Liquid leveraged ETF',5)
    elif symbol in {'NVDA','AAPL','MSFT','AMZN','META','TSLA','AMD','PLTR'}:add('Core liquidity',2)

    return {'score':max(0,min(100,round(score))),'ma5':ma5,'ma5_slope_pct':slope,
            'avg5_volume':avg5vol,'avg5_dollar_volume':avg5dv,'atr5_pct':atr,
            'gap_pct':gap_pct,'effective_gap_pct':effective_gap,'asset_group':asset_group(symbol),
            'parts':parts}

class ValidationStore:
    def __init__(self, live_db_path:str):
        p=Path(live_db_path)
        self.path=str(p.with_name(p.stem+'_validation.db'))
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS runs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              model TEXT, days_requested INTEGER, days_validated INTEGER,
              symbols_requested INTEGER, mean_spearman REAL, median_spearman REAL,
              top5_excess_avg REAL, top5_close_avg REAL, top5_positive_excess_rate REAL,
              note TEXT, created_at TEXT, payload TEXT
            );
            """)

    def save(self,result:dict)->int:
        s=result['summary']
        with sqlite3.connect(self.path) as c:
            cur=c.execute("""INSERT INTO runs(model,days_requested,days_validated,symbols_requested,
                mean_spearman,median_spearman,top5_excess_avg,top5_close_avg,
                top5_positive_excess_rate,note,created_at,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.get('model'),s.get('days_requested'),s.get('days_validated'),s.get('symbols_requested'),
                 s.get('mean_spearman'),s.get('median_spearman'),s.get('top5_excess_avg'),
                 s.get('top5_close_avg'),s.get('top5_positive_excess_rate'),s.get('note'),
                 s.get('created_at'),json.dumps(result,ensure_ascii=False)))
            return int(cur.lastrowid)

    def runs(self,limit=20):
        with sqlite3.connect(self.path) as c:
            c.row_factory=sqlite3.Row
            return [dict(r) for r in c.execute(
                'SELECT id,model,days_requested,days_validated,symbols_requested,mean_spearman,median_spearman,top5_excess_avg,top5_close_avg,top5_positive_excess_rate,note,created_at FROM runs ORDER BY id DESC LIMIT ?',
                (limit,)).fetchall()]

    def result(self,run_id:int):
        with sqlite3.connect(self.path) as c:
            r=c.execute('SELECT payload FROM runs WHERE id=?',(run_id,)).fetchone()
            return json.loads(r[0]) if r else None

class HistoricalValidator:
    def __init__(self,kiwoom,live_db_path):
        self.k=kiwoom
        self.store=ValidationStore(live_db_path)

    def daily_history(self,symbol,exchange,calendar_days=420,max_pages=10):
        start=(datetime.now(timezone.utc)-timedelta(days=calendar_days)).strftime('%Y%m%d')
        candidates=[]
        for ex in [exchange,'ND','NY','NA']:
            ex='NA' if ex=='AM' else ex
            if ex and ex not in candidates: candidates.append(ex)

        last_error=''
        for ex in candidates:
            for attempt in range(3):
                try:
                    all_rows=[]
                    cont_yn=None
                    next_key=None
                    for page in range(max_pages):
                        headers=self.k.headers('usa06012')
                        if cont_yn is not None:
                            headers['cont-yn']=cont_yn
                        if next_key is not None:
                            headers['next-key']=next_key

                        resp=requests.post(
                            self.k.s.rest_base+'/api/us/chart',
                            headers=headers,
                            json={
                                'stex_tp':ex,'stk_cd':symbol,'strt_dt':start,
                                'upd_stkpc_tp':'1','exrt_appl_tp':'0'
                            },
                            timeout=30
                        )
                        d=resp.json()
                        if d.get('return_code')!=0:
                            last_error=f"{d.get('return_code')} {d.get('return_msg')}"
                            break

                        page_rows=d.get('result_list') or []
                        all_rows.extend(page_rows)

                        cont_yn=resp.headers.get('cont-yn') or resp.headers.get('Cont-Yn')
                        next_key=resp.headers.get('next-key') or resp.headers.get('Next-Key')
                        if cont_yn!='Y':
                            break
                        time.sleep(.22)

                    rows=[]
                    for x in all_rows:
                        dt=str(x.get('dt') or '')
                        op=abs(num(x.get('open_pric'))); close=abs(num(x.get('cur_prc')))
                        hi=abs(num(x.get('high_pric'))); lo=abs(num(x.get('low_pric')))
                        vol=abs(num(x.get('acc_trde_qty')))
                        if len(dt)>=8 and op>0 and close>0:
                            rows.append({
                                'date':dt[:8],'open':op,'high':hi or max(op,close),
                                'low':lo or min(op,close),'close':close,'volume':vol
                            })

                    by={r['date']:r for r in rows}
                    out=[by[d] for d in sorted(by)]
                    if len(out)>=40:
                        return out,ex,'',len(all_rows),page+1
                    last_error=f"only {len(out)} usable rows after {page+1} pages"
                except Exception as e:
                    last_error=str(e)
                time.sleep(.5*(attempt+1))
        return [],exchange,last_error or 'no usable history',0,0

    def run(self,symbols,days=60):
        hist={}; exchanges={}; failures={}; load_meta={}
        # Need requested sessions + at least 30 warm-up sessions. Use generous calendar range.
        calendar_days=max(420,int((days+40)*1.8))
        for sym in symbols:
            ex=self.k.active_exchange(sym)
            rows,used_ex,err,raw_rows,pages=self.daily_history(sym,ex,calendar_days,max_pages=10)
            hist[sym]=rows; exchanges[sym]=used_ex
            load_meta[sym]={'usable_rows':len(rows),'raw_rows':raw_rows,'pages':pages,'exchange':used_ex}
            if not rows: failures[sym]=err
            time.sleep(.12)

        qrows=hist.get('QQQ',[])
        if len(qrows)<10: raise RuntimeError('QQQ historical data unavailable')
        srows=hist.get('SMH',[])
        qmap={r['date']:i for i,r in enumerate(qrows)}
        smap={r['date']:i for i,r in enumerate(srows)}
        # Use latest completed historical sessions. If today's row exists while market is open,
        # drop it so validation never evaluates an incomplete day.
        all_dates=sorted(qmap.keys())
        today_utc=datetime.now(timezone.utc).strftime('%Y%m%d')
        if all_dates and all_dates[-1]>=today_utc:
            all_dates=all_dates[:-1]
        dates=all_dates[-days:]
        all_rows=[]; daily=[]

        for day in dates:
            qi=qmap[day]
            if qi<5: continue
            qcur=qrows[qi]; qprev=qrows[qi-1]
            qgap=(qcur['open']/qprev['close']-1)*100
            qret=(qcur['close']/qcur['open']-1)*100

            sgap=sret=0.0
            si=smap.get(day)
            if si is not None and si>=1:
                scur=srows[si]; sprev=srows[si-1]
                sgap=(scur['open']/sprev['close']-1)*100
                sret=(scur['close']/scur['open']-1)*100

            regime_info=classify_regime(qrows,qi)
            semi_info=classify_semi_regime(srows,si) if si is not None else {
                'semi_regime':'SEMI_UNKNOWN','semi_ma20':None,'semi_ma20_slope_pct':None
            }

            rows=[]
            for sym,h in hist.items():
                if sym in ('QQQ','SMH'): continue
                imap={r['date']:i for i,r in enumerate(h)}
                idx=imap.get(day)
                if idx is None or idx<5: continue
                cur=h[idx]; prev=h[idx-1]; prev5=h[idx-5:idx]
                gap=(cur['open']/prev['close']-1)*100
                f=open_model_score(cur['open'],prev5,gap,qgap,sgap,sym)
                ret=(cur['close']/cur['open']-1)*100
                mfe=(cur['high']/cur['open']-1)*100
                mae=(cur['low']/cur['open']-1)*100
                if sym in INVERSE:
                    bench=-(sret if INVERSE[sym]=='SMH' and si is not None else qret)
                else:
                    bench=sret if sym in SEMI and si is not None else qret
                rows.append({'trade_date':day,'symbol':sym,'exchange':exchanges.get(sym,''),
                             'model':'OPEN_V0','asset_group':f['asset_group'],
                             'market_regime':regime_info['regime'],'semi_regime':semi_info['semi_regime'],
                             'score':f['score'],'gap_pct':gap,'effective_gap_pct':f['effective_gap_pct'],
                             'ma5':f['ma5'],'ma5_slope_pct':f['ma5_slope_pct'],
                             'atr5_pct':f['atr5_pct'],'avg5_dollar_volume':f['avg5_dollar_volume'],
                             'open_to_close_pct':ret,'mfe_pct':mfe,'mae_pct':mae,
                             'benchmark_pct':bench,'excess_pct':ret-bench,'parts':f['parts']})

            if len(rows)<3: continue
            rows.sort(key=lambda r:(r['score'],r['avg5_dollar_volume']),reverse=True)
            for n,r in enumerate(rows,1): r['pred_rank']=n
            actual=sorted(rows,key=lambda r:r['excess_pct'],reverse=True)
            actual_rank={r['symbol']:n for n,r in enumerate(actual,1)}
            for r in rows:
                r['actual_rank']=actual_rank[r['symbol']]
                r['validation_tag']='NEUTRAL'
                if r['pred_rank']<=5 and r['actual_rank']<=5:
                    r['validation_tag']='TRUE_POSITIVE'
                elif r['pred_rank']<=5 and r['actual_rank']>10:
                    r['validation_tag']='FALSE_POSITIVE'
                elif r['pred_rank']>10 and r['actual_rank']<=5:
                    r['validation_tag']='MISSED_WINNER'

            pred=pd.Series([r['pred_rank'] for r in rows],dtype=float)
            act=pd.Series([r['actual_rank'] for r in rows],dtype=float)
            rho=pred.corr(act,method='pearson')
            rho=None if pd.isna(rho) else float(rho)
            top5=rows[:5]
            actual_top5={r['symbol'] for r in actual[:5]}
            pred_top5={r['symbol'] for r in top5}
            precision5=len(actual_top5 & pred_top5)/max(1,len(pred_top5))*100
            group_stats={}
            for grp in ('STOCK','LEVERAGED_LONG','INVERSE_ETF'):
                gr=[r for r in rows if r['asset_group']==grp]
                if gr:
                    group_stats[grp]={
                        'count':len(gr),
                        'avg_excess':mean([r['excess_pct'] for r in gr]),
                        'top3_avg_excess':mean([r['excess_pct'] for r in gr[:3]])
                    }
            daily.append({'trade_date':day,
                          'market_regime':regime_info['regime'],
                          'semi_regime':semi_info['semi_regime'],
                          'market_ma20_slope_pct':regime_info['ma20_slope_pct'],
                          'semi_ma20_slope_pct':semi_info['semi_ma20_slope_pct'],
                          'spearman':rho,
                          'top5_excess_avg':mean([r['excess_pct'] for r in top5]),
                          'top5_close_avg':mean([r['open_to_close_pct'] for r in top5]),
                          'top5_positive_excess_rate':mean([1 if r['excess_pct']>0 else 0 for r in top5])*100,
                          'precision_at_5':precision5,'group_stats':group_stats,
                          'universe':len(rows),'qqq_return':qret,'smh_return':sret})
            all_rows.extend(rows)

        if not daily: raise RuntimeError('No historical validation dates could be constructed')
        rhos=[x['spearman'] for x in daily if x['spearman'] is not None]
        summary={'model':'OPEN_V0','days_requested':days,'days_validated':len(daily),
                 'symbols_requested':len(symbols),'mean_spearman':mean(rhos) if rhos else None,
                 'median_spearman':float(pd.Series(rhos).median()) if rhos else None,
                 'top5_excess_avg':mean([x['top5_excess_avg'] for x in daily]),
                 'top5_close_avg':mean([x['top5_close_avg'] for x in daily]),
                 'top5_positive_excess_rate':mean([x['top5_positive_excess_rate'] for x in daily]),
                 'precision_at_5':mean([x['precision_at_5'] for x in daily]),
                 'symbols_loaded':len([x for x in hist if hist[x]]),
                 'symbols_failed':len(failures),
                 'failed_symbols':failures,
                 'load_meta':load_meta,
                 'requested_sessions':days,
                 'candidate_sessions_loaded':len(dates),
                 'validated_sessions':len(daily),
                 'history_start':dates[0] if dates else None,
                 'history_end':dates[-1] if dates else None,
                 'unknown_regime_days':len([x for x in daily if x.get('market_regime')=='UNKNOWN']),
                 'avg_universe':mean([x['universe'] for x in daily]),
                 'created_at':datetime.now(timezone.utc).isoformat(),
                 'note':'OPEN_V0 uses previous completed daily bars plus the current-day opening print only. Current-day high/low/close/volume are evaluation-only. Inverse ETF score direction and benchmark are both adjusted.'}

        # Aggregate group performance and component diagnostics.
        group_summary={}
        for grp in ('STOCK','LEVERAGED_LONG','INVERSE_ETF'):
            gr=[r for r in all_rows if r.get('asset_group')==grp]
            if gr:
                group_summary[grp]={
                    'n':len(gr),
                    'avg_excess':mean([r['excess_pct'] for r in gr]),
                    'avg_score':mean([r['score'] for r in gr]),
                    'positive_excess_rate':mean([1 if r['excess_pct']>0 else 0 for r in gr])*100
                }
        summary['group_summary']=group_summary

        tp=[r for r in all_rows if r.get('validation_tag')=='TRUE_POSITIVE']
        fp=[r for r in all_rows if r.get('validation_tag')=='FALSE_POSITIVE']
        def avg_parts(items):
            keys=sorted({k for r in items for k in (r.get('parts') or {}).keys()})
            return {k:mean([float((r.get('parts') or {}).get(k,0)) for r in items]) for k in keys} if items else {}
        summary['component_diagnostics']={
            'true_positive_count':len(tp),
            'false_positive_count':len(fp),
            'true_positive_avg_parts':avg_parts(tp),
            'false_positive_avg_parts':avg_parts(fp)
        }


        regime_summary={}
        for rg in ('BULL','BEAR','MIXED'):
            regime_summary[rg]=summarize_slice([r for r in all_rows if r.get('market_regime')==rg])
        summary['regime_summary']=regime_summary

        semi_regime_summary={}
        for rg in ('SEMI_STRONG','SEMI_WEAK','SEMI_MIXED'):
            semi_regime_summary[rg]=summarize_slice([r for r in all_rows if r.get('semi_regime')==rg])
        summary['semi_regime_summary']=semi_regime_summary

        dates_used=sorted({r['trade_date'] for r in all_rows})
        time_windows=[]
        max_blocks=min(6,(len(dates_used)+19)//20)
        for block in range(max_blocks):
            end_idx=len(dates_used)-block*20
            start_idx=max(0,end_idx-20)
            ds=dates_used[start_idx:end_idx]
            if not ds:
                continue
            if block==0:
                label='RECENT_20'
            else:
                label=f'{block*20+1}_{(block+1)*20}_DAYS_AGO'
            ds_set=set(ds)
            x=summarize_slice([r for r in all_rows if r['trade_date'] in ds_set])
            x.update({'window':label,'start_date':ds[0],'end_date':ds[-1]})
            time_windows.append(x)
        summary['time_window_summary']=time_windows

        regime_components={}
        for rg in ('BULL','BEAR','MIXED'):
            grp=[r for r in all_rows if r.get('market_regime')==rg]
            gtp=[r for r in grp if r.get('validation_tag')=='TRUE_POSITIVE']
            gfp=[r for r in grp if r.get('validation_tag')=='FALSE_POSITIVE']
            regime_components[rg]={
                'true_positive_count':len(gtp),
                'false_positive_count':len(gfp),
                'true_positive_avg_parts':avg_parts(gtp),
                'false_positive_avg_parts':avg_parts(gfp)
            }
        summary['regime_component_diagnostics']=regime_components
        result={'summary':summary,'daily':daily,'rows':all_rows}
        summary['run_id']=self.store.save(result)
        return result


class LiveTop10Validator:
    """Evaluates saved live ranking snapshots against subsequent tick data."""
    def __init__(self,live_db_path:str):
        self.path=live_db_path

    def evaluate(self,trade_date:str|None=None):
        from zoneinfo import ZoneInfo
        et=ZoneInfo('America/New_York')
        with sqlite3.connect(self.path) as c:
            c.row_factory=sqlite3.Row
            if not trade_date:
                r=c.execute('SELECT MAX(trade_date) d FROM ranking_snapshots').fetchone()
                trade_date=r['d'] if r else None
            if not trade_date:
                return {'trade_date':None,'snapshots':[]}
            snaps=[dict(r) for r in c.execute(
                'SELECT * FROM ranking_snapshots WHERE trade_date=? ORDER BY captured_at,rank',(trade_date,)).fetchall()]
            if not snaps:
                return {'trade_date':trade_date,'snapshots':[]}

            groups={}
            for s in snaps: groups.setdefault(s['label'],[]).append(s)
            out=[]
            for label,rows in groups.items():
                evaluated=[]
                for s in rows:
                    try:
                        t0=pd.Timestamp(s['captured_at'])
                        if t0.tzinfo is None: t0=t0.tz_localize('UTC')
                        else: t0=t0.tz_convert('UTC')
                    except Exception:
                        continue
                    ticks=[dict(r) for r in c.execute(
                        'SELECT price,ts FROM ticks WHERE symbol=? AND ts>=? ORDER BY ts',
                        (s['symbol'],t0.isoformat())).fetchall()]
                    pts=[]
                    for x in ticks:
                        try:
                            ts=pd.Timestamp(x['ts'])
                            if ts.tzinfo is None: ts=ts.tz_localize('UTC')
                            else: ts=ts.tz_convert('UTC')
                            if ts.tz_convert(et).strftime('%Y-%m-%d')==trade_date:
                                pts.append((ts,float(x['price'])))
                        except Exception:
                            pass
                    if not pts: continue
                    p0=float(s['price'] or pts[0][1])
                    def horizon(minutes):
                        target=t0+pd.Timedelta(minutes=minutes)
                        vals=[p for ts,p in pts if ts>=target]
                        return vals[0] if vals else None
                    p30=horizon(30); p60=horizon(60); pcl=pts[-1][1]
                    prices=[p for _,p in pts]
                    evaluated.append({
                        'rank':s['rank'],'symbol':s['symbol'],'score':s['score'],'price0':p0,
                        'ret_30m':None if p30 is None else (p30/p0-1)*100,
                        'ret_60m':None if p60 is None else (p60/p0-1)*100,
                        'ret_to_last':(pcl/p0-1)*100,
                        'mfe_to_last':(max(prices)/p0-1)*100,
                        'mae_to_last':(min(prices)/p0-1)*100
                    })
                if evaluated:
                    out.append({
                        'label':label,'captured':len(rows),'evaluated':len(evaluated),
                        'top5_30m':mean([x['ret_30m'] for x in evaluated[:5] if x['ret_30m'] is not None]) if any(x['ret_30m'] is not None for x in evaluated[:5]) else None,
                        'top5_60m':mean([x['ret_60m'] for x in evaluated[:5] if x['ret_60m'] is not None]) if any(x['ret_60m'] is not None for x in evaluated[:5]) else None,
                        'top5_to_last':mean([x['ret_to_last'] for x in evaluated[:5]]),
                        'rows':evaluated
                    })
            return {'trade_date':trade_date,'snapshots':out}
