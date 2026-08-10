# V4.4.1 LIGHT TRACKER 20 + LIVE ROTATION — PATCH

Requires
- V4.4.0 Broad Market Finder already applied.

Changed files only
- live_server/api.py
- live_server/v4_engine.py

What changes

1) Light Tracker 20
- Broad market candidates are scored first.
- The best 20 become the Light Tracker pool.
- Heavy Tracker / Finder TOP5 is selected only from those 20.
- Finder API now also carries `light_rows`, `light_count`, and `rotation_seconds`.

2) Faster local rotation
- Finder local ranking: every 60s -> every 30s.
- Broad Kiwoom market discovery remains ~120s during REGULAR session.
- This does not double the market-wide ranking API load.

3) Live fade protection
- Uses the V4.4.0 recent 5m / 15m return and volume acceleration.
- If recent 5m return is negative, ranking receives a bounded fade penalty.
- If the symbol was recently Heavy-Tracked and its measured Power is also negative,
  the fade penalty becomes stronger.

This directly targets the observed failure:
- a stock can be +10% on the day,
- but if it is currently falling and Power is negative,
- it should not remain Finder #1/#2 merely because of the earlier move.

4) Diagnostics
Light rows expose:
- ret_5m
- ret_15m
- volume_accel
- recent_score
- observed_power (when available)
- fade_penalty
- light_rank

Not changed
- Entry Trigger thresholds
- Position/Exit/Floor logic
- Kiwoom TR IDs / schemas
- Korea logic
- No automatic orders
