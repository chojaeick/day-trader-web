# DAY TRADER WEB V2.1 — News Catalyst + AI Intelligence

## Purpose
Adds a material-news intelligence layer to the already timestamp-gated PREOPEN engine.

## Optional OpenAI integration
Set on the AWS backend:
- OPENAI_API_KEY=...
- DAYTRADER_NEWS_AI_MODEL=gpt-5   (optional; default gpt-5)

The server uses the OpenAI Responses API with built-in web search and Structured Outputs.
No key is embedded in this package.

If OPENAI_API_KEY is absent or the API call fails:
- PREOPEN report generation still succeeds,
- technical / premarket intelligence still works,
- News Catalyst weight is zero,
- the snapshot records that News AI was unavailable.

## Per-symbol News fields
- Catalyst: NONE / LOW / MEDIUM / HIGH / CRITICAL
- News Bias: BULLISH / BEARISH / MIXED / NEUTRAL
- News LONG / SHORT power
- AI Confidence
- Price Reaction: CONFIRMED / DIVERGENT / UNKNOWN
- concise Korean summary
- risk/caveat
- URL citation metadata from web search response when provided

## Final score
News does NOT replace the technical/pre-market score.
The technical score is frozen first, then news receives a bounded weight:
- none: 0
- low: small
- medium: moderate
- high/critical: larger
The weight is reduced when premarket data is not fresh or price reaction diverges.

## Archive
The generated report stores:
- technical input state
- premarket freshness
- news judgement
- final LONG/SHORT
- source metadata
- model version

This makes later forward validation possible.

## Safety
- CURRENT Trading Score unchanged
- SHADOW remains research-only
- NO AUTO ORDER
