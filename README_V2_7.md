# DAY TRADER WEB V2.7 — KOREA INTRADAY PULSE

Official Kiwoom integrations:
- ka10046 체결강도추이시간별
  - endpoint `/api/dostk/mrkcond`
  - body: `{"stk_cd": ...}`
  - response table: `cntr_str_tm`
  - fields: current / 5m / 20m / 60m execution strength
- ka10054 변동성완화장치발동종목
  - endpoint `/api/dostk/stkinfo`
  - integrated-market query
  - response table: `motn_stk`

Scoring architecture:
- KOREA_CURRENT_V1_GAMMA = discovery/base score
- KOREA_CURRENT_V2_LIVE = GAMMA + execution-strength adjustment - VI penalty
- execution strength center = 100
- directional strength adjustment capped at +/-10
- VI penalty starts at 8 points
- outside regular market hours, no live adjustment is applied

Automation:
- 09:00~15:30 KST weekdays: refresh pulse every 60 seconds
- PREOPEN 08:30 remains separate
- NO AUTO ORDER remains
