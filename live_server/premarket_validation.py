
from __future__ import annotations



import json

import sqlite3

from datetime import datetime

from zoneinfo import ZoneInfo



NY = ZoneInfo("America/New_York")

DB_PATH = "/home/ubuntu/day-trader-api/daytrader.db"



def _conn():

    c = sqlite3.connect(DB_PATH)

    c.row_factory = sqlite3.Row



    c.execute("""

        CREATE TABLE IF NOT EXISTS premarket_validation (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            trade_date TEXT NOT NULL,

            label TEXT NOT NULL,

            symbol TEXT NOT NULL,

            theme TEXT,

            rank INTEGER,

            score REAL,

            context_power REAL,

            anchor_price REAL,

            captured_at TEXT NOT NULL,



            ret_5m REAL,

            ret_15m REAL,

            ret_30m REAL,

            ret_60m REAL,

            mfe_pct REAL,

            mae_pct REAL,



            updated_at TEXT

        )

    """)



    c.execute("""

        CREATE UNIQUE INDEX IF NOT EXISTS

        idx_premarket_validation_unique

        ON premarket_validation(

            trade_date,

            label,

            symbol

        )

    """)



    c.commit()

    return c



def _latest_price(c, symbol):

    row = c.execute(

        """

        SELECT price

        FROM quotes

        WHERE symbol=?

        ORDER BY updated_at DESC

        LIMIT 1

        """,

        (symbol,),

    ).fetchone()



    if not row:

        return None



    try:

        return float(row["price"])

    except Exception:

        return None



def capture_from_snapshot(label):

    now = datetime.now(NY)

    day = now.strftime("%Y-%m-%d")



    c = _conn()



    snap = c.execute(

        """

        SELECT payload_json,captured_at

        FROM premarket_intel_snapshots

        WHERE trade_date=? AND label=?

        ORDER BY id DESC

        LIMIT 1

        """,

        (day,label),

    ).fetchone()



    if not snap:

        c.close()

        print("NO SNAPSHOT",day,label)

        return 0



    report = json.loads(snap["payload_json"])

    rows = report.get("candidates") or []



    count = 0



    for rank,row in enumerate(rows[:10],1):

        symbol = str(row.get("symbol") or "").upper()



        if not symbol:

            continue



        price = _latest_price(c,symbol)



        if not price or price <= 0:

            continue



        c.execute(

            """

            INSERT OR REPLACE INTO premarket_validation (

                trade_date,

                label,

                symbol,

                theme,

                rank,

                score,

                context_power,

                anchor_price,

                captured_at,

                updated_at

            )

            VALUES (?,?,?,?,?,?,?,?,?,?)

            """,

            (

                day,

                label,

                symbol,

                row.get("theme"),

                rank,

                float(row.get("final_score") or 0),

                float(row.get("context_power") or 0),

                price,

                snap["captured_at"],

                now.isoformat(),

            ),

        )



        count += 1



    c.commit()

    c.close()



    print("CAPTURED",day,label,count)

    return count



def status():

    now = datetime.now(NY)

    day = now.strftime("%Y-%m-%d")



    c = _conn()



    rows = c.execute(

        """

        SELECT

            label,

            symbol,

            rank,

            theme,

            anchor_price,

            ret_5m,

            ret_15m,

            ret_30m,

            ret_60m

        FROM premarket_validation

        WHERE trade_date=?

        ORDER BY label,rank

        """,

        (day,),

    ).fetchall()



    c.close()



    return [dict(x) for x in rows]



if __name__ == "__main__":

    print("=== PREMARKET VALIDATION STATUS ===")

    for row in status():

        print(row)




def _parse_ts(v):

    if not v:

        return None

    try:

        return datetime.fromisoformat(

            str(v).replace("Z","+00:00")

        )

    except Exception:

        return None



def _market_times(trade_date):

    from datetime import time, timezone



    d=datetime.strptime(

        trade_date,

        "%Y-%m-%d"

    ).date()



    def ny(h,m):

        return datetime.combine(

            d,

            time(h,m),

            tzinfo=NY

        )



    return {

        "open":ny(9,30),

        "ret_5m":ny(9,35),

        "ret_15m":ny(9,45),

        "ret_30m":ny(10,0),

        "ret_60m":ny(10,30),

    }



