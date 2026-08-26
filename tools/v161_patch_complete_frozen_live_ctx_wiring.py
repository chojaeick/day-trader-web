#!/usr/bin/env python3
"""V161 complete frozen USA live context wiring.

Runtime patch only. Strategy constants unchanged. USA paper-only path remains isolated.
Repairs missing live context assignment and supplies both entry and exit args using
causal 1-minute bars already present inside _usa_row().
"""
from pathlib import Path
import shutil,re,py_compile

P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
B=P.with_suffix('.py.bak_v161')
S=P.read_text()
if not B.exists(): shutil.copy2(P,B)

required=['def _usa_row(','def _paper_williams_step(','def _v140_usa_frozen_williams_eval']
missing=[x for x in required if x not in S]
if missing:
    print('MISSING_PREREQ=',missing); raise SystemExit(2)

METHOD=r'''
    def _v161_wire_usa_frozen_ctx(self, row, b1):
        """Build replay-equivalent frozen USA entry+exit context from live 1m bars."""
        try:
            if str((row or {}).get('market','')).upper()!='USA' or b1 is None or len(b1)<25:
                return None
            need=('time','open','high','low','close','volume')
            if any(c not in b1.columns for c in need):
                return None
            x=b1.copy().reset_index(drop=True)
            # Parse timestamps. Aware timestamps are converted to ET; naive timestamps are
            # treated as already ET-local, matching the frozen replay wall-clock semantics.
            ts=pd.to_datetime(x['time'],errors='coerce')
            if ts.isna().all(): return None
            try:
                if getattr(ts.dt,'tz',None) is not None:
                    et=ts.dt.tz_convert('America/New_York')
                else:
                    et=ts.dt.tz_localize('America/New_York',ambiguous='NaT',nonexistent='shift_forward')
            except Exception:
                et=ts
            mins=et.dt.hour*60+et.dt.minute
            dates=et.dt.strftime('%Y-%m-%d')
            reg=(mins>=570)&(mins<960)
            if not bool(reg.any()): return None
            current_date=str(dates.iloc[-1])
            cur_idx=x.index[reg & (dates==current_date)].tolist()
            if not cur_idx:
                # Premarket: current regular-session open does not yet exist. No fake context.
                return None
            prior_dates=sorted(set(str(d) for d in dates[reg & (dates<current_date)].dropna().tolist()))
            if not prior_dates:return None
            prev_date=prior_dates[-1]
            prev_idx=x.index[reg & (dates==prev_date)].tolist()
            if not prev_idx:return None
            day_open=_f(x.loc[cur_idx[0],'open'])
            prev_high=max(_f(x.loc[i,'high']) for i in prev_idx)
            prev_low=min(_f(x.loc[i,'low']) for i in prev_idx)
            if not (day_open and prev_high and prev_low):return None

            closes=[_f(v) for v in x['close'].tolist()]
            highs=[_f(v) for v in x['high'].tolist()]
            lows=[_f(v) for v in x['low'].tolist()]
            vols=[_f(v) for v in x['volume'].tolist()]
            if len(closes)<21:return None
            rsi2=_williams_rsi2(closes[-60:])
            tp=[(h+l+c)/3.0 for h,l,c in zip(highs,lows,closes)]
            def _cci_at(end):
                if end<19:return None
                w=tp[end-19:end+1]; m=sum(w)/20.0; md=sum(abs(v-m) for v in w)/20.0
                return 0.0 if md==0 else (tp[end]-m)/(0.015*md)
            cci20=_cci_at(len(tp)-1); prev_cci20=_cci_at(len(tp)-2)
            if rsi2 is None or cci20 is None or prev_cci20 is None:return None
            def _ema(vals,span):
                if not vals:return []
                a=2.0/(span+1.0); out=[float(vals[0])]
                for v in vals[1:]:out.append(a*float(v)+(1-a)*out[-1])
                return out
            e12=_ema(closes,12); e26=_ema(closes,26); mac=[a-b for a,b in zip(e12,e26)]; sig=_ema(mac,9)
            hist=[a-b for a,b in zip(mac,sig)]
            if len(hist)<2:return None
            prior=vols[-11:-1]; va=(sum(prior)/len(prior)) if prior else 0.0
            trigger=day_open+0.5*(prev_high-prev_low)
            prv=closes[-2]; cur=closes[-1]; cross_now=bool(prv<=trigger<cur)
            sym=str((row or {}).get('symbol') or '').upper()
            day=current_date.replace('-','')
            cross_key=('WUF_CROSS',sym,day)
            cross_state=self._last.get(cross_key,{}) if sym else {}
            prev_crossed=bool((cross_state or {}).get('seen'))
            entry_args={
                'ts':x.iloc[-1]['time'],'prev_crossed':prev_crossed,'cross_now':cross_now,
                'rsi2':rsi2,'day_open':day_open,'prev_high':prev_high,'prev_low':prev_low,
                'volume':vols[-1],'prior10_volume_avg':va,'cci20':cci20,
                'macd_hist':hist[-1],'prev_macd_hist':hist[-2],
            }
            # Mark first cross after capturing prev_crossed=False for this exact evaluation.
            if sym and cross_now:self._last[cross_key]={'seen':True}
            out={'entry_args':entry_args,'feature_snapshot':{
                'day_open':day_open,'prev_high':prev_high,'prev_low':prev_low,
                'rsi2':rsi2,'cci20':cci20,'prev_cci20':prev_cci20,
                'macd':mac[-1],'signal':sig[-1],'macd_hist':hist[-1],
                'prev_macd_hist':hist[-2],'volume_ratio':(vols[-1]/va if va else 0.0),
                'trigger':trigger,'cross_now':cross_now,'prev_crossed':prev_crossed,
                'trade_date':current_date}}
            try:
                ppos=self.paper.position('USA',sym) if sym and hasattr(self.paper,'position') else None
            except Exception:
                ppos=None
            if not ppos:
                if sym:self._last[('WUF_WEAK',sym)]={'run':0}
                out['exit_args']=None
                return out
            ep=_f((ppos or {}).get('avg_entry') or (ppos or {}).get('entry_price') or (ppos or {}).get('price'))
            weak_state=self._last.get(('WUF_WEAK',sym),{}) if sym else {}
            if ep:
                out['exit_args']={
                    'entry_price':ep,'price':_f((row or {}).get('price') or closes[-1]),
                    'macd':mac[-1],'signal':sig[-1],'cci20':cci20,'prev_cci20':prev_cci20,
                    'prev_macd':mac[-2] if len(mac)>=2 else None,
                    'prev_signal':sig[-2] if len(sig)>=2 else None,
                    'weak_run':int((weak_state or {}).get('run') or 0),
                }
            else:
                out['exit_args']=None
            return out
        except Exception as e:
            return {'error':str(e)}
'''

