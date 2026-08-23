from pathlib import Path

API=Path('live_server/api.py')

BLOCK="""\n@app.get('/api/v5/momentum-diagnostic/USA')\ndef v40_momentum_diagnostic_usa():\n    try:\n        volume=k.volume_rank()\n        dollar=k.dollar_rank()\n        return {'ok':True,**k.v40_momentum_diagnostic(volume,dollar)}\n    except Exception as e:\n        return {'ok':False,'error':str(e)}\n"""

def main():
    a=API.read_text()
    # Remove the incorrectly inserted pre-app route block wherever it currently is.
    a=a.replace(BLOCK,'\n')

    # Reinsert only after the FastAPI app object exists.
    lines=a.splitlines(True)
    insert_at=None
    for i,line in enumerate(lines):
        compact=line.replace(' ','')
        if compact.startswith('app=FastAPI(') or compact.startswith('app:FastAPI=FastAPI('):
            insert_at=i+1
            break
    if insert_at is None:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app creation')

    text=''.join(lines[:insert_at])+BLOCK+''.join(lines[insert_at:])
    API.write_text(text)
    print('FINDER_MOMENTUM_DIAG_V40_FIX_OK')

if __name__=='__main__':
    main()
