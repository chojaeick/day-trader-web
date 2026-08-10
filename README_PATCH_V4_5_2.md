# V4.5.2 STAGE FUNNEL / OBSERVED REACH SEMANTICS

Changed file
- app.py only

Why
V4.5.0 Episode UI inferred "ENTRY reached" from downstream states such as HOLD,
PARTIAL_EXIT, EXIT_READY, HARD_EXIT. That could report ENTRY reached even when
no actual ENTRY validation mark existed.

V4.5.2 rule
- SETUP reached = actual SETUP Stage Anchor exists
- READY reached = actual READY Stage Anchor exists
- ENTRY reached = actual ENTRY Stage Anchor exists
- Downstream states never imply ENTRY for validation reporting

Adds
- Episode metrics now show READY actual observed / ENTRY actual observed
- Stage Funnel:
  Episode -> SETUP -> READY -> ENTRY
- SETUP->READY conversion
- READY->ENTRY conversion
- SETUP->ENTRY conversion
- average minutes from Episode start to READY
- average minutes from Episode start to ENTRY
- low-sample warning for ENTRY

No backend change
No DB migration
No Finder/Power/Fresh/Entry threshold change
No order behavior change

Apply
python3 apply_v4_5_2.py app.py
python3 -m py_compile app.py