if 'def _v161_wire_usa_frozen_ctx' not in S:
    anchor='    def _v140_usa_frozen_williams_eval(self, row):\n'
    if anchor not in S: raise SystemExit('V140_EVAL_ANCHOR_NOT_FOUND')
    S=S.replace(anchor,METHOD+'\n'+anchor,1)

# Insert exact ctx assignment before the final return row inside _usa_row.
if 'V161_FROZEN_CTX_WIRING' not in S:
    start=S.find('    def _usa_row(')
    if start<0: raise SystemExit('USA_ROW_NOT_FOUND')
    end=S.find('\n    def ',start+10)
    if end<0: raise SystemExit('USA_ROW_END_NOT_FOUND')
    block=S[start:end]
    returns=list(re.finditer(r'^        return row\s*$',block,re.M))
    if not returns: raise SystemExit('USA_ROW_RETURN_ROW_NOT_FOUND')
    m=returns[-1]
    insert="""        # V161_FROZEN_CTX_WIRING: replay-equivalent USA paper context only.\n        row['williams_frozen_ctx']=self._v161_wire_usa_frozen_ctx(row,b1)\n"""
    block=block[:m.start()]+insert+block[m.start():]
    S=S[:start]+block+S[end:]

# Persist two-bar weak-run state from the frozen evaluator; no change to exit rule.
if 'V161_FROZEN_WEAK_RUN_STATE' not in S:
    old="""            sym=str((row or {}).get('symbol') or '').upper()\n            price=_f((row or {}).get('price'))\n"""
    new="""            sym=str((row or {}).get('symbol') or '').upper()\n            price=_f((row or {}).get('price'))\n            # V161_FROZEN_WEAK_RUN_STATE: carry causal 2-bar combo state across refreshes.\n            if sym and isinstance(ev,dict) and isinstance(ev.get('exit_eval'),dict):\n                self._last[('WUF_WEAK',sym)]={'run':int(ev['exit_eval'].get('weak_run') or 0)}\n"""
    if old not in S: raise SystemExit('PAPER_SYM_PRICE_ANCHOR_NOT_FOUND')
    S=S.replace(old,new,1)

P.write_text(S)
try:
    py_compile.compile(str(P),doraise=True); comp='PASS'
except Exception as e:
    comp='FAIL:'+str(e)

print('=== V161 COMPLETE FROZEN USA LIVE CTX WIRING ===')
print('PATCHED',P)
print('BACKUP',B)
print('CTX_ASSIGNMENT=',"row['williams_frozen_ctx']=self._v161_wire_usa_frozen_ctx(row,b1)" in S)
print('ENTRY_CTX=YES')
print('EXIT_CTX=YES')
print('FIRST_CROSS_STATE=YES')
print('TWO_BAR_WEAK_RUN_STATE=YES')
print('REGULAR_SESSION_PREV_DAY_OHLC_CAUSAL=YES')
print('PREMARKET_FAKE_DAY_OPEN=NO')
print('STRATEGY_CONSTANTS_CHANGED=NO')
print('ORDER_MODE=USA_PAPER_ONLY')
print('PY_COMPILE=',comp)
print('NEXT=RESTART_AND_VERIFY_CTX_REASON_NOT_NO_CTX_DURING_REGULAR_SESSION')
