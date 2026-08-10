# V4.2.3 CHART SCALE + VOLUME — PATCH

Changed file only:
- app.py

Changes
- Keeps V4.2.2 focus windows:
  - 1m Trigger: latest 60 minutes
  - 5m Setup: latest 3 hours
- Tight price Y-axis around the actual recent range (zero=False).
- Price / VWAP / EMA9 / EMA20 remain overlaid.
- Adds a separate volume bar chart below each price chart.
- X-axis is shared and shown as HH:MM.
- Engine logic unchanged.
- No auto order.
