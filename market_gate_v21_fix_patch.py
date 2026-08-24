from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

PATCH=r'''

    # ===== MARKET GATE V2.1 FIX =====
    def market_gate_v21(self):
        from datetime import datetime, timezone

        base=self.market_gate_v1()
        comps=[dict(x) for x in (base.get('components') or [])]

        def replace_component(name,newrow):
            for i,x in enumerate(comps):
                if x.get('name')==name:
                    comps[i]=newrow
                    return
            comps.append(newrow)

        # 4) Foreign + institution flow: ka10051 belongs to /api/dostk/sect.
        try:
            flow_rows=[]; flow_calls=[]
            for mrkt_tp in ('001','101'):
                r=requests.post(
                    self.k.s.rest_base+'/api/dostk/sect',
                    headers=self.k.headers('ka10051'),
                    json={'mrkt_tp':mrkt_tp,'amt_qty_tp':'1','base_dt_tp':'0','stex_tp':'3'},
                    timeout=25,
                )
                d=r.json()
                flow_calls.append({'mrkt_tp':mrkt_tp,'return_code':d.get('return_code'),'return_msg':d.get('return_msg')})
                if d.get('return_code') not in (None,0):
                    continue
                flow_rows.extend(x for x in (d.get('inds_netprps') or []) if isinstance(x,dict))

            agg=[x for x in flow_rows if '종합' in str(x.get('inds_nm') or '')]
            used=agg or flow_rows
            foreign=sum(_num(x.get('frgnr_netprps')) for x in used)
            inst=sum(_num(x.get('orgn_netprps')) for x in used)
            individual=sum(_num(x.get('ind_netprps')) for x in used)
            smart=foreign+inst
            denom=max(50.0,abs(foreign)+abs(inst)+abs(individual))
            pressure=smart/denom
            flow_score=max(0.0,min(100.0,50.0+50.0*pressure))
            magnitude=abs(foreign)+abs(inst)+abs(individual)
            quality='LIVE' if magnitude>=50 else 'THIN_PREOPEN'
            replace_component('foreign_institution_flow',{
                'name':'foreign_institution_flow','status':quality,'weight':20,
                'score':round(flow_score,1),
                'detail':{
                    'rows':len(flow_rows),'aggregate_rows':len(agg),
                    'foreign_net':foreign,'institution_net':inst,'individual_net':individual,
                    'smart_money_net':smart,'magnitude':magnitude,'calls':flow_calls,
                    'source':'ka10051','uri':'/api/dostk/sect'
                }
            })
        except Exception as e:
            replace_component('foreign_institution_flow',{
                'name':'foreign_institution_flow','status':'UNKNOWN','weight':20,'score':None,
                'error':str(e)[:180],'source':'ka10051'
            })

        # 5) KOSPI/KOSDAQ 20-day trend: use official index daily chart ka20006.
        try:
            base_dt=datetime.now(timezone.utc).strftime('%Y%m%d')
            idx=[]
            for label,inds_cd in [('KOSPI','001'),('KOSDAQ','101')]:
                r=requests.post(
                    self.k.s.rest_base+'/api/dostk/chart',
                    headers=self.k.headers('ka20006'),
                    json={'inds_cd':inds_cd,'base_dt':base_dt},
                    timeout=25,
                )
                d=r.json()
                if d.get('return_code') not in (None,0):
                    idx.append({'market':label,'ok':False,'error':d.get('return_msg'),'return_code':d.get('return_code')})
                    continue
                raw=d.get('inds_dt_pole_qry') or []
                rows=[]
                for x in raw:
                    if not isinstance(x,dict):
                        continue
                    c=abs(_num(x.get('cur_prc')))
                    dt=str(x.get('dt') or '').replace('-','').strip()
                    if c>0:
                        rows.append((dt,c))
                # API is newest-first. Ensure deterministic newest-first by date when dates exist.
                if rows and all(dt for dt,_ in rows):
                    rows=sorted(rows,key=lambda z:z[0],reverse=True)
                closes=[c for _,c in rows]
                dates=[dt for dt,_ in rows]
                if len(closes)>=20:
                    current=closes[0]
                    ma20=sum(closes[:20])/20.0
                    recent5=sum(closes[:5])/5.0 if len(closes)>=5 else current
                    prev5=sum(closes[5:10])/5.0 if len(closes)>=10 else recent5
                    above=current>=ma20
                    rising=recent5>=prev5
                    sc=100.0 if (above and rising) else 70.0 if above else 45.0 if rising else 20.0
                    idx.append({
                        'market':label,'ok':True,'current':round(current,4),'ma20':round(ma20,4),
                        'above_ma20':above,'ma20_momentum_up':rising,'score':sc,
                        'daily_rows':len(closes),'latest_date':dates[0] if dates else None,
                    })
                else:
                    idx.append({'market':label,'ok':False,'daily_rows':len(closes),'error':'insufficient_daily_rows'})

            valid=[x for x in idx if x.get('ok') and x.get('score') is not None]
            if valid:
                trend_score=sum(float(x['score']) for x in valid)/len(valid)
                replace_component('index_20d_trend',{
                    'name':'index_20d_trend','status':'LIVE','weight':15,
                    'score':round(trend_score,1),
                    'detail':{'indexes':idx,'source':'ka20006','uri':'/api/dostk/chart'}
                })
            else:
                replace_component('index_20d_trend',{
                    'name':'index_20d_trend','status':'UNKNOWN','weight':15,'score':None,
                    'detail':{'indexes':idx,'source':'ka20006','uri':'/api/dostk/chart'}
                })
        except Exception as e:
            replace_component('index_20d_trend',{
                'name':'index_20d_trend','status':'UNKNOWN','weight':15,'score':None,
                'error':str(e)[:180],'source':'ka20006'
            })

        replace_component('us_overnight',{
            'name':'us_overnight','status':'NOT_CONNECTED','weight':5,'score':None,
            'planned_source':'USA previous regular-session close context'
        })

        live=[c for c in comps if c.get('score') is not None]
        used_weight=sum(float(c.get('weight') or 0) for c in live)
        raw=sum(float(c['score'])*float(c.get('weight') or 0) for c in live)
        score=(raw/used_weight) if used_weight else None
        confidence=round(used_weight/100.0,2)
        by={c.get('name'):c for c in comps}
        investor_live=by.get('foreign_institution_flow',{}).get('score') is not None
        index_live=by.get('index_20d_trend',{}).get('score') is not None
        core_ready=bool(investor_live and index_live)

        if score is None:
            state='UNKNOWN'
        elif score>=70:
            state='AGGRESSIVE' if confidence>=0.90 and core_ready else 'NORMAL'
        elif score>=55:
            state='NORMAL'
        elif score>=40:
            state='DEFENSIVE'
        else:
            state='CASH'

        return {
            'ok':True,'version':'MARKET_GATE_V2_1_FIX','market':'KOREA',
            'state':state,'score':None if score is None else round(score,1),
            'confidence':confidence,'used_weight':used_weight,'core_ready':core_ready,
            'components':comps,
            'rule':'AGGRESSIVE>=70 only when confidence>=0.90 and investor+index core are live; NORMAL>=55, DEFENSIVE>=40, CASH<40',
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
'''


def main():
    s=KOREA.read_text()
    if 'def market_gate_v21(self)' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,PATCH+'\n'+anchor,1)
        KOREA.write_text(s)

    a=API.read_text()
    old='return await asyncio.to_thread(korea.market_gate_v2)'
    new='return await asyncio.to_thread(korea.market_gate_v21)'
    if old in a:
        a=a.replace(old,new,1)
    elif new not in a:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: market gate v2 endpoint')
    API.write_text(a)
    print('MARKET_GATE_V21_FIX_PATCH_OK')

if __name__=='__main__': main()
