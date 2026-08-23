from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

KOREA_PATCH=r'''

    # ===== FUJIMOTO SWING DAILY V1 =====
    def fujimoto_swing_daily_v1(self, stk_cd):
        import time as _time
        code=_clean_code(stk_cd)
        now=_time.time()
        if not hasattr(self,'_fujimoto_swing_daily_cache'):
            self._fujimoto_swing_daily_cache={}
        cached=self._fujimoto_swing_daily_cache.get(code)
        if cached and now-float(cached.get('_cached_at',0) or 0)<900:
            return dict(cached)

        rows=[]; next_key=''; pages=0
        while pages<4:
            hdr=self.k.headers('ka10081')
            if next_key:
                hdr['cont-yn']='Y'; hdr['next-key']=next_key
            r=requests.post(self.k.s.rest_base+'/api/dostk/chart',headers=hdr,json={
                'stk_cd':code,
                'base_dt':datetime.now(timezone.utc).astimezone().strftime('%Y%m%d'),
                'upd_stkpc_tp':'1'
            },timeout=25)
            d=r.json()
            if d.get('return_code') not in (None,0):
                raise RuntimeError(f"ka10081 {code}: {d.get('return_code')} {d.get('return_msg')}")
            raw=d.get('stk_dt_pole_chart_qry') or d.get('stk_dt_chart_qry') or []
            if not raw:
                for v in d.values():
                    if isinstance(v,list): raw=v; break
            rows.extend(x for x in raw if isinstance(x,dict))
            pages+=1
            cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
            next_key=str(r.headers.get('next-key') or r.headers.get('Next-Key') or '')
            if cont!='Y' or not next_key: break
            _time.sleep(0.18)

        seq=[]
        for x in rows:
            dt=str(x.get('dt') or x.get('stk_dt') or x.get('base_dt') or '').replace('-','').strip()
            close=abs(_num(x.get('cur_prc') if x.get('cur_prc') is not None else x.get('close')))
            high=abs(_num(x.get('high_pric') if x.get('high_pric') is not None else x.get('high')))
            low=abs(_num(x.get('low_pric') if x.get('low_pric') is not None else x.get('low')))
            if len(dt)>=8 and close>0:
                seq.append((dt[:8],close,high or close,low or close))
        uniq={dt:(c,h,l) for dt,c,h,l in seq}
        seq=[(dt,*uniq[dt]) for dt in sorted(uniq)]
        if len(seq)<60:
            out={'ok':False,'version':'FUJIMOTO_SWING_DAILY_V1','symbol':code,'reason':f'insufficient_daily_rows:{len(seq)}','_cached_at':now}
            self._fujimoto_swing_daily_cache[code]=out
            return dict(out)

        closes=[x[1] for x in seq]
        highs=[x[2] for x in seq]
        lows=[x[3] for x in seq]

        def ema(vals,span):
            a=2.0/(span+1.0); out=[float(vals[0])]
            for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
            return out

        def rsi_series(vals,period=14):
            out=[None]*len(vals)
            if len(vals)<=period: return out
            gains=[]; losses=[]
            for i in range(1,period+1):
                ch=vals[i]-vals[i-1]; gains.append(max(ch,0)); losses.append(max(-ch,0))
            ag=sum(gains)/period; al=sum(losses)/period
            out[period]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
            for i in range(period+1,len(vals)):
                ch=vals[i]-vals[i-1]; g=max(ch,0); l=max(-ch,0)
                ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
                out[i]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
            return out

        ema12=ema(closes,12); ema26=ema(closes,26)
        macd=[a-b for a,b in zip(ema12,ema26)]
        signal=ema(macd,9)
        hist=[a-b for a,b in zip(macd,signal)]
        rsi=rsi_series(closes,14)

        def bars_since_cross(a,b,up=True,lookback=10):
            start=max(1,len(a)-lookback-1)
            hits=[]
            for i in range(start,len(a)):
                if a[i-1] is None or a[i] is None or b[i-1] is None or b[i] is None: continue
                ok=(a[i-1]<=b[i-1] and a[i]>b[i]) if up else (a[i-1]>=b[i-1] and a[i]<b[i])
                if ok: hits.append(len(a)-1-i)
            return min(hits) if hits else None

        rsi50_up=None; rsi50_down=None; rsi30_reclaim=None
        for i in range(max(15,len(rsi)-11),len(rsi)):
            if rsi[i-1] is None or rsi[i] is None: continue
            ago=len(rsi)-1-i
            if rsi[i-1]<=50<rsi[i]: rsi50_up=ago if rsi50_up is None else min(rsi50_up,ago)
            if rsi[i-1]>=50>rsi[i]: rsi50_down=ago if rsi50_down is None else min(rsi50_down,ago)
            if rsi[i-1]<=30<rsi[i]: rsi30_reclaim=ago if rsi30_reclaim is None else min(rsi30_reclaim,ago)

        golden=bars_since_cross(macd,signal,True,10)
        dead=bars_since_cross(macd,signal,False,10)
        zero_up=None
        for i in range(max(1,len(macd)-11),len(macd)):
            if macd[i-1]<=0<macd[i]:
                ago=len(macd)-1-i; zero_up=ago if zero_up is None else min(zero_up,ago)

        rr=float(rsi[-1] or 0); mm=float(macd[-1]); ss=float(signal[-1]); hh=float(hist[-1])
        hist_rising3=len(hist)>=4 and hist[-1]>hist[-2]>hist[-3]
        hist_falling3=len(hist)>=4 and hist[-1]<hist[-2]<hist[-3]
        rsi_rising3=all(v is not None for v in rsi[-3:]) and rsi[-1]>rsi[-2]>rsi[-3]

        score=20; pos=[]; neg=[]
        def add(pt,reason):
            nonlocal score; score+=pt; pos.append({'points':pt,'reason':reason})
        def sub(pt,reason):
            nonlocal score; score-=pt; neg.append({'points':-pt,'reason':reason})

        if 50<=rr<70: add(15,'RSI_HEALTHY_50_70')
        elif 40<=rr<50: add(6,'RSI_RECOVERY_ZONE')
        if rsi50_up is not None and rsi50_up<=3: add(15,'RSI_50_CROSS_UP_RECENT')
        if rsi30_reclaim is not None and rsi30_reclaim<=5: add(8,'RSI_30_RECLAIM')
        if rsi_rising3: add(7,'RSI_RISING_3D')
        if mm>ss: add(12,'MACD_ABOVE_SIGNAL')
        if golden is not None and golden<=5: add(18,'MACD_GOLDEN_RECENT')
        if mm>0: add(10,'MACD_ABOVE_ZERO')
        if zero_up is not None and zero_up<=5: add(8,'MACD_ZERO_CROSS_RECENT')
        if hist_rising3: add(10,'HISTOGRAM_RISING_3D')
        if rr>=78: sub(10,'RSI_OVERHEATED')
        if rsi50_down is not None and rsi50_down<=2: sub(20,'RSI_50_BREAKDOWN')
        if dead is not None and dead<=3: sub(25,'MACD_DEAD_CROSS_RECENT')
        if hist_falling3: sub(10,'HISTOGRAM_FALLING_3D')
        score=max(0,min(100,int(round(score))))

        if score>=80: state='STRONG_ENTRY'
        elif score>=65: state='ENTRY_READY'
        elif score>=50: state='PREPARE'
        else: state='WATCH'
        exit_signal=bool((rsi50_down is not None and rsi50_down<=2) or (dead is not None and dead<=3))
        if exit_signal: state='EXIT_REVIEW'

        out={
            'ok':True,'version':'FUJIMOTO_SWING_DAILY_V1','symbol':code,'timeframe':'1d',
            'holding_horizon':'2-10 trading days','score':score,'state':state,'exit_review':exit_signal,
            'price':closes[-1],'rsi':round(rr,2),'macd':round(mm,6),'macd_signal':round(ss,6),'macd_hist':round(hh,6),
            'signals':{
                'rsi_50_cross_up_bars_ago':rsi50_up,'rsi_50_cross_down_bars_ago':rsi50_down,'rsi_30_reclaim_bars_ago':rsi30_reclaim,
                'rsi_rising_3d':rsi_rising3,'macd_golden_cross_bars_ago':golden,'macd_dead_cross_bars_ago':dead,
                'macd_zero_cross_up_bars_ago':zero_up,'macd_above_signal':mm>ss,'macd_above_zero':mm>0,
                'histogram_rising_3d':hist_rising3,'histogram_falling_3d':hist_falling3,
            },
            'positive_reasons':pos,'penalties':neg,'daily_rows':len(seq),'latest_bar_date':seq[-1][0],
            'note':'Daily RSI(14)+MACD(12,26,9) swing model. Intended for roughly 2-10 trading-day holds, not intraday entry timing.',
            '_cached_at':now,
        }
        self._fujimoto_swing_daily_cache[code]=out
        return dict(out)
'''

API_PATCH=r'''

@app.get('/api/v5/fujimoto-swing/KOREA/{symbol}')
async def v5_fujimoto_swing_korea(symbol:str):
    return await asyncio.to_thread(korea.fujimoto_swing_daily_v1,symbol)
'''

def main():
    s=KOREA.read_text()
    if 'def fujimoto_swing_daily_v1' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,KOREA_PATCH+'\n'+anchor,1)
        KOREA.write_text(s)
    a=API.read_text()
    if '/api/v5/fujimoto-swing/KOREA/{symbol}' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1)
        API.write_text(a)
    print('FUJIMOTO_SWING_DAILY_V1_PATCH_OK')

if __name__=='__main__': main()
