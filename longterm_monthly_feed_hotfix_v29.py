from pathlib import Path
import re

API=Path('live_server/api.py')

START='# ===== V28 LONG-TERM MONTHLY HISTORY FEED =====\n'
END="\nmanual_scan_state={'last_started_monotonic':0.0,'last_result':None}\n"
ANCHOR="app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])\n"


def main():
    s=API.read_text()
    if START not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: V28 block')
    if ANCHOR not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI middleware anchor')

    # V28 was inserted before app=FastAPI, so @app.get executed before app existed.
    # Extract the whole V28 helper+route block and reinsert immediately after app creation.
    i=s.index(START)
    j=s.index(END,i)
    block=s[i:j]
    s=s[:i]+s[j:]

    # Avoid duplicate reinsertion if script is rerun after successful hotfix.
    if START not in s[s.index(ANCHOR)+len(ANCHOR):]:
        pos=s.index(ANCHOR)+len(ANCHOR)
        s=s[:pos]+'\n'+block+'\n'+s[pos:]

    API.write_text(s)
    print('LONGTERM_MONTHLY_FEED_HOTFIX_V29_OK')

if __name__=='__main__':
    main()