def _ticks_for_window(c,symbol,start_et,end_et):

    start_utc=start_et.astimezone(

        ZoneInfo("UTC")

    ).isoformat()



    end_utc=end_et.astimezone(

        ZoneInfo("UTC")

    ).isoformat()



    rows=c.execute(

        """

        SELECT price,ts

        FROM ticks

        WHERE symbol=?

          AND ts>=?

          AND ts<=?

        ORDER BY ts

        """,

        (

            symbol.upper(),

            start_utc,

            end_utc,

        ),

    ).fetchall()



    return [

        {

            "price":float(x["price"]),

            "ts":_parse_ts(x["ts"]),

        }

        for x in rows

        if x["price"] is not None

    ]



def _last_price_before(rows,target_et):

    target_utc=target_et.astimezone(

        ZoneInfo("UTC")

    )



    valid=[

        x for x in rows

        if x["ts"] is not None

        and x["ts"]<=target_utc

    ]



    if not valid:

        return None



    return float(valid[-1]["price"])



def update_outcomes():

    now=datetime.now(NY)

    day=now.strftime("%Y-%m-%d")



    c=_conn()



    vals=c.execute(

        """

        SELECT *

        FROM premarket_validation

        WHERE trade_date=?

        ORDER BY label,rank

        """,

        (day,),

    ).fetchall()



    if not vals:

        c.close()

        return {

            "trade_date":day,

            "rows":0,

            "updated":0,

        }



    mt=_market_times(day)

    updated=0



    for row in vals:

        anchor=float(row["anchor_price"] or 0)



        if anchor<=0:

            continue



        end_et=min(

            now,

            mt["ret_60m"]

        )



        if end_et < mt["open"]:

            continue



        ticks=_ticks_for_window(

            c,

            row["symbol"],

            mt["open"],

            end_et,

        )



        if not ticks:

            continue



        changes={}

        for col in (

            "ret_5m",

            "ret_15m",

            "ret_30m",

            "ret_60m",

        ):

            target=mt[col]



            if now < target:

                continue



            px=_last_price_before(

                ticks,

                target

            )



            if px is not None:

                changes[col]=round(

                    (px/anchor-1.0)*100,

                    4

                )



        prices=[

            float(x["price"])

            for x in ticks

            if float(x["price"])>0

        ]



        if prices:

            changes["mfe_pct"]=round(

                (max(prices)/anchor-1.0)*100,

                4

            )

            changes["mae_pct"]=round(

                (min(prices)/anchor-1.0)*100,

                4

            )



        if not changes:

            continue



        sets=[]

        args=[]



        for col,val in changes.items():

            sets.append(col+"=?")

            args.append(val)



        sets.append("updated_at=?")

        args.append(now.isoformat())

        args.append(row["id"])



        c.execute(

            "UPDATE premarket_validation SET "

            + ",".join(sets)

            + " WHERE id=?",

            args,

        )



        updated+=1



    c.commit()

    c.close()



    return {

        "trade_date":day,

        "rows":len(vals),

        "updated":updated,

    }



def performance_summary():

    now=datetime.now(NY)

    day=now.strftime("%Y-%m-%d")



    c=_conn()



    rows=c.execute(

        """

        SELECT *

        FROM premarket_validation

        WHERE trade_date=?

        ORDER BY label,rank

        """,

        (day,),

    ).fetchall()



    c.close()



    out={}



    for label in ("T-20","FINAL"):

        group=[

            dict(x)

            for x in rows

            if x["label"]==label

        ]



        stats={

            "count":len(group)

        }



        for col in (

            "ret_5m",

            "ret_15m",

            "ret_30m",

            "ret_60m",

        ):

            values=[

                float(x[col])

                for x in group

                if x.get(col) is not None

            ]



            if values:

                stats[col]={

                    "n":len(values),

                    "avg":round(

                        sum(values)/len(values),

                        3

                    ),

                    "hit_pct":round(

                        sum(v>0 for v in values)

                        /len(values)*100,

                        1

                    ),

                }



        out[label]=stats



    return out

