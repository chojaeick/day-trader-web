from pathlib import Path
import re

APP=Path('/home/ubuntu/day-trader-api/app_v5.py')
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')

STATUS={
    'Core':'기준엔진',
    'MA20':'미검증',
    'Ethan':'검증중',
    'Jared3/4':'미검증',
    'Predator':'미검증',
}

API_BLOCK=r'''

# ===== DAYTRADE ENGINE VALIDATION REGISTRY V62 =====
@app.get('/api/v5/daytrade-engine-validation/KOREA')
async def v5_daytrade_engine_validation_korea():
    return {
        'ok': True,
        'version': 'DAYTRADE_ENGINE_VALIDATION_V62',
        'engines': [
            {'engine':'Core','role':'baseline','status':'BASELINE','validated':False,'note':'비교 기준선. 성과 검증은 별도 수행'},
            {'engine':'MA20','role':'intraday','status':'NOT_VALIDATED','validated':False,'note':'백테스트/실전 로그 검증 필요'},
            {'engine':'Ethan','role':'intraday','status':'VALIDATING','validated':False,'note':'현재 검증 단계'},
            {'engine':'Jared3/4','role':'intraday','status':'NOT_VALIDATED','validated':False,'note':'백테스트/실전 로그 검증 필요'},
            {'engine':'Predator','role':'intraday','status':'NOT_VALIDATED','validated':False,'note':'백테스트/실전 로그 검증 필요'},
        ],
        'criteria': {
            'minimum_trades': 100,
            'metrics': ['win_rate','avg_return','expectancy','profit_factor','max_drawdown','signal_count'],
            'requirements': ['causal_only','no_lookahead','fees_slippage_included','same_universe_comparison','out_of_sample_check'],
        },
        'note':'Fujimoto is excluded from intraday validation; it is a separate 2-10 trading-day swing engine.',
    }
'''


def add_validation_field(line,name,label):
    if "'검증':" in line:
        return line
    token=f"'엔진':'{name}',"
    if token in line:
        return line.replace(token, token+f"'검증':'{label}',",1)
    return line


def main():
    if not APP.exists():
        raise SystemExit('APP_NOT_FOUND')
    s=APP.read_text()

    # 1) Rename the intraday detail section clearly.
    s=s.replace('상세 엔진 평가','단타 엔진 비교 · 검증')
    s=s.replace('상세엔진평가','단타 엔진 비교 · 검증')

    # 2) Fujimoto is no longer an intraday engine. Remove only the single-line
    #    intraday matrix row; keep the dedicated Fujimoto Swing section below.
    lines=[]
    removed=0
    for line in s.splitlines(True):
        if "{'엔진':'Fujimoto'" in line and 'Fujimoto Swing' not in line:
            removed+=1
            continue
        for name,label in STATUS.items():
            line=add_validation_field(line,name,label)
        lines.append(line)
    s=''.join(lines)

    # 3) Make the swing section role explicit without changing its calculations.
    s=s.replace('Fujimoto Swing · 일봉 RSI(14)+MACD(12,26,9) · 목표 보유 2~10영업일',
                '중단기 Swing · Fujimoto · 일봉 RSI(14)+MACD(12,26,9) · 목표 보유 2~10영업일')

    marker='# ===== UI DAYTRADE ENGINE VALIDATION V62 =====\n'
    if marker not in s:
        # Safe top-level marker only; no indentation-sensitive insertion.
        s=marker+s

    APP.write_text(s)

    if API.exists():
        a=API.read_text()
        if 'DAYTRADE ENGINE VALIDATION REGISTRY V62' not in a:
            a += API_BLOCK
            API.write_text(a)

    print('UI_DAYTRADE_ENGINE_VALIDATION_V62_OK removed_fujimoto_rows=',removed)

if __name__=='__main__':
    main()
