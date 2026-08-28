from pathlib import Path

import tools.backtest_dbb_engine5_fast_tuner as tuner

# New checkpoint namespace: gate semantics changed again (persistence/context).
# Never mix prior gates-v2 results into this validation.
tuner.STAGE1_CKPT = Path('/home/ubuntu/day-trader-api/dbb_engine5_fast_gates_v3_stage1_checkpoint.csv')
tuner.STAGE2_CKPT = Path('/home/ubuntu/day-trader-api/dbb_engine5_fast_gates_v3_stage2_checkpoint.csv')

if __name__ == '__main__':
    tuner.main()
