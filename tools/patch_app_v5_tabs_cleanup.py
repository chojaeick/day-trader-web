from pathlib import Path
import re

p = Path('/home/ubuntu/day-trader-api/app_v5.py')
s = p.read_text()

# Replace only the tab declaration, then remove t3/t4/t5 blocks while preserving
# the existing indentation of the t1/t2 bodies. This avoids collapsing
# "with t1:" and its first statement onto one line.
old_decl = "t1,t2,t3,t4,t5=st.tabs(['⚡ Trading','💼 Portfolio','📰 Market Briefing','⚙️ Settings','🧪 Legacy / Debug'])"
new_decl = "t1,t2=st.tabs(['⚡ Trading','💼 Portfolio'])"

if old_decl in s:
    s = s.replace(old_decl, new_decl, 1)
elif new_decl not in s:
    raise SystemExit('V5_TAB_DECL_NOT_FOUND')

# Repair the exact syntax damage made by the previous patch if present.
s = s.replace("with t1:if market == 'KOREA':", "with t1:\n    if market == 'KOREA':")
s = s.replace("with t2:render_portfolio(market)", "with t2:\n    render_portfolio(market)")

# Drop obsolete tab bodies starting at with t3:, keeping only t1/t2 tail.
lines = s.splitlines()
cut = None
for i, line in enumerate(lines):
    if re.match(r'^\s*with\s+t3\s*:', line):
        cut = i
        break
if cut is not None:
    lines = lines[:cut]
    s = '\n'.join(lines).rstrip() + '\n'
else:
    s = '\n'.join(lines).rstrip() + '\n'

p.write_text(s)
print('V5_TABS_CLEANED_SAFE')
