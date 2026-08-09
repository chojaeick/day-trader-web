from __future__ import annotations
from datetime import datetime, timezone
import requests

def _num(v):
    try:
        s=str(v).replace(',', '').replace('+','').strip()
        return 0.0 if s in ('','None','null') else float(s)
    except Exception:
        return 0.0

def _first_list_payload(d:dict):
    if isinstance(d.get('trde_prica_upper'), list): return d.get('trde_prica_upper') or []
    for v in d.values():
        if isinstance(v,list): return v
    return []

class KoreaMarketAdapter:
    def __init__(self, kiwoom_client):
        self.k=kiwoom_client
        self.discovery={'updated_at':None,'rows':[],'count':0,'source':'ka10032','market_breakdown':{'KOSPI':0,'KOSDAQ':0},'top10':[]}

    def quote(self, stk_cd:str='005930'):
        code=str(stk_cd or '').strip()
        r=requests.post(self.k.s.rest_base+'/api/dostk/mrkcond',headers=self.k.headers('ka10004'),json={'stk_cd':code},timeout=20)
        d=r.json()
        if d.get('return_code') not in (None,0): raise RuntimeError(f"ka10004 {code}: {d.get('return_code')} {d.get('return_msg')}")
        return {'ok':True,'api_id':'ka10004','symbol':code,'checked_at':datetime.now(timezone.utc).isoformat(),'raw':d}

    def _rank_trading_value(self,mrkt_tp:str):
        body={'mrkt_tp':mrkt_tp,'mang_stk_incls':'0','stex_tp':'3'}
        r=requests.post(self.k.s.rest_base+'/api/dostk/rkinfo',headers=self.k.headers('ka10032'),json=body,timeout=25)
        d=r.json()
        if d.get('return_code') not in (None,0): raise RuntimeError(f"ka10032/{mrkt_tp}: {d.get('return_code')} {d.get('return_msg')}")
        return _first_list_payload(d)

    def discover(self,limit:int=40):
        merged={}; breakdown={'KOSPI':0,'KOSDAQ':0}
        for mrkt_tp,label in [('001','KOSPI'),('101','KOSDAQ')]:
            rows=self._rank_trading_value(mrkt_tp); breakdown[label]=len(rows)
            for idx,x in enumerate(rows,1):
                sym=str(x.get('stk_cd') or '').strip(); price=abs(_num(x.get('cur_prc')))
                if not sym or price<=0: continue
                row={'symbol':sym,'name':str(x.get('stk_nm') or '').strip(),'market':label,'exchange':'INTEGRATED','price':price,'change_pct':_num(x.get('flu_rt')),'volume':abs(_num(x.get('now_trde_qty') or x.get('trde_qty') or x.get('acc_trde_qty'))),'trading_value':abs(_num(x.get('trde_prica') or x.get('acc_trde_prica'))),'value_rank':int(_num(x.get('now_rank') or idx) or idx),'source':'ka10032'}
                if sym not in merged or row['value_rank'] < merged[sym].get('value_rank',9999): merged[sym]=row
        rows=sorted(merged.values(),key=lambda r:(r.get('value_rank',9999),-r.get('trading_value',0)))[:max(10,int(limit))]
        max_rank=max([r['value_rank'] for r in rows],default=1)
        for r in rows:
            rank_score=max(0.0,60.0*(1-(r['value_rank']-1)/max(1,max_rank)))
            momentum=min(20.0,abs(r['change_pct'])*2.0)
            direction_bonus=10.0 if r['change_pct']>0 else (5.0 if r['change_pct']==0 else 0.0)
            r['score']=round(min(100.0,rank_score+momentum+direction_bonus),1)
            r['bias']='LONG' if r['change_pct']>=0 else 'SHORT'
            r['score_model']='KOREA_CURRENT_V1_ALPHA'
        top10=sorted(rows,key=lambda r:(r['score'],-r['value_rank']),reverse=True)[:10]
        self.discovery={'updated_at':datetime.now(timezone.utc).isoformat(),'rows':rows,'count':len(rows),'source':'ka10032','market_breakdown':breakdown,'top10':top10}
        return self.discovery

    def status(self):
        return {'ok':True,'phase':'KOREA_UNIVERSE_TOP10_ALPHA','market':'KOREA','adapter_ready':True,'quote_probe_ready':True,'ranking_live':True,'score_live':True,'preopen_live':False,'score_model':'KOREA_CURRENT_V1_ALPHA','universe_count':self.discovery.get('count',0),'updated_at':self.discovery.get('updated_at'),'source':'ka10032 거래대금상위','next_sources':[{'api_id':'ka10030','name':'당일거래량상위','status':'V2.5.2'},{'api_id':'ka10023','name':'거래량급증','status':'V2.5.2'},{'api_id':'ka10027','name':'전일대비등락률상위','status':'V2.5.2'},{'api_id':'ka10029','name':'예상체결등락률상위','status':'PREOPEN'},{'api_id':'ka10046','name':'체결강도추이시간별','status':'SCORE_ENHANCEMENT'},{'api_id':'ka10054','name':'VI 발동종목','status':'SCORE_ENHANCEMENT'}]}
