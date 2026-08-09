
from __future__ import annotations
from datetime import datetime, timezone
import requests, re

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

        rows=sorted(rows,key=lambda x:(x['score'],x['source_count'],x['trading_value']),reverse=True)[:max(20,int(limit))]
        top10=rows[:10]

        self.discovery={
            'updated_at':datetime.now(timezone.utc).isoformat(),
            'rows':rows,'count':len(rows),'top10':top10,
            'source_counts':source_counts,'market_breakdown':market_breakdown,
            'score_model':'KOREA_CURRENT_V1_GAMMA',
            'sources':['ka10032','ka10030','ka10023','ka10027']
        }
        return self.discovery

    def status(self):
        return {
            'ok':True,'phase':'KOREA_RISK_NORMALIZATION','market':'KOREA',
            'adapter_ready':True,'ranking_live':True,'score_live':True,
            'preopen_live':True,'score_model':'KOREA_CURRENT_V1_GAMMA',
            'universe_count':self.discovery.get('count',0),
            'updated_at':self.discovery.get('updated_at'),
            'sources':['ka10032 거래대금','ka10030 당일거래량','ka10023 거래량급증','ka10027 등락률(상승/하락)'],
            'next_sources':[
                {'api_id':'ka10029','name':'예상체결등락률상위','status':'LIVE_PREOPEN'},
                {'api_id':'ka10046','name':'체결강도추이시간별','status':'SCORE_NEXT'},
                {'api_id':'ka10054','name':'VI 발동종목','status':'SCORE_NEXT'}
            ]
        }
