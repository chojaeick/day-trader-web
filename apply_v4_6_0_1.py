from pathlib import Path
import sys
root=Path(sys.argv[1] if len(sys.argv)>1 else '.')
scanner_path=root/'live_server'/'scanner.py'
api_path=root/'live_server'/'api.py'
app_path=root/'app.py'
scanner=scanner_path.read_text(encoding='utf-8')
api=api_path.read_text(encoding='utf-8')
app=app_path.read_text(encoding='utf-8')

repls_scanner=[("    seen=set(); symbols=[]\n    for r in picked:\n        if r['symbol'] not in seen:\n            symbols.append(r['symbol']); seen.add(r['symbol'])\n        r['sources']=','.join(sorted(r['sources']))\n        r['origin']='CORE' if r['symbol'] in core else 'AUTO'\n","    seen=set(); symbols=[]\n    for r in picked:\n        if r['symbol'] not in seen:\n            symbols.append(r['symbol']); seen.add(r['symbol'])\n\n        # V4.6.0.1: sources may already be a normalized comma-separated string\n        # (notably EXTREME_WATCH). Never join a string character-by-character.\n        src=r.get('sources')\n        if isinstance(src,(set,list,tuple)):\n            r['sources']=','.join(sorted(str(x) for x in src if x))\n        elif isinstance(src,str):\n            r['sources']=src\n        else:\n            r['sources']=''\n\n        # Preserve EXTREME_WATCH provenance instead of overwriting it as AUTO.\n        if r['symbol'] in core:\n            r['origin']='CORE'\n        elif str(r.get('origin') or '').startswith('EXTREME'):\n            r['origin']='EXTREME_WATCH'\n        else:\n            r['origin']='AUTO'\n")]
repls_api=[
('    ds=_symset(discovery_rows)\n    ss=_symset(screen_rows)\n    ls=_symset(light_rows)\n    fs=_symset(finder_rows)\n    hs=_symset(tracker_rows)\n','    ds=_symset(discovery_rows)\n    es=_symset(extreme_rows)\n    qrs=_symset(quality_risk_rows)\n    ss=_symset(screen_rows)\n    ls=_symset(light_rows)\n    fs=_symset(finder_rows)\n    hs=_symset(tracker_rows)\n'),
("    dmap={str(r.get('symbol') or '').upper():r for r in discovery_rows}\n    smap={str(r.get('symbol') or '').upper():r for r in screen_rows}\n","    dmap={str(r.get('symbol') or '').upper():r for r in discovery_rows}\n    emap={str(r.get('symbol') or '').upper():r for r in extreme_rows}\n    qrmap={str(r.get('symbol') or '').upper():r for r in quality_risk_rows}\n    smap={str(r.get('symbol') or '').upper():r for r in screen_rows}\n"),
('    def stage(sym):\n        if sym in hs:return \'HEAVY5\'\n        if sym in fs:return \'FINDER\'\n        if sym in ls:return \'LIGHT\'\n        if sym in ds:return \'DISCOVERY\'\n        if sym in ss:return \'SCREENER\'\n        return \'NOT_SEEN\'\n\n    def reason(sym):\n        if sym in hs:return \'Heavy Tracker active\'\n        if sym in fs:return \'Finder TOP5 selected\'\n        if sym in ls:\n            r=lmap.get(sym) or {}\n            return f"Light only · score={r.get(\'finder_score\',r.get(\'score\'))} · fresh={r.get(\'fresh_mode\')}"\n        if sym in ds:\n            r=dmap.get(sym) or {}\n            grade=r.get(\'quality_grade\')\n            origin=r.get(\'origin\')\n            risk=r.get(\'chase_risk\')\n            return f"Discovery only · origin={origin} · quality={grade} · risk={risk}"\n        if sym in ss:\n            r=smap.get(sym) or {}\n            return f"Screener only · score={r.get(\'score\')} · eligible={r.get(\'eligible\')}"\n        return \'Not present in current discovery/screener snapshots\'\n','    def stage(sym):\n        if sym in hs:return \'HEAVY5\'\n        if sym in fs:return \'FINDER\'\n        if sym in ls:return \'LIGHT\'\n        if sym in es and sym in ds:return \'EXTREME_WATCH\'\n        if sym in es:return \'EXTREME\'\n        if sym in ds:return \'DISCOVERY\'\n        if sym in qrs:return \'QUALITY_RISK\'\n        if sym in ss:return \'SCREENER\'\n        return \'NOT_SEEN\'\n\n    def reason(sym):\n        if sym in hs:return \'Heavy Tracker active\'\n        if sym in fs:return \'Finder TOP5 selected\'\n        if sym in ls:\n            r=lmap.get(sym) or {}\n            return f"Light only · score={r.get(\'finder_score\',r.get(\'score\'))} · fresh={r.get(\'fresh_mode\')}"\n        if sym in es:\n            r=emap.get(sym) or dmap.get(sym) or {}\n            return (\n                f"Extreme mover · quality={r.get(\'quality_grade\',\'C_HIGH_RISK\')} · "\n                f"risk={r.get(\'chase_risk\',\'EXTREME\')} · "\n                + (\'active watch universe\' if sym in ds else \'separate extreme audit row\')\n            )\n        if sym in ds:\n            r=dmap.get(sym) or {}\n            grade=r.get(\'quality_grade\')\n            origin=r.get(\'origin\')\n            risk=r.get(\'chase_risk\')\n            return f"Discovery only · origin={origin} · quality={grade} · risk={risk}"\n        if sym in qrs:\n            r=qrmap.get(sym) or {}\n            return f"Quality risk · grade={r.get(\'quality_grade\')} · reason={r.get(\'quality_reasons\')}"\n        if sym in ss:\n            r=smap.get(sym) or {}\n            return f"Screener only · score={r.get(\'score\')} · eligible={r.get(\'eligible\')}"\n        return \'Not present in current discovery/extreme/screener snapshots\'\n'),
("    source_counts={}\n    for r in discovery_rows:\n        src=str(r.get('sources') or '')\n        for s0 in [x.strip() for x in src.split(',') if x.strip()]:\n            source_counts[s0]=source_counts.get(s0,0)+1\n","    source_counts={}\n    for r in discovery_rows:\n        src=r.get('sources')\n        if isinstance(src,str):\n            parts=[x.strip() for x in src.split(',') if x.strip()]\n        elif isinstance(src,(set,list,tuple)):\n            parts=[str(x).strip() for x in src if str(x).strip()]\n        else:\n            parts=[]\n        for s0 in parts:\n            source_counts[s0]=source_counts.get(s0,0)+1\n"),
('        r=dmap.get(sym) or smap.get(sym) or lmap.get(sym) or fmap.get(sym) or hmap.get(sym) or qmap.get(sym) or {}\n','        r=hmap.get(sym) or fmap.get(sym) or lmap.get(sym) or dmap.get(sym) or emap.get(sym) or qrmap.get(sym) or smap.get(sym) or qmap.get(sym) or {}\n'),
]

if 'V4.6.0.1: sources may already' in scanner:
    raise SystemExit('PATCH ABORTED: V4.6.0.1 scanner already present; no files changed.')
for old,new in repls_scanner:
    if old not in scanner: raise SystemExit('PATCH ABORTED: scanner anchor missing; no files changed.')
for old,new in repls_api:
    if old not in api: raise SystemExit('PATCH ABORTED: api anchor missing; no files changed.')

for old,new in repls_scanner: scanner=scanner.replace(old,new,1)
for old,new in repls_api: api=api.replace(old,new,1)
if "st.caption('V4.6.0 · SCANNER COVERAGE AUDIT + V4.5.5 VALIDATION · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY')" in app: app=app.replace("st.caption('V4.6.0 · SCANNER COVERAGE AUDIT + V4.5.5 VALIDATION · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY')","st.caption('V4.6.0.1 · COVERAGE AUDIT CORRECTNESS FIX · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY')",1)

scanner_path.write_text(scanner,encoding='utf-8')
api_path.write_text(api,encoding='utf-8')
app_path.write_text(app,encoding='utf-8')
print('PATCHED:',scanner_path)
print('PATCHED:',api_path)
print('PATCHED:',app_path)
