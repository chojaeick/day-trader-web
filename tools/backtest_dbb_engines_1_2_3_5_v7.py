"""Integrated DBB validation: fixed Engines 1/2/3 + current Engine 5 V7.

Engines 1/2/3 reuse the existing persistent diagnostics cache through the
integrated runner. Engine 5 is explicitly replaced with the V7 implementation.
"""

from __future__ import annotations

import tools.backtest_dbb_engines_1_2_3_5 as integrated
from tools.backtest_dbb_engine5_fast_tuner_v7 import main as run_engine5_v7


def main():
    print('[INTEGRATED 1/2/3/5] Engine 5 implementation = V7', flush=True)
    integrated.run_engine5 = run_engine5_v7
    integrated.main()


if __name__ == '__main__':
    main()
