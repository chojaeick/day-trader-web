from pathlib import Path

p = Path('live_server/api.py')
s = p.read_text()

if 'async def v23_korea_mock_order_forever():' not in s:
    marker = 'async def korea_intraday_pulse_forever():'
    if marker not in s:
        raise SystemExit('ERROR: korea_intraday_pulse_forever marker not found; nothing changed')
    block = '''async def v23_korea_mock_order_forever():
    """DAYTRADE KR-only V23 mock-order authority.

    Runs the existing Korea tracker only during KRX regular session and only
    while the KOREA market auto switch is ON. The existing tracker owns
    _williams_mock_auto_step(), V23 entry/exit evaluation and Kiwoom MOCK
    buy_market/sell_market. USA work is intentionally not started here.
    """
    await asyncio.sleep(25)
    while True:
        try:
            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
            mins=kst.hour*60+kst.minute
            regular=bool(kst.weekday()<5 and 540<=mins<930)
            if regular and is_market_auto_enabled('KOREA'):
                await asyncio.to_thread(v4.refresh_korea_tracker,korea)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception('V23 KOREA mock order loop failed')
        await asyncio.sleep(30)

'''
    s = s.replace(marker, block + marker, 1)

if 'asyncio.create_task(v23_korea_mock_order_forever())' not in s:
    marker = '                asyncio.create_task(daytrade_entry_auto_forever()), # V232_KOREA_RESTORE\n'
    if marker not in s:
        raise SystemExit('ERROR: DAYTRADE task marker not found; nothing changed')
    s = s.replace(marker, marker + '                asyncio.create_task(v23_korea_mock_order_forever()), # V23_KOREA_MOCK_ORDER\n', 1)

p.write_text(s)
print('V23 KR MOCK ORDER LOOP CONNECTED')
