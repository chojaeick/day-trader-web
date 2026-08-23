from pathlib import Path

API=Path('live_server/api.py')


def main():
    s=API.read_text()
    # V5.14 korea master/search uses re.fullmatch/re.match in request handlers.
    # api.py did not import re, which caused HTTP 500 on /api/v5/korea-symbol-search.
    if '\nimport re\n' not in '\n'+s:
        if 'import os\n' in s:
            s=s.replace('import os\n','import os\nimport re\n',1)
        else:
            s='import re\n'+s
    API.write_text(s)
    print('PREOPEN_KOREA_MASTER_HOTFIX_V15_OK')

if __name__=='__main__':
    main()
