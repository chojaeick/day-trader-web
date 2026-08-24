from pathlib import Path

KOREA=Path('live_server/korea.py')

OLD="        d=self.daytrade_entry_v11(limit,eval_limit,max_pages)\n"
NEW="        d=self.daytrade_entry_v1(limit,eval_limit,max_pages)\n"


def main():
    s=KOREA.read_text()
    if NEW in s and OLD not in s:
        print('DAYTRADE_ENTRY_V12_FIX_CALL_ALREADY_APPLIED')
        return
    if OLD not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: v12 base call')
    s=s.replace(OLD,NEW,1)
    KOREA.write_text(s)
    print('DAYTRADE_ENTRY_V12_FIX_CALL_OK')

if __name__=='__main__':
    main()
