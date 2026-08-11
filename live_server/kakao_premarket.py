
from __future__ import annotations



import os

import sys

import json

import time

import requests

from datetime import datetime

from zoneinfo import ZoneInfo

from dotenv import load_dotenv



from .premarket_briefing import build_premarket_briefing
from .premarket_history import build_and_store_message
from .premarket_validation import capture_from_snapshot, update_outcomes



ENV = "/home/ubuntu/day-trader-api/.env"

NY = ZoneInfo("America/New_York")



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



def load_env():

    load_dotenv(ENV, override=True)



def save_tokens(access, refresh=None):

    load_env()



    old_refresh = os.getenv("KAKAO_REFRESH_TOKEN", "").strip()



    try:

        lines = open(ENV, encoding="utf-8").read().splitlines()

    except Exception:

        lines = []



    lines = [

        x for x in lines

        if not x.startswith("KAKAO_ACCESS_TOKEN=")

        and not x.startswith("KAKAO_REFRESH_TOKEN=")

    ]



    lines.append("KAKAO_ACCESS_TOKEN=" + access)



    refresh = refresh or old_refresh

    if refresh:

        lines.append("KAKAO_REFRESH_TOKEN=" + refresh)



    open(ENV, "w", encoding="utf-8").write(

        "\n".join(lines) + "\n"

    )



def refresh_access_token():

    load_env()



    payload = {

        "grant_type": "refresh_token",

        "client_id": os.environ["KAKAO_REST_API_KEY"].strip(),

        "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"].strip(),

    }



    secret = os.getenv("KAKAO_CLIENT_SECRET", "").strip()



    if secret:

        payload["client_secret"] = secret



    r = requests.post(

        "https://kauth.kakao.com/oauth/token",

        data=payload,

        timeout=20,

    )



    d = r.json()



    if not d.get("access_token"):

        raise RuntimeError("KAKAO REFRESH FAILED: " + str(d))



    save_tokens(

        d["access_token"],

        d.get("refresh_token"),

    )



    return d["access_token"]



def send_text(text):

    load_env()



    token = os.getenv("KAKAO_ACCESS_TOKEN", "").strip()



    if not token:

        token = refresh_access_token()



    template = {

        "object_type": "text",

        "text": text[:200],

        "link": {

            "web_url": "https://developers.kakao.com",

            "mobile_web_url": "https://developers.kakao.com",

        },

    }



    def do_send(tok):

        return requests.post(

            "https://kapi.kakao.com/v2/api/talk/memo/default/send",

            headers={

                "Authorization": "Bearer " + tok,

                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",

            },

            data={

                "template_object": json.dumps(

                    template,

                    ensure_ascii=False,

                )

            },

            timeout=20,

        )



    r = do_send(token)



    if r.status_code in (401, 403):

        token = refresh_access_token()

        r = do_send(token)



    try:

        d = r.json()

    except Exception:

        d = {"raw": r.text}



    if r.status_code != 200 or d.get("result_code") != 0:

        raise RuntimeError(

            "KAKAO SEND FAILED HTTP="

            + str(r.status_code)

            + " RESULT="

            + str(d)

        )



    return d



def make_report(label="PREMARKET"):

    report = build_premarket_briefing()



    themes = report.get("themes") or []

    candidates = report.get("candidates") or []



    theme_parts = []

    leaders = []



    for row in themes[:3]:

        theme = row.get("theme") or "-"

        name = THEME_KO.get(theme, theme)

        power = float(row.get("final_power") or 0)



        theme_parts.append(

            name + " " + str(round(power, 1))

        )



        leader = row.get("leader")

        if leader:

            leaders.append(str(leader))



    symbols = [

        str(x.get("symbol"))

        for x in candidates[:5]

        if x.get("symbol")

    ]



    now = datetime.now(NY)



    text = (

        "DAY TRADER V4 " + label + "\n"

        + now.strftime("%m/%d %H:%M ET") + "\n"

        + "\ud14c\ub9c8: " + " / ".join(theme_parts) + "\n"

        + "\ub9ac\ub354: " + " / ".join(leaders[:3]) + "\n"

        + "\ub2e8\ud0c0\ud6c4\ubcf4: " + ", ".join(symbols) + "\n"

        + "\uc815\uaddc\uc7a5 Setup/Trigger \ud655\uc778 \ud6c4 \uc9c4\uc785"

    )



    return text[:200]



def send_report(label="PREMARKET"):

    text = build_and_store_message(label)



    try:

        capture_from_snapshot(label)

    except Exception as e:

        print("VALIDATION CAPTURE ERROR",label,repr(e),flush=True)



    result = send_text(text)



    print("=== KAKAO SENT ===")

    print(text)

    print(result)





def scheduler():

    sent = set()



    print("KAKAO PREMARKET SCHEDULER STARTED")



    while True:

        now = datetime.now(NY)



        if now.weekday() < 5:

            day = now.strftime("%Y-%m-%d")

            minute = now.hour * 60 + now.minute



            if 9 * 60 + 30 <= minute <= 10 * 60 + 35:

                try:

                    update_outcomes()

                except Exception as e:

                    print(

                        "PREMARKET OUTCOME ERROR",

                        repr(e),

                        flush=True,

                    )



            targets = {

                "T-20": 9 * 60 + 10,

                "FINAL": 9 * 60 + 25,

            }



            for label, target in targets.items():

                key = (day, label)



                if (

                    key not in sent

                    and target <= minute <= target + 1

                ):

                    try:

                        send_report(label)

                        sent.add(key)

                    except Exception as e:

                        print(

                            "KAKAO SCHEDULE ERROR",

                            label,

                            repr(e),

                            flush=True,

                        )



        time.sleep(20)



if __name__ == "__main__":

    if "--send-now" in sys.argv:

        send_report("TEST")

    else:

        scheduler()

