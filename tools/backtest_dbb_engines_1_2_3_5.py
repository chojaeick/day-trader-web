"""Integrated validation runner for DBB engines 1, 2, 3 and 5.

Order is deliberate:
  1) Reproduce the fixed historical reference for Engine 1 (V2 BASE),
     Engine 2 (V2.1 STRUCTURE), and Engine 3 (V2.2 adaptive/structural exit).
  2) Run the current clarified Engine 5 tuner on the exact same KR historical
     source so every tuning pass is interpreted against the 1/2/3 references.

Engine 5's 80%+ win rate is a tuning objective only, never a filter.
"""

from tools.backtest_dbb_kr_v2_v21_v22 import main as run_engines_123
from tools.backtest_dbb_engine5_tuner import main as run_engine5


def main():
    print('\n' + '=' * 88)
    print('PHASE A — FIXED REFERENCE: ENGINES 1 / 2 / 3')
    print('=' * 88, flush=True)
    run_engines_123()

    print('\n' + '=' * 88)
    print('PHASE B — ENGINE 5 CLARIFIED LOGIC + TUNING')
    print('Compare every Engine 5 candidate against the Engine 1/2/3 reference above.')
    print('=' * 88, flush=True)
    run_engine5()

    print('\n' + '=' * 88)
    print('VALIDATION RULE')
    print('Do not choose Engine 5 by win rate alone: compare trades, win rate, avg_pct, gross_pct, PF, max loss and partial-profit behavior against Engines 1/2/3.')
    print('=' * 88)


if __name__ == '__main__':
    main()
