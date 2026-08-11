
from __future__ import annotations



from .premarket_theme import build_report

from .kiwoom_industry import fetch_industry_strength



THEME_TO_INDUSTRY_CODES = {

    "SEMICONDUCTOR": {"710","720"},

    "AI_INFRA": {"230","740","820"},

    "SPACE_AEROSPACE": {"210","230"},

    "DEFENSE": {"210","230"},

    "AIRLINES": {"210"},

    "NUCLEAR": {"10","930"},

    "EV_BATTERY": {"110","140","340"},

    "BIOTECH": {"510","520","530"},

    "ENERGY": {"10","930"},

    "FINANCIAL": {"610","620"},

    "SOFTWARE_CLOUD": {"740","810","820"},

    "CRYPTO": {"610"},

}



def _match_industry(theme, industries):

    codes = THEME_TO_INDUSTRY_CODES.get(theme, set())



    matched = [

        row for row in industries

        if str(row.get("inds_cd") or "") in codes

    ]



    if not matched:

        return None



    return max(

        matched,

        key=lambda x: float(x.get("industry_power") or 0)

    )



def build_premarket_briefing(db_path="daytrader.db"):

    custom = build_report(db_path)

    industries = fetch_industry_strength()



    themes = []



    for row in custom.get("themes", []):

        ind = _match_industry(

            row.get("theme"),

            industries

        )



        cp = float(row.get("power") or 0)

        ip = float(ind.get("industry_power") or 0) if ind else 0.0



        final_power = (

            cp * 0.70 + ip * 0.30

            if ind

            else cp

        )



        out = dict(row)

        out["industry"] = ind.get("inds_nm") if ind else "-"

        out["industry_power"] = round(ip, 1)

        out["final_power"] = round(final_power, 1)



        themes.append(out)



    themes.sort(

        key=lambda x: float(x.get("final_power") or 0),

        reverse=True

    )



    theme_power = {

        x["theme"]: x["final_power"]

        for x in themes

    }



    candidates = []



    for row in custom.get("candidates", []):

        out = dict(row)



        fp = float(

            theme_power.get(

                out.get("theme"),

                out.get("theme_power") or 0

            )

        )



        out["context_power"] = round(fp, 1)

        out["final_score"] = round(

            float(out.get("score") or 0) * 0.75

            + fp * 0.25,

            1

        )



        candidates.append(out)



    candidates.sort(

        key=lambda x: float(x.get("final_score") or 0),

        reverse=True

    )



    return {

        "version": "V4.8.2",

        "industries": industries[:10],

        "themes": themes[:10],

        "candidates": candidates[:10],

        "speculative": custom.get("speculative", [])[:10],

    }



if __name__ == "__main__":

    report = build_premarket_briefing()



    print("=== OFFICIAL INDUSTRY TOP5 ===")

    for i, x in enumerate(report["industries"][:5], 1):

        print(

            i,

            x.get("inds_nm"),

            "P=", x.get("industry_power"),

            "1D=", x.get("perf_1d"),

        )



    print()

    print("=== FINAL THEME TOP5 ===")

    for i, x in enumerate(report["themes"][:5], 1):

        print(

            i,

            x.get("theme"),

            "FINAL=", x.get("final_power"),

            "CUSTOM=", x.get("power"),

            "IND=", x.get("industry_power"),

            "LEADER=", x.get("leader"),

        )



    print()

    print("=== DAY TRADE TOP10 ===")

    for i, x in enumerate(report["candidates"][:10], 1):

        print(

            i,

            x.get("symbol"),

            "FINAL=", x.get("final_score"),

            "CHG=", x.get("change_pct"),

            "RVOL=", x.get("rvol"),

            "THEME=", x.get("theme"),

            "CTX=", x.get("context_power"),

        )

