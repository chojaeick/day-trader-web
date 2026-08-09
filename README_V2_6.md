# DAY TRADER WEB V2.6 — KOREA PREOPEN

Verified official Kiwoom source:
- ka10029 예상체결등락률상위
- endpoint: /api/dostk/rkinfo
- request fields:
  mrkt_tp, sort_tp, trde_qty_cnd, stk_cnd, crd_cnd, pric_cnd, stex_tp
- response table: exp_cntr_flu_rt_upper
- key fields: exp_cntr_pric, base_pric, flu_rt, exp_cntr_qty,
  sel_req, sel_bid, buy_bid, buy_req

V2.6 workflow:
- Weekdays 08:30 KST
- refresh KOSPI/KOSDAQ GAMMA Universe
- query ka10029 expected gainers/losers
- combine GAMMA + expected ranking + expected change
- apply PREOPEN chase-risk penalty
- create KOREA PREOPEN TOP10
- save immutable PREOPEN_30 archive through PreOpenReportStore
- if expected-execution data is unavailable, still save GAMMA_FALLBACK

Endpoints:
- POST /api/korea/preopen/generate
- GET /api/korea/preopen/latest
- GET /api/korea/preopen/history
- GET /api/korea/expected

NO AUTO ORDER.
