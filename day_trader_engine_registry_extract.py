#!/usr/bin/env python3
"""
DAY TRADER V4 - ENGINE REGISTRY RECOVERY EXTRACTOR

Reads /tmp/day_trader_engine_archaeology.txt and produces a concise
evidence-first registry report. No simulation. No downloads.
"""

from pathlib import Path
import re

SRC = Path("/tmp/day_trader_engine_archaeology.txt")
OUT = Path("/tmp/day_trader_engine_registry_recovered.md")

FAMILIES = {
    "V7": [r"\bv7\b", r"v7_", r"_v7", r"five_break", r"floor"],
    "SCALP": [r"scalp", r"tp/sl", r"time[-_ ]?out"],
    "REBOUND": [r"rebound", r"v490c"],
    "TREND": [r"trend_v", r"\btrend\b", r"probe", r"confirm", r"full_fail"],
    "DYNAMIC_RSI_MACD": [r"dynamic[_ ]?rsi", r"\bmacd\b"],
}

RESULT_WORDS = [
    "net", "gross", "win rate", "wins:", "losses:", "avg:", "best:", "worst:",
    "failed", "fail", "pass", "reject", "cost", "profit", "loss",
]

def main():
    if not SRC.exists():
        raise SystemExit(f"missing {SRC}")

    lines = SRC.read_text(errors="ignore").splitlines()

    out = []
    out.append("# DAY TRADER V4 — RECOVERED ENGINE EVIDENCE")
    out.append("")
    out.append("Source: /tmp/day_trader_engine_archaeology.txt")
    out.append("Rule: evidence only; unknown details remain UNKNOWN.")
    out.append("")

    for fam, pats in FAMILIES.items():
        out.append(f"## {fam}")
        hits = []
        for idx, line in enumerate(lines, 1):
            low = line.lower()
            if any(re.search(p, low) for p in pats):
                start = max(0, idx-3)
                end = min(len(lines), idx+3)
                block = lines[start:end]
                # prioritize code/result-looking evidence
                score = sum(1 for b in block if any(w in b.lower() for w in RESULT_WORDS))
                hits.append((score, idx, block))

        # de-duplicate overlapping regions
        selected = []
        used = set()
        for score, idx, block in sorted(hits, key=lambda x: (-x[0], x[1])):
            key = idx // 5
            if key in used:
                continue
            used.add(key)
            selected.append((score, idx, block))
            if len(selected) >= 30:
                break

        if not selected:
            out.append("- No direct evidence found.")
            out.append("")
            continue

        for score, idx, block in sorted(selected, key=lambda x: x[1]):
            out.append(f"### Evidence around line {idx}")
            out.append("```text")
            out.extend(block)
            out.append("```")
            out.append("")

    # file inventory lines for likely version names
    out.append("## VERSION-NAMED FILES")
    seen = []
    for line in lines:
        low = line.lower()
        if any(k in low for k in [
            "v1","v2","v3","v4","v5","v6","v7","scalp","rebound","trend",
            "dynamic","macd"
        ]):
            if "/" in line and line not in seen:
                seen.append(line)

    for line in seen[:250]:
        out.append(f"- {line}")

    OUT.write_text("\n".join(out), encoding="utf-8")
    print("REPORT", OUT)
    print("LINES", len(out))
    print("DONE")

if __name__ == "__main__":
    main()
