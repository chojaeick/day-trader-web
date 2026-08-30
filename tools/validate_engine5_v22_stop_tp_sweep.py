from __future__ import annotations

"""
V22 exit-only validation sweep.

This script is intentionally a launcher/spec guard for the V22 stop/TP sweep.
It verifies the requested matrix and then delegates to the existing integrated
history validator once the variant-capable simulator is present.

Requested matrix:
A   = current structural stop, TP1 2.0x band
B5  = max 2% initial loss cap for first 5m, TP1 2.0x
B10 = max 2% initial loss cap for first 10m, TP1 2.0x
C   = current structural stop, TP1 1.5x band
D5  = max 2% initial loss cap for first 5m, TP1 1.5x
D10 = max 2% initial loss cap for first 10m, TP1 1.5x

NOTE: This file is a guard only and must not silently claim results. It exits
with a clear message until the variant-capable simulator is wired in.
"""

MATRIX = [
    ("A", None, None, 2.0),
    ("B5", 2.0, 5, 2.0),
    ("B10", 2.0, 10, 2.0),
    ("C", None, None, 1.5),
    ("D5", 2.0, 5, 1.5),
    ("D10", 2.0, 10, 1.5),
]


def main():
    print("=== V22 STOP/TP SWEEP SPEC ===")
    for name, cap_pct, cap_minutes, tp1_mult in MATRIX:
        print(f"{name}: cap={cap_pct}, minutes={cap_minutes}, tp1={tp1_mult}x")
    raise SystemExit(
        "Variant-capable simulator not wired yet. Do NOT treat this as a completed validation."
    )


if __name__ == "__main__":
    main()
