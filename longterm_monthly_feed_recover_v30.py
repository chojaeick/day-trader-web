from pathlib import Path
import re

API = Path('live_server/api.py')
START = '# ===== V28 LONG-TERM MONTHLY HISTORY FEED ====='
ROUTE = "@app.get('/api/v5/monthly-history/{market}/{symbol}')"
ANCHOR = "app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])"


def main():
    s = API.read_text()
    if START not in s or ROUTE not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: V28 monthly block')
    if ANCHOR not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI middleware anchor')

    start = s.index(START)
    anchor_pos = s.index(ANCHOR)
    if start > anchor_pos:
        print('LONGTERM_MONTHLY_FEED_RECOVER_V30_ALREADY_OK')
        return

    # Robustly find the end of the V28 block by the next top-level statement
    # that follows the route function. In the broken source this is v4=CleanEngine(...),
    # while earlier variants may still contain manual_scan_state.
    candidates = []
    for marker in [
        "\nv4=CleanEngine(s.db_path)",
        "\nmanual_scan_state={'last_started_monotonic':0.0,'last_result':None}",
        "\n# V5 runtime load mode.",
    ]:
        p = s.find(marker, start)
        if p != -1:
            candidates.append(p)
    if not candidates:
        # Fallback: find the first top-level declaration after the route function.
        m = re.search(r"\n(?=(?:async def |def |class |[A-Za-z_][A-Za-z0-9_]*\s*=))", s[s.index(ROUTE, start)+len(ROUTE):])
        if not m:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: end of V28 block')
        route_tail = s.index(ROUTE, start)+len(ROUTE)
        end = route_tail + m.start()
    else:
        end = min(candidates)

    block = s[start:end].rstrip() + '\n'
    s = s[:start] + s[end:]

    # Recompute anchor because source length changed.
    pos = s.index(ANCHOR) + len(ANCHOR)
    s = s[:pos] + '\n\n' + block + s[pos:]

    API.write_text(s)
    print('LONGTERM_MONTHLY_FEED_RECOVER_V30_OK')


if __name__ == '__main__':
    main()
