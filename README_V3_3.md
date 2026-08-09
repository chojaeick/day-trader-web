# DAY TRADER WEB V3.3 — MARKET LANGUAGE UX

Changes
- Replace BULL/BEAR/NEUTRAL with Korean labels:
  상승 우세 / 하락 우세 / 혼조
- Replace raw session/status language with:
  정규장 거래중 / 프리마켓 / 애프터마켓 / 장 마감
- Remove internal codes such as GAMMA_FALLBACK and OFF-HOURS from Trading.
- KOREA Market Context now shows:
  current trading time / candidate mood / preopen data usage / intraday execution-data usage
- KOREA stock selector shows `종목명 (코드)`.
- USA selector also prefers `name (ticker)` when available.
- Candidate TOP10 gets sorting:
  engine rank / quality / candidate score / change / volume strength / low risk
- User-facing quality labels:
  일반 / 주의 / 고위험 / 제외
- Internal engine codes remain available in Research/Diagnostics.

No scoring-engine changes.
No auto-order changes.
