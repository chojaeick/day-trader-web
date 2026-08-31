from pathlib import Path
import re

p = Path('/home/ubuntu/day-trader-api/app_v5.py')
s = p.read_text()

# Collapse the temporary 5-tab shell to the two active pages we are keeping
# during the KR/US modularization phase.  Trading keeps the KR/US module
# dispatch already installed; Portfolio stays as the personal-holdings page
# until the dedicated replacement is finished.
pat = re.compile(
    r"t1\s*,\s*t2\s*,\s*t3\s*,\s*t4\s*,\s*t5\s*=\s*st\.tabs\(\[[^\n]+\]\)\s*\n"
    r"with\s+t1:\s*(?P<t1>.*?)\n"
    r"with\s+t2:\s*(?P<t2>.*?)\n"
    r"with\s+t3:.*?\n"
    r"with\s+t4:.*?\n"
    r"with\s+t5:.*?(?=\n\Z|\n[^ \t])",
    re.S,
)

m = pat.search(s)
if m:
    t1 = m.group('t1').rstrip()
    t2 = m.group('t2').rstrip()
    repl = "t1,t2=st.tabs(['⚡ Trading','💼 Portfolio'])\nwith t1:" + t1 + "\nwith t2:" + t2
    s = s[:m.start()] + repl + s[m.end():]
elif "st.tabs(['⚡ Trading','💼 Portfolio'])" in s or 'st.tabs([\"⚡ Trading\",\"💼 Portfolio\"])' in s:
    print('V5_TABS_ALREADY_CLEAN')
    raise SystemExit(0)
else:
    # Safer exact-tail fallback for the production form seen in v22.
    marker = "t1,t2,t3,t4,t5=st.tabs(['⚡ Trading','💼 Portfolio','📰 Market Briefing','⚙️ Settings','🧪 Legacy / Debug'])"
    i = s.find(marker)
    if i < 0:
        raise SystemExit('V5_TAB_BLOCK_NOT_FOUND')
    head = s[:i]
    tail = s[i:]
    lines = tail.splitlines()
    # locate start of each with-block
    idx = {}
    for n,line in enumerate(lines):
        z=line.strip()
        if z.startswith('with t1:'): idx['t1']=n
        elif z.startswith('with t2:'): idx['t2']=n
        elif z.startswith('with t3:'): idx['t3']=n
        elif z.startswith('with t4:'): idx['t4']=n
        elif z.startswith('with t5:'): idx['t5']=n
    if not all(k in idx for k in ('t1','t2','t3','t4','t5')):
        raise SystemExit('V5_TAB_WITH_BLOCKS_NOT_FOUND')
    t1_lines=lines[idx['t1']:idx['t2']]
    t2_lines=lines[idx['t2']:idx['t3']]
    new=["t1,t2=st.tabs(['⚡ Trading','💼 Portfolio'])"] + t1_lines + t2_lines
    s=head+'\n'.join(new)+'\n'

p.write_text(s)
print('V5_TABS_CLEANED')
