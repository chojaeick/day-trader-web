# DAY TRADER WEB V2.5 — KOREA BASE

This is the first domestic-market layer.

Included:
- USA / KOREA market selector inside Trading.
- New `KoreaMarketAdapter`.
- `/api/korea/status`.
- `/api/korea/quote/{stk_cd}` using official Kiwoom domestic quote TR `ka10004`.
- Samsung Electronics (005930) connection test button.
- Domestic scanner architecture panel.

Planned V2.5.1 candidate sources:
- ka10032 거래대금상위
- ka10030 당일거래량상위
- ka10023 거래량급증
- ka10027 전일대비등락률상위
- ka10029 예상체결등락률상위 (pre-open)
Later score inputs:
- ka10046 체결강도추이시간별
- ka10054 VI 발동종목
- WebSocket `1h` VI발동/해제

Safety:
- V2.5 does not guess unverified ranking request bodies.
- USA CURRENT score and all existing briefing logic are unchanged.
- NO AUTO ORDER remains.
