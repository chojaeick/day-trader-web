
from __future__ import annotations



import json

import sqlite3

from datetime import datetime

from zoneinfo import ZoneInfo



from .premarket_briefing import build_premarket_briefing



NY = ZoneInfo("America/New_York")

DB_PATH = "/home/ubuntu/day-trader-api/daytrader.db"



THEME_KO = {

    "SEMICONDUCTOR": "\ubc18\ub3c4\uccb4",

    "SPACE_AEROSPACE": "\uc6b0\uc8fc\ud56d\uacf5",

    "ENERGY": "\uc5d0\ub108\uc9c0",

    "AI_INFRA": "AI\uc778\ud504\ub77c",

    "EV_BATTERY": "EV\ubc30\ud130\ub9ac",

    "CRYPTO": "\ud06c\ub9bd\ud1a0",

    "BIOTECH": "\ubc14\uc774\uc624",

    "NUCLEAR": "\uc6d0\uc804",

    "DEFENSE": "\ubc29\uc0b0",

    "AIRLINES": "\ud56d\uacf5",

    "FINANCIAL": "\uae08\uc735",

    "SOFTWARE_CLOUD": "SW\ud074\ub77c\uc6b0\ub4dc",

}



def _conn():

    c = sqlite3.connect(DB_PATH)

    c.row_factory = sqlite3.Row

    c.execute("""

        CREATE TABLE IF NOT EXISTS premarket_intel_snapshots (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            trade_date TEXT NOT NULL,

            label TEXT NOT NULL,

            captured_at TEXT NOT NULL,

            payload_json TEXT NOT NULL

        )

    """)

    c.execute("""

        CREATE INDEX IF NOT EXISTS

        idx_premarket_intel_date_label

        ON premarket_intel_snapshots(trade_date,label,id)

    """)

    c.commit()

    return c



def save_snapshot(label, report):

    now = datetime.now(NY)

    day = now.strftime("%Y-%m-%d")



    c = _conn()

    c.execute(

        """

        INSERT INTO premarket_intel_snapshots

        (trade_date,label,captured_at,payload_json)

        VALUES (?,?,?,?)

        """,

        (

            day,

            label,

            now.isoformat(),

            json.dumps(report, ensure_ascii=False),

        ),

    )

    c.commit()

    c.close()



def latest_snapshot(trade_date, label):

    c = _conn()

    row = c.execute(

        """

        SELECT *

        FROM premarket_intel_snapshots

        WHERE trade_date=? AND label=?

        ORDER BY id DESC

        LIMIT 1

        """,

        (trade_date,label),

    ).fetchone()

    c.close()



    if not row:

        return None



    try:

        return json.loads(row["payload_json"])

    except Exception:

        return None



def _theme_map(report):

    return {

        str(x.get("theme")):

        float(x.get("final_power") or 0)

        for x in (report.get("themes") or [])

        if x.get("theme")

    }



def _candidate_set(report, n=5):

    return [

        str(x.get("symbol"))

        for x in (report.get("candidates") or [])[:n]

        if x.get("symbol")

    ]



def build_and_store_message(label):

    now = datetime.now(NY)

    day = now.strftime("%Y-%m-%d")



    report = build_premarket_briefing()



    previous = None

    if label == "FINAL":

        previous = latest_snapshot(day, "T-20")



    themes = report.get("themes") or []

    candidates = _candidate_set(report, 5)



    parts = []



    prev_map = _theme_map(previous) if previous else {}



    for row in themes[:3]:

        theme = str(row.get("theme") or "-")

        name = THEME_KO.get(theme, theme)

        power = float(row.get("final_power") or 0)



        if theme in prev_map:

            delta = power - prev_map[theme]

            arrow = "+" if delta >= 0 else ""

            parts.append(

                name + " "

                + f"{power:.1f}"

                + "(" + arrow + f"{delta:.1f}" + ")"

            )

        else:

            parts.append(

                name + " " + f"{power:.1f}"

            )



    lines = [

        "DAY TRADER V4 " + label,

        now.strftime("%m/%d %H:%M ET"),

        "\ud14c\ub9c8: " + " / ".join(parts),

        "\ub2e8\ud0c0: " + ", ".join(candidates),

    ]



    if previous:

        old_candidates = _candidate_set(previous, 5)



        new_syms = [

            x for x in candidates

            if x not in old_candidates

        ]



        dropped = [

            x for x in old_candidates

            if x not in candidates

        ]



        if new_syms:

            lines.append(

                "\uc2e0\uaddc: " + ",".join(new_syms[:3])

            )



        if dropped:

            lines.append(

                "\uc774\ud0c8: " + ",".join(dropped[:3])

            )



    lines.append(

        "Setup/Trigger \ud655\uc778 \ud6c4 \uc9c4\uc785"

    )



    save_snapshot(label, report)



    return "\n".join(lines)[:200]



def status():

    now = datetime.now(NY)

    day = now.strftime("%Y-%m-%d")

    c = _conn()



    rows = c.execute(

        """

        SELECT id,trade_date,label,captured_at

        FROM premarket_intel_snapshots

        WHERE trade_date=?

        ORDER BY id DESC

        LIMIT 10

        """,

        (day,),

    ).fetchall()



    c.close()

    return [dict(x) for x in rows]



if __name__ == "__main__":

    print("=== PREMARKET HISTORY STATUS ===")

    for x in status():

        print(x)

