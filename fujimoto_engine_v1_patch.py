from pathlib import Path

MOD=Path('live_server/fujimoto.py')
API=Path('live_server/api.py')

PATCH=r'''

# ===== FUJIMOTO ENTRY/EXIT ENGINE V1 =====
def evaluate_fujimoto_engine_v1(bars, previous_state='WATCH', position_open=False):
    score_data=evaluate_fujimoto_v1(bars)
    if not score_data.get('ok'):
        return {**score_data,'engine_version':'FUJIMOTO_ENGINE_V1','signal':'NONE','engine_state':'DATA_INVALID','reasons':['DATA_INVALID']}

    s=score_data.get('signals') or {}
    score=int(score_data.get('score') or 0)
    rsi=float(score_data.get('rsi') or 0)
    macd=float(score_data.get('macd') or 0)
    signal=float(score_data.get('macd_signal') or 0)
    hist=float(score_data.get('macd_hist') or 0)
    actionable=bool(score_data.get('actionable'))

    entry_reasons=[]; exit_reasons=[]
    if s.get('bullish_divergence'): entry_reasons.append('RSI_BULLISH_DIVERGENCE')
    if s.get('rsi_30_reclaim_bars_ago') is not None: entry_reasons.append('RSI_30_RECLAIM')
    if s.get('rsi_50_cross_up_bars_ago') is not None: entry_reasons.append('RSI_50_CROSS_UP')
    if s.get('rsi_rising_3'): entry_reasons.append('RSI_RISING')
    if s.get('macd_golden_cross_bars_ago') is not None: entry_reasons.append('MACD_GOLDEN_CROSS')
    if s.get('histogram_rising_3'): entry_reasons.append('MACD_HISTOGRAM_RISING')
    if s.get('macd_zero_cross_up_bars_ago') is not None: entry_reasons.append('MACD_ZERO_CROSS_UP')
    if s.get('macd_above_signal') and hist>0: entry_reasons.append('MACD_CONFIRMATION')

    if s.get('bearish_divergence'): exit_reasons.append('RSI_BEARISH_DIVERGENCE')
    if s.get('rsi_70_cross_down_bars_ago') is not None: exit_reasons.append('RSI_70_EXIT_DOWN')
    if s.get('rsi_50_cross_down_bars_ago') is not None: exit_reasons.append('RSI_50_CROSS_DOWN')
    if s.get('macd_dead_cross_bars_ago') is not None: exit_reasons.append('MACD_DEAD_CROSS')
    if s.get('histogram_falling_3'): exit_reasons.append('MACD_HISTOGRAM_FALLING')

    hard_exit=bool(('MACD_DEAD_CROSS' in exit_reasons and 'RSI_50_CROSS_DOWN' in exit_reasons) or ('RSI_BEARISH_DIVERGENCE' in exit_reasons and macd<signal))
    partial_exit=bool(position_open and ('RSI_70_EXIT_DOWN' in exit_reasons or 'MACD_HISTOGRAM_FALLING' in exit_reasons) and not hard_exit)

    if position_open:
        if hard_exit:
            state='EXIT'; sig='EXIT'
        elif partial_exit:
            state='PARTIAL_EXIT'; sig='PARTIAL_EXIT'
        else:
            state='HOLD'; sig='HOLD'
    else:
        if score>=80 and len(entry_reasons)>=3:
            state='ENTRY'; sig='ENTRY' if actionable else 'ENTRY_CANDIDATE'
        elif score>=60 and len(entry_reasons)>=2:
            state='ENTRY_READY'; sig='READY'
        elif score>=40:
            state='PREPARE'; sig='WATCH'
        else:
            state='WATCH'; sig='WATCH'

    return {
        **score_data,
        'engine_version':'FUJIMOTO_ENGINE_V1',
        'previous_state':previous_state,
        'position_open':bool(position_open),
        'engine_state':state,
        'signal':sig,
        'entry_reasons':entry_reasons,
        'exit_reasons':exit_reasons,
        'hard_exit':hard_exit,
        'partial_exit':partial_exit,
        'transition':f'{previous_state}->{state}',
        'engine_note':'Signal-only engine. No order placement in v1.'
    }
'''

API_PATCH=r'''

from .fujimoto import evaluate_fujimoto_engine_v1

@app.get('/api/v5/fujimoto-engine/KOREA/{symbol}')
async def v5_fujimoto_engine_korea(symbol:str,max_pages:int=2,previous_state:str='WATCH',position_open:bool=False):
    def _run():
        d=korea.canonical_minute_bars(symbol,max_pages=max(1,min(int(max_pages),3)))
        out=evaluate_fujimoto_engine_v1(d.get('bars') or [],previous_state=previous_state,position_open=position_open)
        out['symbol']=symbol
        out['source']='KIWOOM_KA10080_CANONICAL_1M'
        return out
    return await asyncio.to_thread(_run)
'''

def main():
    s=MOD.read_text()
    if 'def evaluate_fujimoto_engine_v1' not in s:
        s += PATCH
        MOD.write_text(s)
    a=API.read_text()
    if '/api/v5/fujimoto-engine/KOREA/{symbol}' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1)
        API.write_text(a)
    print('FUJIMOTO_ENGINE_V1_PATCH_OK')

if __name__=='__main__': main()
