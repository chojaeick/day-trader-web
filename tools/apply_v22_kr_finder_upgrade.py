from __future__ import annotations

"""Upgrade KR Finder for V22 mock trading.

Goals
- Finder is candidate discovery, not order authority.
- Prefer positive/accelerating long candidates: good discovery signal, movers,
  high turnover/volume participation, event-quality names.
- Reject negative/down/short candidates from KR long Finder.
- Expand Finder to 20 names and Heavy Tracker capacity to 15 so V22 gets a
  wider opportunity set without lowering the V22 entry threshold.
- Preserve full V22 BUY/SELL broker authority and capital allocator patches.
"""

from pathlib import Path
import os, py_compile, subprocess, tempfile, time, urllib.request

RUNTIME=Path('/home/ubuntu/day-trader-api')
TARGET=RUNTIME/'live_server/v4_engine.py'
SERVICE='day-trader-api'

OLD_FUNC='''    def build_korea_finder(self,discovery,limit=5):\n        rows=[]\n        for r in discovery.get('rows') or []:\n            q=r.get('quality_grade'); risk=str(r.get('chase_risk') or 'NORMAL').upper()\n            if q not in ('A','B_EVENT') or risk=='EXTREME':continue\n            rows.append({'market':'KOREA','symbol':str(r.get('symbol') or ''),'name':r.get('name') or r.get('symbol'),'quality':q,'finder_score':round(_f(r.get('score')),1),'direction':str(r.get('bias') or 'NEUTRAL').upper(),'price':_f(r.get('price')),'change_pct':_f(r.get('change_pct')),'dollar_volume':_f(r.get('trading_value')),'rvol':None,'atr_pct':None,'risk':risk})\n        rows.sort(key=lambda x:x['finder_score'],reverse=True); rows=rows[:limit]\n        for i,r in enumerate(rows,1):r['rank']=i\n        self._update_finder('KOREA',rows); return self.finder['KOREA']\n'''

NEW_FUNC='''    def build_korea_finder(self,discovery,limit=20):\n        # V22_KR_FINDER_UPGRADE: long-only candidate discovery.\n        # Finder finds opportunity; Engine5 V22 remains the sole BUY/SELL authority.\n        rows=[]\n        for r in discovery.get('rows') or []:\n            q=str(r.get('quality_grade') or '').upper()\n            risk=str(r.get('chase_risk') or 'NORMAL').upper()\n            if q not in ('A','B_EVENT') or risk=='EXTREME':\n                continue\n\n            sym=str(r.get('symbol') or '').replace('A','').zfill(6)\n            if not sym:\n                continue\n            bias=str(r.get('bias') or r.get('direction') or 'NEUTRAL').upper()\n            chg=_f(r.get('change_pct'))\n            base=_clip(_f(r.get('score')),0,100)\n            value=max(0.0,_f(r.get('trading_value') or r.get('dollar_volume')))\n            live=_clip(_f(r.get('live_score'),base),0,100)\n            strength=_f(r.get('strength_composite'))\n            rvol=max(0.0,_f(r.get('rvol') or r.get('volume_ratio') or r.get('vol_ratio')))\n\n            # KR strategy is long-only. Strong negative/down names must not consume\n            # Finder slots merely because absolute Power/movement is large.\n            if bias in ('DOWN','SHORT','BEAR') or chg < -0.20:\n                continue\n\n            # 1) quality/signal component: discovery + live score.\n            signal_component=.32*base + .18*live\n            if strength:\n                signal_component += _clip((strength-80.0)/60.0*10.0,0,10)\n\n            # 2) positive mover component. Reward fresh strength, but cap chase.\n            if chg>=12: mover=18.0\n            elif chg>=7: mover=16.0\n            elif chg>=4: mover=13.0\n            elif chg>=2: mover=10.0\n            elif chg>=0.7: mover=7.0\n            elif chg>=0.15: mover=3.0\n            else: mover=0.0\n\n            # 3) turnover/liquidity component. Korean trading_value is KRW; use\n            # broad logarithmic-like thresholds without assuming one exact universe.\n            if value>=100_000_000_000: flow=22.0\n            elif value>=50_000_000_000: flow=19.0\n            elif value>=20_000_000_000: flow=16.0\n            elif value>=10_000_000_000: flow=13.0\n            elif value>=5_000_000_000: flow=10.0\n            elif value>=1_000_000_000: flow=6.0\n            elif value>=300_000_000: flow=3.0\n            else: flow=0.0\n\n            # Relative-volume bonus when the upstream discovery source supplies it.\n            vol_bonus=(10.0 if rvol>=3 else 8.0 if rvol>=2 else 5.0 if rvol>=1.5 else 2.0 if rvol>=1.15 else 0.0)\n            event_bonus=7.0 if q=='B_EVENT' else 4.0\n\n            # Avoid filling the list with stagnant high-liquidity names. A candidate\n            # must show at least one meaningful positive opportunity signal.\n            positive_signal=bool(chg>=0.15 or base>=55 or live>=55 or rvol>=1.5 or q=='B_EVENT')\n            if not positive_signal:\n                continue\n\n            # Chase is a penalty, not a blanket exclusion; 급등주는 후보로 보되\n            # 과열 후반부가 상단을 독점하지 않게 한다.\n            chase_penalty=0.0\n            if risk=='HIGH': chase_penalty+=5.0\n            elif risk=='CHASE': chase_penalty+=3.0\n            if chg>=20: chase_penalty+=12.0\n            elif chg>=15: chase_penalty+=7.0\n\n            score=_clip(signal_component+mover+flow+vol_bonus+event_bonus-chase_penalty,0,100)\n            if score<=0:\n                continue\n\n            reason=(f'signal {signal_component:.1f} + mover {mover:.1f} + flow {flow:.1f}'\n                    f' + volume {vol_bonus:.1f} + quality {event_bonus:.1f}'\n                    f' - chase {chase_penalty:.1f}')\n            rows.append({\n                'market':'KOREA','symbol':sym,'name':r.get('name') or sym,'quality':q,\n                'finder_score':round(score,1),'finder_raw_score':round(base,1),\n                'direction':'UP' if chg>0 or bias in ('UP','LONG','BULL') else 'NEUTRAL',\n                'price':_f(r.get('price')),'change_pct':chg,'dollar_volume':value,\n                'rvol':rvol or None,'atr_pct':_f(r.get('atr_pct')) or None,'risk':risk,\n                'finder_reason':reason,\n                'finder_components':{'signal':round(signal_component,1),'mover':mover,'flow':flow,\n                                     'volume':vol_bonus,'quality':event_bonus,'chase':-chase_penalty},\n            })\n\n        # Ranking: total opportunity score first, then positive move and turnover.\n        rows.sort(key=lambda x:(x['finder_score'],x['change_pct'],x['dollar_volume']),reverse=True)\n        rows=rows[:max(1,int(limit))]\n        for i,r in enumerate(rows,1):r['rank']=i\n        self._update_finder('KOREA',rows)\n        self.finder['KOREA']['finder_policy']='V22_KR_POSITIVE_MOMENTUM_VOLUME_EVENT'\n        self.finder['KOREA']['candidate_limit']=max(1,int(limit))\n        return self.finder['KOREA']\n'''


