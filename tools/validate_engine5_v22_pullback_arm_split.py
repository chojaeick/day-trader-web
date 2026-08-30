from __future__ import annotations

"""Split validation for V22 UPTREND_PULLBACK_REENTRY by arm origin.

Compare pullback re-entry candidates armed by:
  1) VETO15 rejected primary signals only
  2) realized LOSING_EXIT only
  3) both combined

Reuses the exact causal candidate generator and score thresholds from
validate_engine5_v22_uptrend_pullback_reentry.py. Diagnostic only.
"""

from dataclasses import replace
from pathlib import Path
import pandas as pd

import tools.validate_engine5_v22_uptrend_pullback_reentry as pb
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_pullback_arm_split')
GROUPS = {
    'VETO15_ONLY': ['VETO15'],
    'LOSING_EXIT_ONLY': ['LOSING_EXIT'],
    'BOTH': ['VETO15', 'LOSING_EXIT'],
}


def make_extra_tags(q: pd.DataFrame):
    tags = []
    for r in q.itertuples(index=False):
        ev = pb.event_from_candidate(r)
        if ev is None:
            continue
        tags.append(dict(
            source='UPTREND_PULLBACK_REENTRY',
            symbol=pb.n(r.symbol),
            time=pd.Timestamp(r.candidate_time),
            event=ev,
            meta={
                'arm_reason': str(r.arm_reason),
                'arm_time': pd.Timestamp(r.arm_time),
                'primary_time': pd.Timestamp(r.primary_time),
            },
        ))
    return tags


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 PULLBACK ARM SPLIT VALIDATION ===', flush=True)
    print('Groups = VETO15_ONLY / LOSING_EXIT_ONLY / BOTH', flush=True)
    print('Thresholds =', pb.SCORE_THRESHOLDS, flush=True)

    raw = {pb.n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed, states, tagged, baseline = pb.baseline_objects(raw, cfg)

    bstat = pb.summary('A_BASELINE', baseline)
    print('\nBASELINE', bstat)

    arms_all = pb.build_arms(raw, cfg, tagged, baseline)
    print('ALL ARMS', len(arms_all), arms_all.arm_reason.value_counts().to_dict() if len(arms_all) else {})

    summaries = [bstat]
    candidate_dump = []
    trade_dump = [baseline.assign(case='A_BASELINE')]

    for gname, reasons in GROUPS.items():
        arms = arms_all[arms_all.arm_reason.isin(reasons)].copy().reset_index(drop=True)
        probes = pb.find_pullback_candidates(raw, cfg, arms)
        mandatory = probes[probes.mandatory_pass].copy().sort_values(['symbol','candidate_time']) if len(probes) else pd.DataFrame()
        print(f'\n=== {gname} ===')
        print('arms=', len(arms), 'mandatory_first_attempts=', len(mandatory))

        if len(probes):
            p = probes.copy()
            p['group'] = gname
            candidate_dump.append(p)

        for th in pb.SCORE_THRESHOLDS:
            if mandatory.empty:
                q = mandatory.copy()
            else:
                q = mandatory[mandatory.pullback_score >= th].copy()
                q = q.sort_values(['candidate_time','pullback_score'], ascending=[True,False]).drop_duplicates(['symbol','candidate_time'])

            extra_tags = make_extra_tags(q)
            combo_tags = list(tagged) + extra_tags
            tr = pb.integ.simulate(packed, states, combo_tags)
            label = f'{gname}_PB{int(th)}'
            st = pb.summary(label, tr)
            st['selected_candidates'] = len(q)
            st['arms'] = len(arms)
            st['mandatory_attempts'] = len(mandatory)
            summaries.append(st)
            trade_dump.append(tr.assign(case=label))
            print(label, st)

        # Show 466100 target rows separately for veto-only group.
        if gname == 'VETO15_ONLY' and len(probes):
            t = probes[(probes.symbol.astype(str).str.zfill(6) == '466100') &
                       (pd.to_datetime(probes.arm_time).dt.date == pd.Timestamp('2026-08-14').date())]
            if len(t):
                cols = ['arm_time','arm_reason','candidate_time','candidate_price','pullback_score','mandatory_pass',
                        'base_live_score','trend_up','mid_slope8','higher_low','pullback_low','pre_structural_low',
                        'macd_slope','rsi_slope','reaccel','close_above_mid','outer_expanding','volume_recovery']
                print('\nTARGET 466100 VETO15-ONLY')
                print(t[cols].to_string(index=False))

    sdf = pd.DataFrame(summaries)
    print('\n=== SUMMARY ===')
    print(sdf.to_string(index=False))
    sdf.to_csv(OUT / 'summary.csv', index=False)
    if candidate_dump:
        pd.concat(candidate_dump, ignore_index=True).to_csv(OUT / 'candidates.csv', index=False)
    if trade_dump:
        pd.concat(trade_dump, ignore_index=True).to_csv(OUT / 'trades.csv', index=False)
    arms_all.to_csv(OUT / 'arms.csv', index=False)
    print('\nWROTE', OUT)


if __name__ == '__main__':
    main()
