from pathlib import Path
import re

API=Path('live_server/api.py')


def main():
    s=API.read_text()

    # v07 already replaced the entire validator. The remaining bug is the
    # over-escaped regex literal: r'\\d{6}' matches a literal backslash+d,
    # so valid codes such as 379800 are rejected. Fix both validator routes.
    before=s.count("_re.fullmatch(r'\\\\d{6}',q)")
    s=s.replace("_re.fullmatch(r'\\\\d{6}',q)", "_re.fullmatch(r'\\d{6}',q)")
    after=s.count("_re.fullmatch(r'\\\\d{6}',q)")

    if before < 1:
        # Accept spacing variants, but fail loudly if the actual defect is absent.
        s2,n=re.subn(r"_re\.fullmatch\(r'\\\\d\{6\}',\s*q\)", "_re.fullmatch(r'\\d{6}',q)", s)
        s=s2
        if n==0:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: overescaped korea 6-digit regex')

    API.write_text(s)
    print('PREOPEN_KOREA_VALIDATOR_HOTFIX_V09_OK')


if __name__=='__main__':
    main()
