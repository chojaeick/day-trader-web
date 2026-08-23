from pathlib import Path
import re

API = Path('live_server/api.py')
APP = Path('app_v5.py')


def patch_api():
    s = API.read_text()
    original = s

    # KRX product codes are not guaranteed to be digits-only. Some ETF/ETN/
    # leveraged products contain letters (e.g. 0193T0). Validation must be
    # format-light and existence-heavy: accept a canonical 6-char alphanumeric
    # code, then let Kiwoom decide whether it actually exists.
    replacements = [
        ("_re.fullmatch(r'\\d{6}',q)", "_re.fullmatch(r'[0-9A-Z]{6}',q)"),
        ("_re.fullmatch(r'\\d{6}', q)", "_re.fullmatch(r'[0-9A-Z]{6}', q)"),
        ("re.fullmatch(r'\\d{6}',q)", "re.fullmatch(r'[0-9A-Z]{6}',q)"),
        ("re.fullmatch(r'\\d{6}', q)", "re.fullmatch(r'[0-9A-Z]{6}', q)"),
    ]
    for old, new in replacements:
        s = s.replace(old, new)

    s = s.replace("KOREA_REQUIRES_6_DIGIT_CODE", "KOREA_REQUIRES_6_CHAR_CODE")

    if s == original:
        # Handle source variants with regex substitution, but fail loudly if the
        # expected validator is not present.
        s, n = re.subn(
            r"(?:_re|re)\.fullmatch\(r'\\d\{6\}',\s*q\)",
            "_re.fullmatch(r'[0-9A-Z]{6}',q)",
            s,
        )
        if n == 0 and "symbol-validate" not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: korea symbol validator')
        s = s.replace("KOREA_REQUIRES_6_DIGIT_CODE", "KOREA_REQUIRES_6_CHAR_CODE")

    API.write_text(s)


def patch_app():
    s = APP.read_text()
    # Copy only; API remains the source of truth for existence validation.
    s = s.replace('국장은 6자리 코드를 Kiwoom으로 직접 검증합니다.',
                  '국장은 6자리 영숫자 종목코드를 Kiwoom으로 직접 검증합니다.')
    s = s.replace('국장은 현재 6자리 종목코드 기준으로 검증합니다.',
                  '국장은 6자리 영숫자 종목코드를 Kiwoom으로 직접 검증합니다.')
    s = s.replace("placeholder='SOXL / NVDA / 005930'",
                  "placeholder='SOXL / 005930 / 0193T0'")
    s = s.replace('KOREA_REQUIRES_6_DIGIT_CODE', 'KOREA_REQUIRES_6_CHAR_CODE')
    APP.write_text(s)


def main():
    patch_api()
    patch_app()
    print('PREOPEN_KOREA_ALNUM_CODE_V10_OK')


if __name__ == '__main__':
    main()
