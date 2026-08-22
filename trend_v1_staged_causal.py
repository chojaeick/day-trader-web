#!/usr/bin/env python3
import glob, os
import pandas as pd

CACHE_GLOB = "/tmp/fast_replay_cache/*.csv"
COST_RT = 0.20
MAX_HOLD = 45
PROBE_W = 0.10
CONFIRM_W = 0.20
FULL_W = 0.70

def score_row(x, i):
    if i < 6:
        return 0, {}
    r = x.loc[i]
    p3 = (float(r.signal_price) / float(x.loc[i-2, 'signal_price']) - 1) * 100
    p5 = (float(r.signal_price) / float(x.loc[i-4, 'signal_price']) - 1) * 100
    mfi3 = float(r.mfi14) - float(x.loc[i-2, 'mfi14'])

    o = float(r.signal_open)
    h = float(r.signal_high)
    l = float(r.signal_low)
    c = float(r.signal_price)
    rg = max(h-l, 1e-9)
    close_pos = (c-l)/rg

    ema9_gap = (c/float(r.ema9)-1)*100 if float(r.ema9) else 0
    rp = float(r.rp)
    part = float(r.participation)

    score = (
        int(p3 >= .50) +
        int(p5 >= .60) +
        int(mfi3 >= 8) +
        int(close_pos >= .70) +
        int(ema9_gap >= .45) +
        int((rp >= .35) or (part >= 3))
    )
    return score, dict(
        p3=p3, p5=p5, mfi3=mfi3, close_pos=close_pos,
        ema9_gap=ema9_gap, rp=rp, part=part
    )

def trade_one(x, i0):
    r0 = x.loc[i0]
    entry0 = float(x.loc[i0, 'open'])
    entry_mfi = float(r0.mfi14)

    legs = [(entry0, PROBE_W, 'PROBE')]
    deployed = PROBE_W
    confirm_done = False
    full_done = False
    exit_reason = 'TIME'
    exit_px = None
    exit_i = min(i0 + MAX_HOLD, len(x)-1)

    for i in range(i0+1, min(i0+MAX_HOLD, len(x)-1)+1):
        r = x.loc[i]
        sig = float(r.signal_price)
        ema9 = float(r.ema9)
        ema20 = float(r.ema20)
        vwap = float(r.vwap)
        mfi = float(r.mfi14)
        vo = float(r.vo_raw)
        px = float(x.loc[i, 'open'])

        break_refs = (sig < ema20 and sig < vwap)
        hard_break = bool(int(r.one_break)) and sig < ema9
        if break_refs or hard_break:
            exit_px = px
            exit_i = i
            exit_reason = 'STRUCT_EXIT'
            break

        if not confirm_done:
            higher_low = True
            if i >= 3:
                prev = x.loc[i-3:i-1, 'signal_low']
                higher_low = float(r.signal_low) >= float(prev.min())

            confirm_ok = (
                sig > ema9 > ema20 and
                sig >= entry0 * 0.997 and
                mfi >= entry_mfi - 8 and
                higher_low
            )
            if confirm_ok:
                legs.append((px, CONFIRM_W, 'CONFIRM'))
                deployed += CONFIRM_W
                confirm_done = True

        if confirm_done and not full_done and i >= i0+2:
            prior_high = float(x.loc[i0:i-1, 'signal_high'].max())
            breakout = sig > prior_high
            flow_ok = (mfi >= entry_mfi - 5) and (vo > -1.0)
            trend_ok = sig > ema9 > ema20 and sig > vwap
            if breakout and flow_ok and trend_ok:
                legs.append((px, FULL_W, 'FULL'))
                deployed += FULL_W
                full_done = True

        if full_done:
            failed = (sig < ema9 and (sig < vwap or sig < ema20))
            if failed:
                exit_px = px
                exit_i = i
                exit_reason = 'FAIL_EXIT'
                break

    if exit_px is None:
        exit_px = float(x.loc[exit_i, 'close'])

    gross = 0.0
    for ep, w, _ in legs:
        gross += w * ((exit_px/ep)-1) * 100

    cost = COST_RT * deployed
    net = gross - cost

    w = x.loc[i0:min(i0+30, len(x)-1)]
    mfe30 = (float(w.high.max())/entry0 - 1)*100
    mae30 = (float(w.low.min())/entry0 - 1)*100

    return dict(
        ENTRY_TIME=str(r0.time),
        EXIT_TIME=str(x.loc[exit_i, 'time']),
        SCORE=int(score_row(x, i0)[0]),
        DEPLOYED=deployed,
        LEGS='+'.join(name for _,_,name in legs),
        EXIT_REASON=exit_reason,
        GROSS=gross,
        COST=cost,
        NET=net,
        MFE30=mfe30,
        MAE30=mae30,
    )

