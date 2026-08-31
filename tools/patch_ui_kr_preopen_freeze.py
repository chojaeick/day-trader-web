from pathlib import Path

p=Path('/home/ubuntu/day-trader-api/ui_kr.py')
s=p.read_text()
old="""            with st.expander('상세 엔진 평가 보기', expanded=False):
                st.json({
                    'symbol': symbol,
                    'score': score,
                    'enter': d.get('enter'),
                    'reason': d.get('reason'),
                    'engine': d.get('engine'),
                    'bar_time': d.get('bar_time'),
                    'entry_ready': selected.get('entry_ready'),
                    'gate_open': gate.get('gate_open'),
                    'pulse_status': pulse,
                    'signal_only': signal_only,
                    'order_placement': order_on,
                })
"""
new="""            st.caption(
                f\"{symbol} · {d.get('engine') or 'ENGINE5_V22_KR_LIVE'} · \"
                f\"최종봉 {d.get('bar_time') or '-'} · \"
                f\"{'READY' if selected.get('entry_ready') else 'WATCH'} · \"
                f\"주문 {'ON' if order_on else 'OFF'}\"
            )
"""
if old in s:
    s=s.replace(old,new,1)
elif "st.json({" not in s:
    print('KR_PREOPEN_UI_ALREADY_CLEAN')
    raise SystemExit(0)
else:
    raise SystemExit('KR_ENGINE_DETAIL_BLOCK_NOT_FOUND')
p.write_text(s)
print('KR_PREOPEN_UI_FROZEN')
