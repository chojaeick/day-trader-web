# V3.5 PATCH — Market state + autoload + candidate cleanup

Included files only:
- app.py
- live_server/api.py
- live_server/scanner.py

Fixes
1. USA market session is no longer inferred from backend `LIVE DATA`.
   - weekend => 장 마감
   - 04:00~09:30 ET => 프리마켓
   - 09:30~16:00 ET => 정규장 거래중
   - 16:00~20:00 ET => 애프터마켓
   - outside => 장 마감
   - Holiday-calendar refinement is still a future improvement.

2. USA market direction language
   - regular session: current QQQ move
   - outside regular session: explicitly labelled recent regular-session reference
   - market context shows SPY / QQQ / SMH separately.

3. Automatic loading
   - Streamlit already loads current API data on app open/rerun.
   - Korea universe is now also discovered automatically at backend startup.
   - Korea universe refreshes every 10 minutes during 08:00~15:40 KST weekdays.

4. Candidate cleanup
   - USA Candidate TOP10 only displays names that are present in the Quality Gate universe.
   - removes confusing quality/name blanks.
   - core ETF rows use real display names instead of `CORE`.

5. Korea Briefing
   - adds a visible Korean text summary derived from PREOPEN technical/auction data.
   - News AI is still not connected to Korea briefing.

6. Korea minute charts
   - intentionally unchanged; verified domestic minute-bar API still needs to be connected before BUY NOW is enabled.

No historical data included.
No database included.
No auto-order change.