def main():
    files = sorted(glob.glob(CACHE_GLOB))
    rows = []
    sessions = 0

    for f in files:
        try:
            x = pd.read_csv(f)
        except Exception:
            continue
        needed = {'signal_open','signal_high','signal_low','signal_price','mfi14',
                  'vo_raw','ema9','ema20','vwap','participation','rp','one_break'}
        if not needed.issubset(set(x.columns)):
            continue

        sessions += 1
        case = os.path.basename(f).replace('.csv','')
        symbol, date = case.split('_', 1)

        next_i = 0
        for i in range(8, len(x)-46):
            if i < next_i:
                continue
            r = x.loc[i]
            if str(r.time) >= '11:00':
                break

            score, feat = score_row(x, i)
            if score < 5:
                continue

            t = trade_one(x, i)
            t.update(CASE=case, SYMBOL=symbol, DATE=date, **feat)
            rows.append(t)

            exit_idx = x.index[x.time.astype(str) == t['EXIT_TIME']]
            if len(exit_idx):
                next_i = int(exit_idx[0]) + 1
            else:
                next_i = i + MAX_HOLD + 1

    z = pd.DataFrame(rows)
    print("===== TREND V1 STAGED CAUSAL =====")
    print("SESSIONS", sessions)
    print("TRADES", len(z))
    if len(z) == 0:
        return

    print("FULL_DEPLOYED", int((z.DEPLOYED >= .999).sum()))
    print("CONFIRM_ONLY", int(((z.DEPLOYED > .10) & (z.DEPLOYED < .999)).sum()))
    print("PROBE_ONLY", int((z.DEPLOYED <= .10).sum()))
    print("GROSS", f"{z.GROSS.sum():+.3f}%")
    print("COST", f"{z.COST.sum():.3f}%")
    print("NET", f"{z.NET.sum():+.3f}%")
    print("AVG NET/TRD", f"{z.NET.mean():+.3f}%")
    print("WIN RATE", f"{(z.NET>0).mean()*100:.1f}%")
    print("AVG DEPLOYED", f"{z.DEPLOYED.mean()*100:.1f}%")
    print("MFE30>=2%", int((z.MFE30>=2).sum()))

    print("\n===== BY SYMBOL =====")
    print(z.groupby('SYMBOL').agg(
        N=('NET','size'),
        NET=('NET','sum'),
        AVG=('NET','mean'),
        FULL=('DEPLOYED',lambda s:int((s>=.999).sum()))
    ).round(3).sort_values('NET', ascending=False))

    print("\n===== BY DATE =====")
    print(z.groupby('DATE').agg(
        N=('NET','size'),
        NET=('NET','sum'),
        AVG=('NET','mean')
    ).round(3).sort_index())

    print("\n===== ALL TRADES =====")
    cols=['CASE','ENTRY_TIME','EXIT_TIME','SCORE','DEPLOYED','LEGS','EXIT_REASON',
          'GROSS','COST','NET','MFE30','MAE30']
    print(z[cols].round(3).to_string(index=False))

if __name__ == "__main__":
    main()