def run(*a):
    print('+',' '.join(map(str,a)),flush=True); subprocess.run(list(map(str,a)),check=True)


def install_text(text:str):
    fd,tmp=tempfile.mkstemp(prefix='v22_kr_finder_',suffix='.py'); os.close(fd); p=Path(tmp)
    try:
        p.write_text(text); py_compile.compile(str(p),doraise=True)
        run('sudo','install','-m','0644',p,TARGET)
    finally:
        try:p.unlink()
        except FileNotFoundError:pass


def main():
    text=TARGET.read_text()
    backup=TARGET.with_suffix('.py.pre_v22_kr_finder_upgrade')
    if not backup.exists():
        run('sudo','cp','-p',TARGET,backup); print('BACKUP',backup,flush=True)

    # Never overwrite/downgrade the already-deployed full V22 order lifecycle.\n    for marker in ('V22_KR_FULL_ORDER_AUTHORITY','V22_KR_CAPITAL_ALLOCATOR','_v22_kr_exit(row,st)'):
        if marker not in text: raise SystemExit('ABORT required runtime marker missing: '+marker)

    if 'V22_KR_FINDER_UPGRADE' not in text:
        if text.count(OLD_FUNC)!=1: raise SystemExit(f'ABORT KR finder anchor count={text.count(OLD_FUNC)}')
        text=text.replace(OLD_FUNC,NEW_FUNC,1)
        print('KR_FINDER_POLICY=PATCHED',flush=True)
    else:
        print('KR_FINDER_POLICY=ALREADY_PATCHED',flush=True)

    # Expand live opportunity set without lowering V22 entry threshold.
    if 'TRACK_LIMIT=5' in text:
        text=text.replace('TRACK_LIMIT=5','TRACK_LIMIT=15',1); print('TRACK_LIMIT=15',flush=True)
    elif 'TRACK_LIMIT=15' not in text:
        raise SystemExit('ABORT unexpected TRACK_LIMIT')

    if '            if len(syms)>=8:\n' in text:
        text=text.replace('            if len(syms)>=8:\n','            if len(syms)>=15:\n',1); print('KR_TRACKER_CANDIDATES=15',flush=True)
    elif '            if len(syms)>=15:\n' not in text:
        raise SystemExit('ABORT KR tracker candidate cap anchor missing')

    install_text(text)
    run(RUNTIME/'venv/bin/python','-m','py_compile',TARGET)
    verify=TARGET.read_text()
    required=('V22_KR_FINDER_UPGRADE','def build_korea_finder(self,discovery,limit=20):','TRACK_LIMIT=15','if len(syms)>=15:',\
              'V22_KR_FULL_ORDER_AUTHORITY','V22_KR_CAPITAL_ALLOCATOR')
    for x in required:
        if x not in verify: raise SystemExit('ABORT missing '+x)

    run('sudo','systemctl','restart',SERVICE)
    deadline=time.time()+60; last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2) as r:
                last=r.read().decode('utf-8','replace')
                if r.status==200: print('HEALTH=PASS',flush=True); break
        except Exception as e:last=repr(e)
        time.sleep(2)
    else: raise SystemExit('ABORT health failed: '+str(last))

    print('KR_FINDER=POSITIVE_SIGNAL_MOVER_VOLUME_EVENT',flush=True)
    print('KR_NEGATIVE_DIRECTION_CANDIDATES=EXCLUDED',flush=True)
    print('KR_FINDER_LIMIT=20',flush=True)
    print('KR_TRACKER_LIMIT=15',flush=True)
    print('V22_ENTRY_THRESHOLD=UNCHANGED',flush=True)
    print('KR_BUY_AUTHORITY=ENGINE5_V22_KR_LIVE',flush=True)
    print('KR_SELL_AUTHORITY=ENGINE5_V22_KR_LIVE',flush=True)
    print('BROKER=KIWOOM_MOCK_ONLY',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__': main()
