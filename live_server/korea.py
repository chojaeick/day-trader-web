
from __future__ import annotations
from datetime import datetime, timezone
import requests, re
from .quality_gate import build_korea_metadata, grade_korea_row

def _num(v):
    try:
        s=str(v).replace(',','').replace('+','').strip()
        if s in ('','None','null'):
            return 0.0
        return float(s)
    except Exception:
        return 0.0

def _clean_code(raw):
    s=str(raw or '').strip()
    # Kiwoom integrated-market symbols can include suffixes such as _AL.
    # Prefer a canonical 6-character Korean security code when safely extractable.
    if '_' in s:
        head=s.split('_',1)[0]
        if len(head)==6:
            return head
    m=re.match(r'^([0-9A-Z]{6})', s)
    return m.group(1) if m else s

def _first_list(d, preferred):
    if preferred and isinstance(d.get(preferred), list):
        return d.get(preferred) or []
    for v in d.values():
        if isinstance(v, list):
            return v
    return []

class KoreaMarketAdapter:
    def __init__(self, kiwoom_client):
        self.k=kiwoom_client
        self.discovery={
            'updated_at':None,'rows':[],'count':0,'top10':[],
            'source_counts':{},'market_breakdown':{'KOSPI':0,'KOSDAQ':0},
            'score_model':'KOREA_CURRENT_V1_GAMMA'
        }
        self.intraday_pulse={
            'updated_at':None,'market_open':False,'status':'NOT_REFRESHED',
            'rows':[],'top10':[],'vi_count':0,'score_model':'KOREA_CURRENT_V2_LIVE'
        }
        self.stock_meta={}
        self.stock_meta_updated_at=None
        self.cap_rank_enabled=False

    def minute_chart(self, stk_cd, tick=1, max_pages=1):

        """V4.7 ka10080 real minute-bar adapter.



        Returns actual Kiwoom 1m/5m OHLCV bars in chronological order.

        Default max_pages=1 keeps live Heavy5 polling lightweight.

        """

        code=_clean_code(stk_cd)

        tick=str(int(tick))



        if tick not in ('1','5'):

            raise ValueError('tick must be 1 or 5')



        rows=[]

        pages=0

        next_key=''

        cont_yn=''



        while pages < max(1,int(max_pages)):

            hdr=self.k.headers('ka10080')



            if next_key:

                hdr['cont-yn']='Y'

                hdr['next-key']=next_key



            r=requests.post(

                self.k.s.rest_base+'/api/dostk/chart',

                headers=hdr,

                json={

                    'stk_cd':code,

                    'tic_scope':tick,

                    'upd_stkpc_tp':'1'

                },

                timeout=30

            )



            d=r.json()



            if d.get('return_code') not in (None,0):

                raise RuntimeError(

                    f"ka10080 {code}/{tick}m: "

                    f"{d.get('return_code')} {d.get('return_msg')}"

                )



            raw=d.get('stk_min_pole_chart_qry') or []



            for x in raw:

                if not isinstance(x,dict):

                    continue



                tm=str(x.get('cntr_tm') or '').strip()

                close=abs(_num(x.get('cur_prc')))

                op=abs(_num(x.get('open_pric')))

                hi=abs(_num(x.get('high_pric')))

                lo=abs(_num(x.get('low_pric')))

                vol=abs(_num(x.get('trde_qty')))

                acc_vol=abs(_num(x.get('acc_trde_qty')))



                if len(tm)<12 or close<=0:

                    continue



                rows.append({

                    'time':tm,

                    'open':op,

                    'high':hi,

                    'low':lo,

                    'close':close,

                    'volume':vol,

                    'acc_volume':acc_vol

                })



            pages+=1



            cont_yn=str(

                r.headers.get('cont-yn')

                or r.headers.get('Cont-Yn')

                or ''

            ).upper()



            next_key=(

                r.headers.get('next-key')

                or r.headers.get('Next-Key')

                or ''

            )



            if cont_yn!='Y' or not next_key:

                break



        # ka10080 is newest-first. Engine uses chronological order.

        unique={}

        for row in rows:

            unique[row['time']]=row



        bars=sorted(unique.values(),key=lambda x:x['time'])



        return {

            'ok':True,

            'api_id':'ka10080',

            'symbol':code,

            'tick_minutes':int(tick),

            'pages':pages,

            'count':len(bars),

            'bars':bars,

            'oldest':bars[0] if bars else None,

            'latest':bars[-1] if bars else None,

            'continuation':{

                'cont_yn':cont_yn,

                'next_key':next_key

            },

            'checked_at':datetime.now(timezone.utc).isoformat()

        }



    def canonical_minute_bars(self, stk_cd, max_pages=3):

        """Aggregate raw ka10079 rows into canonical 1-minute OHLCV bars."""

        d=self.minute_chart(stk_cd,1,max_pages=max_pages)

        raw=d.get('bars') or []



        grouped={}

        for r in raw:

            minute=str(r.get('time') or '')[:12]

            if len(minute)!=12:

                continue

            grouped.setdefault(minute,[]).append(r)



        out=[]

        for minute in sorted(grouped):

            g=grouped[minute]

            g.sort(key=lambda x:x['time'])



            opens=[_num(x.get('open')) for x in g if _num(x.get('open'))>0]

            highs=[_num(x.get('high')) for x in g if _num(x.get('high'))>0]

            lows=[_num(x.get('low')) for x in g if _num(x.get('low'))>0]

            closes=[_num(x.get('close')) for x in g if _num(x.get('close'))>0]

            vols=[max(0.0,_num(x.get('volume'))) for x in g]



            if not closes:

                continue



            op=opens[0] if opens else closes[0]

            hi=max(highs) if highs else max(closes)

            lo=min(lows) if lows else min(closes)

            cl=closes[-1]

            vol=sum(vols)



            out.append({

                'time':minute+'00',

                'open':op,

                'high':hi,

                'low':lo,

                'close':cl,

                'volume':vol,

                'raw_rows':len(g)

            })



        return {

            'ok':True,

            'symbol':d.get('symbol'),

            'tick_minutes':1,

            'pages':d.get('pages'),

            'raw_count':d.get('raw_count'),

            'count':len(out),

            'bars':out,

            'oldest':out[0] if out else None,

            'latest':out[-1] if out else None

        }



    def canonical_five_minute_bars(self, stk_cd, max_pages=3):

        """Aggregate canonical 1-minute bars into KST-aligned 5-minute OHLCV bars."""

        d=self.canonical_minute_bars(stk_cd,max_pages=max_pages)

        one=d.get('bars') or []



        grouped={}

        for r in one:

            tm=str(r.get('time') or '')

            if len(tm)<12:

                continue



            ymd=tm[:8]

            hh=int(tm[8:10])

            mm=int(tm[10:12])

            bucket_mm=(mm//5)*5

            key=f"{ymd}{hh:02d}{bucket_mm:02d}"

            grouped.setdefault(key,[]).append(r)



        out=[]

        for key in sorted(grouped):

            g=grouped[key]

            g.sort(key=lambda x:x['time'])



            out.append({

                'time':key+'00',

                'open':g[0]['open'],

                'high':max(x['high'] for x in g),

                'low':min(x['low'] for x in g),

                'close':g[-1]['close'],

                'volume':sum(x['volume'] for x in g),

                'minute_bars':len(g)

            })



        return {

            'ok':True,

            'symbol':d.get('symbol'),

            'tick_minutes':5,

            'source':'canonical_1m',

            'pages':d.get('pages'),

            'raw_count':d.get('raw_count'),

            'one_minute_count':d.get('count'),

            'count':len(out),

            'bars':out,

            'oldest':out[0] if out else None,

            'latest':out[-1] if out else None

        }



    def quote(self, stk_cd='005930'):
        code=_clean_code(stk_cd)
        r=requests.post(
            self.k.s.rest_base+'/api/dostk/mrkcond',
            headers=self.k.headers('ka10004'),
            json={'stk_cd':code},
            timeout=20
        )
        d=r.json()
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"ka10004 {code}: {d.get('return_code')} {d.get('return_msg')}")
        return {'ok':True,'api_id':'ka10004','symbol':code,'raw_symbol':stk_cd,
                'checked_at':datetime.now(timezone.utc).isoformat(),'raw':d}

    def _post_rank(self, api_id, body, preferred_key):
        r=requests.post(
            self.k.s.rest_base+'/api/dostk/rkinfo',
            headers=self.k.headers(api_id),
            json=body,
            timeout=30
        )
        d=r.json()
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"{api_id}: {d.get('return_code')} {d.get('return_msg')}")
        return _first_list(d, preferred_key)

    def _trading_value(self, mrkt_tp):
        return self._post_rank('ka10032',
            {'mrkt_tp':mrkt_tp,'mang_stk_incls':'0','stex_tp':'3'},
            'trde_prica_upper')

    def _today_volume(self, mrkt_tp):
        return self._post_rank('ka10030',{
            'mrkt_tp':mrkt_tp,'sort_tp':'1','mang_stk_incls':'1','crd_tp':'0',
            'trde_qty_tp':'0','pric_tp':'0','trde_prica_tp':'0',
            'mrkt_open_tp':'0','stex_tp':'3'
        },'tdy_trde_qty_upper')

    def _volume_surge(self, mrkt_tp):
        return self._post_rank('ka10023',{
            'mrkt_tp':mrkt_tp,'sort_tp':'2','tm_tp':'2','trde_qty_tp':'5',
            'stk_cnd':'1','pric_tp':'0','stex_tp':'3','tm':''
        },'trde_qty_sdnin')

    def _change_rate(self, mrkt_tp, sort_tp='1'):
        return self._post_rank('ka10027',{
            'mrkt_tp':mrkt_tp,'sort_tp':sort_tp,'trde_qty_cnd':'0010',
            'stk_cnd':'1','crd_cnd':'0','updown_incls':'1',
            'pric_cnd':'8','trde_prica_cnd':'10','stex_tp':'3'
        },'pred_pre_flu_rt_upper')

    def _expected_change_rate(self, mrkt_tp, sort_tp='1'):
        # Official Kiwoom ka10029 request schema verified from the official example.
        return self._post_rank('ka10029',{
            'mrkt_tp':mrkt_tp,
            'sort_tp':sort_tp,
            'trde_qty_cnd':'0',
            'stk_cnd':'4',
            'crd_cnd':'0',
            'pric_cnd':'8',
            'stex_tp':'3'
        },'exp_cntr_flu_rt_upper')

    def expected_execution_snapshot(self):
        merged={}
        source_counts={}
        for mrkt_tp,market in [('001','KOSPI'),('101','KOSDAQ')]:
            for label,sort_tp in [('expected_gainer','1'),('expected_loser','4')]:
                rows=self._expected_change_rate(mrkt_tp,sort_tp)
                source_counts[f'{market}_{label}']=len(rows)
                for idx,x in enumerate(rows,1):
                    raw=str(x.get('stk_cd') or '').strip()
                    sym=_clean_code(raw)
                    if not sym:
                        continue
                    row=merged.setdefault(sym,{
                        'symbol':sym,'raw_symbol':raw,
                        'name':str(x.get('stk_nm') or '').strip(),
                        'market':market,'expected_rank':9999,
                        'expected_side':None,'expected_price':0.0,
                        'base_price':0.0,'expected_change_pct':0.0,
                        'expected_qty':0.0,'sell_qty':0.0,'sell_bid':0.0,
                        'buy_bid':0.0,'buy_qty':0.0
                    })
                    if not row.get('name'):
                        row['name']=str(x.get('stk_nm') or '').strip()
                    row['expected_rank']=min(row['expected_rank'],idx)
                    row['expected_side']='UP' if label=='expected_gainer' else 'DOWN'
                    row['expected_price']=abs(_num(x.get('exp_cntr_pric')))
                    row['base_price']=abs(_num(x.get('base_pric')))
                    row['expected_change_pct']=_num(x.get('flu_rt'))
                    row['expected_qty']=abs(_num(x.get('exp_cntr_qty')))
                    row['sell_qty']=abs(_num(x.get('sel_req')))
                    row['sell_bid']=abs(_num(x.get('sel_bid')))
                    row['buy_bid']=abs(_num(x.get('buy_bid')))
                    row['buy_qty']=abs(_num(x.get('buy_req')))
        rows=sorted(
            merged.values(),
            key=lambda r:(r.get('expected_rank',9999),-abs(r.get('expected_change_pct',0)))
        )
        return {
            'updated_at':datetime.now(timezone.utc).isoformat(),
            'rows':rows,
            'count':len(rows),
            'source_counts':source_counts,
            'api_id':'ka10029'
        }

    def _upsert(self, merged, x, market, source, rank):
        raw_symbol=str(x.get('stk_cd') or '').strip()
        symbol=_clean_code(raw_symbol)
        if not symbol:
            return
        r=merged.setdefault(symbol,{
            'symbol':symbol,'raw_symbol':raw_symbol,
            'name':str(x.get('stk_nm') or '').strip(),
            'market':market,'exchange':'INTEGRATED',
            'price':0.0,'change_pct':0.0,'volume':0.0,'trading_value':0.0,
            'surge_pct':0.0,'sources':[],'source_count':0,
            'value_rank':9999,'volume_rank':9999,'surge_rank':9999,
            'gainer_rank':9999,'loser_rank':9999
        })
        if not r['name']:
            r['name']=str(x.get('stk_nm') or '').strip()
        r['raw_symbol']=raw_symbol or r.get('raw_symbol')
        price=abs(_num(x.get('cur_prc')))
        if price: r['price']=price
        chg=_num(x.get('flu_rt'))
        if chg or r['change_pct']==0: r['change_pct']=chg
        vol=abs(_num(x.get('now_trde_qty') or x.get('trde_qty') or x.get('acc_trde_qty')))
        if vol: r['volume']=max(r['volume'],vol)
        tv=abs(_num(x.get('trde_prica') or x.get('trde_amt') or x.get('acc_trde_prica')))
        if tv: r['trading_value']=max(r['trading_value'],tv)
        surge=_num(x.get('sdnin_rt'))
        if surge: r['surge_pct']=surge
        if source not in r['sources']:
            r['sources'].append(source)
        r['source_count']=len(r['sources'])
        if source=='value': r['value_rank']=min(r['value_rank'],rank)
        elif source=='volume': r['volume_rank']=min(r['volume_rank'],rank)
        elif source=='surge': r['surge_rank']=min(r['surge_rank'],rank)
        elif source=='gainer': r['gainer_rank']=min(r['gainer_rank'],rank)
        elif source=='loser': r['loser_rank']=min(r['loser_rank'],rank)

    def discover(self, limit=50):
        merged={}
        source_counts={}
        market_breakdown={'KOSPI':0,'KOSDAQ':0}
        for mrkt_tp,market in [('001','KOSPI'),('101','KOSDAQ')]:
            source_jobs=[
                ('value', self._trading_value(mrkt_tp)),
                ('volume', self._today_volume(mrkt_tp)),
                ('surge', self._volume_surge(mrkt_tp)),
                ('gainer', self._change_rate(mrkt_tp,'1')),
                ('loser', self._change_rate(mrkt_tp,'3')),
            ]
            seen=set()
            for source,rows in source_jobs:
                source_counts[f'{market}_{source}']=len(rows)
                for idx,x in enumerate(rows,1):
                    self._upsert(merged,x,market,source,idx)
                    sym=_clean_code(x.get('stk_cd'))
                    if sym: seen.add(sym)
            market_breakdown[market]=len(seen)

        rows=[]
        for r in merged.values():
            # practical day-trading quality gate
            if r['price'] < 1000:
                continue
            if r['volume'] and r['volume'] < 10000 and r['source_count'] < 2:
                continue

            def rank_pts(rank, weight):
                if rank>=9999: return 0.0
                return weight*max(0.0,1.0-(rank-1)/50.0)

            score=0.0
            score += rank_pts(r['value_rank'],28)
            score += rank_pts(r['volume_rank'],18)
            score += rank_pts(r['surge_rank'],18)
            score += max(rank_pts(r['gainer_rank'],14), rank_pts(r['loser_rank'],14))
            score += min(12.0, max(0,r['source_count']-1)*4.0)
            score += min(10.0, abs(r['change_pct'])*1.2)
            raw_score=min(100.0,score)

            # KOREA risk layer: separate "interesting" from "safe to chase".
            ap=abs(r['change_pct'])
            if ap >= 25:
                chase_risk='EXTREME'
                chase_penalty=18.0
            elif ap >= 20:
                chase_risk='HIGH'
                chase_penalty=12.0
            elif ap >= 12:
                chase_risk='MEDIUM'
                chase_penalty=6.0
            else:
                chase_risk='NORMAL'
                chase_penalty=0.0

            # Huge surge percentages often occur in event-driven names; flag rather than discard.
            if r.get('surge_pct',0) >= 3000:
                surge_risk='EXTREME'
                chase_penalty += 4.0
            elif r.get('surge_pct',0) >= 1000:
                surge_risk='HIGH'
                chase_penalty += 2.0
            else:
                surge_risk='NORMAL'

            r['raw_score']=round(raw_score,1)
            r['chase_risk']=chase_risk
            r['surge_risk']=surge_risk
            r['risk_penalty']=round(chase_penalty,1)
            r['score']=round(max(0.0,raw_score-chase_penalty),1)
            r['bias']='LONG' if r['change_pct']>0 else ('SHORT' if r['change_pct']<0 else 'WATCH')
            r['score_model']='KOREA_CURRENT_V1_GAMMA'
            r['source_text']=','.join(r['sources'])
            rows.append(r)

        meta={}; cap_rank_enabled=False; meta_error=None
        try:
            meta,cap_rank_enabled=self._load_stock_metadata(False)
        except Exception as e:
            meta_error=str(e)

        passed=[]; risk_rows=[]; reject_rows=[]
        for r in rows:
            q=grade_korea_row(r,meta.get(r.get('symbol')) if meta else None,cap_rank_enabled)
            if q.get('quality_grade') in ('A','B_EVENT'):
                passed.append(q)
            elif q.get('quality_grade')=='C_HIGH_RISK':
                risk_rows.append(q)
            else:
                reject_rows.append(q)

        passed=sorted(passed,key=lambda x:(x['score'],x['source_count'],x['trading_value']),reverse=True)[:max(10,int(limit))]
        top10=passed[:10]

        self.discovery={
            'updated_at':datetime.now(timezone.utc).isoformat(),
            'rows':passed,'count':len(passed),'top10':top10,
            'source_counts':source_counts,'market_breakdown':market_breakdown,
            'score_model':'KOREA_CURRENT_V1_GAMMA',
            'sources':['ka10032','ka10030','ka10023','ka10027'],
            'quality_gate':'QUALITY_GATE_KOREA_V1_1',
            'quality_counts':{
                'A':len([r for r in passed if r.get('quality_grade')=='A']),
                'B_EVENT':len([r for r in passed if r.get('quality_grade')=='B_EVENT']),
                'C_HIGH_RISK':len(risk_rows),
                'REJECT':len(reject_rows)
            },
            'quality_risk_rows':risk_rows[:50],
            'quality_reject_rows':reject_rows[:50],
            'metadata_count':len(meta),
            'market_cap_rank_enabled':bool(cap_rank_enabled),
            'metadata_error':meta_error
        }
        return self.discovery



    def _load_stock_metadata(self, force=False):
        now=datetime.now(timezone.utc)
        if self.stock_meta and self.stock_meta_updated_at and not force:
            if (now-self.stock_meta_updated_at).total_seconds()<21600:
                return self.stock_meta,self.cap_rank_enabled
        rows=[]
        for mrkt_tp in ('0','10'):
            r=requests.post(
                self.k.s.rest_base+'/api/dostk/stkinfo',
                headers=self.k.headers('ka10099'),
                json={'mrkt_tp':mrkt_tp},
                timeout=30
            )
            d=r.json()
            if d.get('return_code') not in (None,0):
                raise RuntimeError(f"ka10099/{mrkt_tp}: {d.get('return_code')} {d.get('return_msg')}")
            part=d.get('list') or []
            if isinstance(part,list):
                rows.extend([x for x in part if isinstance(x,dict)])
        self.stock_meta,self.cap_rank_enabled=build_korea_metadata(rows)
        self.stock_meta_updated_at=now
        return self.stock_meta,self.cap_rank_enabled

    def _trade_strength(self, stk_cd):
        """Official ka10046 체결강도추이시간별."""
        r=requests.post(
            self.k.s.rest_base+'/api/dostk/mrkcond',
            headers=self.k.headers('ka10046'),
            json={'stk_cd':stk_cd},
            timeout=20
        )
        d=r.json()
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"ka10046 {stk_cd}: {d.get('return_code')} {d.get('return_msg')}")
        rows=d.get('cntr_str_tm') or []
        latest=rows[0] if rows and isinstance(rows[0],dict) else {}
        return {
            'trade_strength':_num(latest.get('cntr_str')),
            'trade_strength_5m':_num(latest.get('cntr_str_5min')),
            'trade_strength_20m':_num(latest.get('cntr_str_20min')),
            'trade_strength_60m':_num(latest.get('cntr_str_60min')),
            'trade_strength_time':latest.get('cntr_tm'),
            'trade_strength_price':abs(_num(latest.get('cur_prc'))),
            'trade_strength_change_pct':_num(latest.get('flu_rt')),
        }

    def _vi_triggered(self):
        """Official ka10054 변동성완화장치발동종목."""
        body={
            'mrkt_tp':'000',
            'bf_mkrt_tp':'1',
            'motn_tp':'0',
            'skip_stk':'000000000',
            'trde_qty_tp':'0',
            'min_trde_qty':'0',
            'max_trde_qty':'0',
            'trde_prica_tp':'0',
            'min_trde_prica':'0',
            'max_trde_prica':'0',
            'motn_drc':'0',
            'stex_tp':'3',
            'stk_cd':''
        }
        r=requests.post(
            self.k.s.rest_base+'/api/dostk/stkinfo',
            headers=self.k.headers('ka10054'),
            json=body,
            timeout=25
        )
        d=r.json()
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"ka10054: {d.get('return_code')} {d.get('return_msg')}")
        rows=d.get('motn_stk') or []
        out={}
        for x in rows:
            if not isinstance(x,dict):
                continue
            sym=_clean_code(x.get('stk_cd'))
            if not sym:
                continue
            out[sym]={
                'vi_triggered':True,
                'vi_type':x.get('viaplc_tp'),
                'vi_trigger_price':abs(_num(x.get('motn_pric'))),
                'vi_release_time':x.get('virelis_time'),
                'vi_count':int(_num(x.get('vimotn_cnt')) or 0),
                'vi_open_change_pct':_num(x.get('open_pric_pre_flu_rt')),
                'vi_direction_gap_dynamic':_num(x.get('dynm_dispty_rt')),
                'vi_direction_gap_static':_num(x.get('static_dispty_rt')),
            }
        return out

    @staticmethod
    def _kst_market_open():
        from zoneinfo import ZoneInfo
        kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
        mins=kst.hour*60+kst.minute
        return kst.weekday()<5 and (9*60) <= mins <= (15*60+30)

    def refresh_intraday_pulse(self, top_n=10, force_probe=False):
        if not self.discovery.get('rows'):
            self.discover(50)

        market_open=self._kst_market_open()
        base=list(self.discovery.get('top10') or [])[:max(1,int(top_n))]

        if not market_open and not force_probe:
            rows=[]
            for r in base:
                x=dict(r)
                x.update({
                    'live_score':x.get('score'),
                    'live_score_model':'KOREA_CURRENT_V2_LIVE',
                    'pulse_status':'MARKET_CLOSED_REFERENCE',
                    'trade_strength':None,'trade_strength_5m':None,
                    'trade_strength_20m':None,'trade_strength_60m':None,
                    'strength_composite':None,'strength_adjustment':0.0,
                    'strength_bias':'N/A','vi_triggered':False,'vi_count':0,
                    'vi_penalty':0.0
                })
                rows.append(x)
            self.intraday_pulse={
                'updated_at':datetime.now(timezone.utc).isoformat(),
                'market_open':False,'status':'MARKET_CLOSED_REFERENCE',
                'rows':rows,'top10':rows[:10],'vi_count':0,
                'score_model':'KOREA_CURRENT_V2_LIVE'
            }
            return self.intraday_pulse

        vi_map={}
        vi_error=None
        try:
            vi_map=self._vi_triggered()
        except Exception as e:
            vi_error=str(e)

        rows=[]
        for r in base:
            x=dict(r)
            raw=x.get('raw_symbol') or x.get('symbol')
            strength={}
            strength_error=None
            try:
                strength=self._trade_strength(raw)
            except Exception as e:
                strength_error=str(e)

            vals=[]
            weights=[]
            for key,w in [
                ('trade_strength',0.40),
                ('trade_strength_5m',0.30),
                ('trade_strength_20m',0.20),
                ('trade_strength_60m',0.10),
            ]:
                v=_num(strength.get(key))
                if v>0:
                    vals.append(v*w); weights.append(w)
            composite=(sum(vals)/sum(weights)) if weights else None

            raw_adj=0.0
            strength_bias='N/A'
            if composite is not None:
                raw_adj=max(-10.0,min(10.0,(composite-100.0)/5.0))
                if composite >= 110:
                    strength_bias='BUY'
                elif composite <= 90:
                    strength_bias='SELL'
                else:
                    strength_bias='NEUTRAL'

            directional_adj=raw_adj if x.get('bias')=='LONG' else (-raw_adj if x.get('bias')=='SHORT' else 0.0)

            vi=vi_map.get(x.get('symbol')) or {}
            vi_penalty=0.0
            if vi:
                vi_penalty=8.0 + min(4.0,max(0,(vi.get('vi_count') or 0)-1)*1.0)

            gamma=float(x.get('score') or 0)
            live_score=round(max(0.0,min(100.0,gamma+directional_adj-vi_penalty)),1)

            x.update(strength)
            x.update(vi)
            x.update({
                'live_score':live_score,
                'live_score_model':'KOREA_CURRENT_V2_LIVE',
                'pulse_status':'LIVE' if market_open else 'FORCED_DIAGNOSTIC',
                'strength_composite':round(composite,1) if composite is not None else None,
                'strength_adjustment':round(directional_adj,1),
                'strength_bias':strength_bias,
                'strength_error':strength_error,
                'vi_triggered':bool(vi),
                'vi_count':vi.get('vi_count',0) if vi else 0,
                'vi_penalty':vi_penalty,
            })
            rows.append(x)

        rows=sorted(rows,key=lambda r:(r.get('live_score',0),r.get('score',0)),reverse=True)
        self.intraday_pulse={
            'updated_at':datetime.now(timezone.utc).isoformat(),
            'market_open':market_open,
            'status':'LIVE' if market_open else 'FORCED_DIAGNOSTIC',
            'rows':rows,'top10':rows[:10],
            'vi_count':len(vi_map),
            'vi_error':vi_error,
            'score_model':'KOREA_CURRENT_V2_LIVE'
        }
        return self.intraday_pulse

    def status(self):
        return {
            'ok':True,'phase':'KOREA_RISK_NORMALIZATION','market':'KOREA',
            'adapter_ready':True,'ranking_live':True,'score_live':True,
            'preopen_live':True,'score_model':'KOREA_CURRENT_V1_GAMMA',
            'intraday_pulse_live':True,
            'intraday_score_model':'KOREA_CURRENT_V2_LIVE',
            'intraday_status':self.intraday_pulse.get('status'),
            'universe_count':self.discovery.get('count',0),
            'updated_at':self.discovery.get('updated_at'),
            'quality_gate':self.discovery.get('quality_gate','QUALITY_GATE_KOREA_V1_1'),
            'quality_counts':self.discovery.get('quality_counts') or {},
            'market_cap_rank_enabled':bool(self.discovery.get('market_cap_rank_enabled')),
            'metadata_count':self.discovery.get('metadata_count',0),
            'sources':['ka10032 거래대금','ka10030 당일거래량','ka10023 거래량급증','ka10027 등락률(상승/하락)'],
            'next_sources':[
                {'api_id':'ka10029','name':'예상체결등락률상위','status':'LIVE_PREOPEN'},
                {'api_id':'ka10046','name':'체결강도추이시간별','status':'LIVE_INTRADAY'},
                {'api_id':'ka10054','name':'VI 발동종목','status':'LIVE_INTRADAY'}
            ]
        }
