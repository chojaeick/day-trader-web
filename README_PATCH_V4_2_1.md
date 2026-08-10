# V4.2.1 RVOL SESSION FIX — PATCH

Changed file only:
- live_server/analytics.py

Problem
- Outside regular market hours, RVOL progress was forced to 0.08.
- That inflated completed-day RVOL by ~12.5x.
- Example observed: GDX ~32.4x while simple completed-day RVOL was ~2.59x.

Fix
- REGULAR session (09:30–16:00 ET weekdays):
  progress = max(0.08, elapsed_minutes / 390)
- Outside REGULAR session:
  progress = 1.0

Effect
- After-hours / premarket / weekend reference RVOL uses full completed-day volume vs avg5 daily volume.
- Premarket RVOL remains a future separate metric; this patch does not treat premarket as regular-session RVOL.
- No automatic order.
