from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

KOREA_PATCH=r'''

    # ===== DAYTRADE ENTRY V1 =====
    def daytrade_entry_v1(self, limit=10, eval_limit=5, max_pages=1):
        """Signal-only intraday entry engine.

        Flow: Market Gate -> ranking-based leader finder -> 1m volatility breakout trigger.
        No order placement. Fujimoto is intentionally excluded; it belongs to 2-10d swing.
        """
        from datetime import datetime, timezone

        limit=max(1,min(int(limit),50))
        eval_limit=max(1,min(int(eval_limit),10))
        max_pages=max(1,min(int(max_pages),2))

        # 1) Market gate
        if hasattr(self,'market_gate_v21'):
            gate=self.market_gate_v21()
        elif hasattr(self,'market_gate_v2'):
            gate=self.market_gate_v2()
        else:
            gate=self.market_gate_v1()
        gate_state=str(gate.get('state') or 'UNKNOWN').upper()
        gate_score=gate.get('score')
        blocked=(gate_state in {'CASH','UNKNOWN'})

        # 2) Finder: value + volume + gain ranks across KOSPI/KOSDAQ.
        merged={}
        sources=[]

        def upsert(x, market, lane, rank):
            sym=_clean_code(x.get('stk_cd'))
            if not sym:
                return
            r=merged.setdefault(sym,{
                'symbol':sym,
                'name':str(x.get('stk_nm') or '').strip(),
                'market':market,
                'value_rank':9999,
                'volume_rank':9999,
                'gain_rank':9999,
                'change_pct':_num(x.get('flu_rt')),
                'rank_sources':[],
            })
            if not r.get('name'):
                r['name']=str(x.get('stk_nm') or '').strip()
            if lane=='VALUE': r['value_rank']=min(r['value_rank'],rank)
            elif lane=='VOLUME': r['volume_rank']=min(r['volume_rank'],rank)
            elif lane=='GAIN': r['gain_rank']=min(r['gain_rank'],rank)
            if lane not in r['rank_sources']:
                r['rank_sources'].append(lane)
            ch=_num(x.get('flu_rt'))
            if abs(ch)>abs(_num(r.get('change_pct'))):
                r['change_pct']=ch

        for mrkt_tp,market in [('001','KOSPI'),('101','KOSDAQ')]:
            try:
                rows=self._trading_value(mrkt_tp) or []
                sources.append({'market':market,'lane':'VALUE','count':len(rows)})
                for i,x in enumerate(rows[:100],1): upsert(x,market,'VALUE',i)
            except Exception as e:
                sources.append({'market':market,'lane':'VALUE','error':str(e)[:120]})
            try:
                rows=self._today_volume(mrkt_tp) or []
                sources.append({'market':market,'lane':'VOLUME','count':len(rows)})
                for i,x in enumerate(rows[:100],1): upsert(x,market,'VOLUME',i)
            except Exception as e:
                sources.append({'market':market,'lane':'VOLUME','error':str(e)[:120]})
            try:
                rows=self._change_rate(mrkt_tp,'1') or []
                sources.append({'market':market,'lane':'GAIN','count':len(rows)})
                for i,x in enumerate(rows[:100],1): upsert(x,market,'GAIN',i)
            except Exception as e:
                sources.append({'market':market,'lane':'GAIN','error':str(e)[:120]})

        def rank_points(rank, weight):
            if rank>=9999: return 0.0
            return max(0.0, weight*(101.0-min(rank,100))/100.0)

        candidates=[]
        for r in merged.values():
            score=(rank_points(r['value_rank'],40.0)+
                   rank_points(r['volume_rank'],30.0)+
                   rank_points(r['gain_rank'],30.0))
            lane_count=sum(1 for k in ('value_rank','volume_rank','gain_rank') if r[k]<9999)
            # Require at least two independent lanes for a usable leader candidate.
            if lane_count<2:
                continue
            r['finder_score']=round(score,1)
            r['lane_count']=lane_count
            candidates.append(r)
        candidates.sort(key=lambda x:(-float(x.get('finder_score') or 0),x.get('value_rank',9999),x.get('volume_rank',9999)))
        candidates=candidates[:limit]

        # 3) 1-minute breakout evaluation for top candidates only.
        evaluated=0
        for idx,r in enumerate(candidates):
            r.update({
                'evaluated':False,
                'trigger_price':None,
                'last_price':None,
                'breakout':False,
                'volume_confirm':False,
                'entry_score':None,
                'state':'WATCH',
                'signal':'NONE',
                'reason':None,
            })
            if idx>=eval_limit or blocked:
                r['reason']='MARKET_GATE_BLOCKED' if blocked else 'NOT_EVALUATED_THIS_CALL'
                continue
            try:
                d=self.canonical_minute_bars(r['symbol'],max_pages=max_pages)
                bars=d.get('bars') or []
                if len(bars)<4:
                    r['reason']=f'INSUFFICIENT_1M_BARS:{len(bars)}'
                    continue
                prev=bars[-2]
                cur=bars[-1]
                prev_range=max(0.0,float(prev.get('high') or 0)-float(prev.get('low') or 0))
                trigger=float(cur.get('open') or 0)+prev_range*0.5
                last=float(cur.get('close') or 0)
                breakout=bool(trigger>0 and last>trigger)
                recent_vol=[float(x.get('volume') or 0) for x in bars[-12:-2]]
                avg_vol=(sum(recent_vol)/len(recent_vol)) if recent_vol else 0.0
                cur_vol=float(cur.get('volume') or 0)
                volume_confirm=bool(avg_vol>0 and cur_vol>=avg_vol*1.2)

                market_bonus={'AGGRESSIVE':20.0,'NORMAL':12.0,'DEFENSIVE':4.0}.get(gate_state,0.0)
                entry_score=min(100.0,
                    float(r.get('finder_score') or 0)*0.65 +
                    market_bonus +
                    (12.0 if breakout else 0.0) +
                    (8.0 if volume_confirm else 0.0)
                )

                if gate_state=='DEFENSIVE':
                    entry_candidate=breakout and volume_confirm and entry_score>=80
                else:
                    entry_candidate=breakout and entry_score>=72

                state='ENTRY_CANDIDATE' if entry_candidate else ('READY' if breakout else 'WATCH')
                signal='ENTRY_CANDIDATE' if entry_candidate else ('READY' if breakout else 'NONE')
                r.update({
                    'evaluated':True,
                    'bar_time':cur.get('time'),
                    'trigger_price':round(trigger,4),
                    'last_price':round(last,4),
                    'previous_range':round(prev_range,4),
                    'breakout':breakout,
                    'current_volume':cur_vol,
                    'avg_recent_volume':round(avg_vol,2),
                    'volume_confirm':volume_confirm,
                    'entry_score':round(entry_score,1),
                    'state':state,
                    'signal':signal,
                    'reason':'BREAKOUT_CONFIRMED' if entry_candidate else ('BREAKOUT_WAIT_VOLUME_OR_SCORE' if breakout else 'WAIT_BREAKOUT'),
                })
                evaluated+=1
            except Exception as e:
                r['reason']='EVAL_ERROR:'+str(e)[:120]

        rows=sorted(candidates,key=lambda x:(
            0 if x.get('signal')=='ENTRY_CANDIDATE' else 1 if x.get('signal')=='READY' else 2,
            -(float(x.get('entry_score') or 0)),
            -(float(x.get('finder_score') or 0)),
        ))

        return {
            'ok':True,
            'version':'DAYTRADE_ENTRY_V1',
            'signal_only':True,
            'order_placement':False,
            'market_gate':{
                'state':gate_state,
                'score':gate_score,
                'confidence':gate.get('confidence'),
                'core_ready':gate.get('core_ready'),
            },
            'market_blocked':blocked,
            'candidate_count':len(candidates),
            'evaluated_count':evaluated,
            'entry_candidate_count':sum(1 for x in rows if x.get('signal')=='ENTRY_CANDIDATE'),
            'ready_count':sum(1 for x in rows if x.get('signal')=='READY'),
            'formula':'Market Gate + Finder(value/volume/gain ranks) + 1m breakout: current close > current open + 0.5*(previous 1m high-low)',
            'sources':sources,
            'rows':rows,
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
'''

API_PATCH=r'''

@app.get('/api/v5/daytrade-entry/KOREA')
async def v5_daytrade_entry_korea(limit:int=10, eval_limit:int=5, max_pages:int=1):
    return await asyncio.to_thread(korea.daytrade_entry_v1,limit,eval_limit,max_pages)
'''

def main():
    s=KOREA.read_text()
    if 'def daytrade_entry_v1' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,KOREA_PATCH+'\n'+anchor,1)
        KOREA.write_text(s)

    a=API.read_text()
    if '/api/v5/daytrade-entry/KOREA' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1)
        API.write_text(a)
    print('DAYTRADE_ENTRY_V1_PATCH_OK')

if __name__=='__main__': main()
