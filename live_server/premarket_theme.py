
import sqlite3

from statistics import median



THEMES = {

    'SEMICONDUCTOR': [

        'NVDA','AMD','AVGO','MU','INTC','ARM','MRVL','QCOM',

        'TSM','ASML','AMAT','LRCX','KLAC','SMCI','SOXL','SOXS'

    ],

    'AI_INFRA': [

        'NVDA','AMD','AVGO','SMCI','DELL','VRT','ANET','PLTR','ORCL'

    ],

    'SPACE_AEROSPACE': [

        'RKLB','ASTS','ACHR','JOBY','RDW','LUNR','ONDS'

    ],

    'DEFENSE': [

        'LMT','NOC','RTX','GD','BA','AVAV','KTOS'

    ],

    'AIRLINES': [

        'DAL','UAL','AAL','LUV','JBLU'

    ],

    'NUCLEAR': [

        'OKLO','SMR','CCJ','LEU','UEC','UUUU','CEG','VST'

    ],

    'EV_BATTERY': [

        'TSLA','RIVN','LCID','QS','CHPT','ALB','LAC'

    ],

    'BIOTECH': [

        'MRNA','BNTX','CRSP','NTLA','BEAM','EDIT','IONS'

    ],

    'ENERGY': [

        'XOM','CVX','OXY','COP','SLB','HAL','RIG'

    ],

    'FINANCIAL': [

        'JPM','BAC','WFC','GS','MS','C','SOFI'

    ],

    'SOFTWARE_CLOUD': [

        'PLTR','CRM','NOW','SNOW','DDOG','NET','CRWD'

    ],

    'CRYPTO': [

        'COIN','MSTR','MARA','RIOT','CLSK','IREN'

    ],

}



def clamp(v, lo=0.0, hi=100.0):

    return max(lo, min(hi, v))



def f(v, d=0.0):

    try:

        return float(v)

    except Exception:

        return d



def build_report(db_path='daytrader.db'):

    c = sqlite3.connect(db_path)

    c.row_factory = sqlite3.Row



    quotes = {

        r['symbol']: dict(r)

        for r in c.execute("""

            SELECT * FROM quotes

        """).fetchall()

    }



    metrics = {

        r['symbol']: dict(r)

        for r in c.execute("""

            SELECT * FROM daily_metrics

        """).fetchall()

    }



    theme_rows = []



    for theme, symbols in THEMES.items():

        members = []



        for sym in symbols:

            q = quotes.get(sym)

            if not q:

                continue



            m = metrics.get(sym, {})



            change = f(q.get('change_pct'))

            volume = f(q.get('volume'))

            avg5v = f(m.get('avg5_volume'))



            rvol = volume / avg5v if avg5v > 0 else 0.0



            members.append({

                'symbol': sym,

                'price': f(q.get('price')),

                'change_pct': change,

                'volume': volume,

                'rvol': rvol,

                'atr5_pct': f(m.get('atr5_pct')),

                'ma5_slope_pct': f(m.get('ma5_slope_pct')),

            })



        if not members:

            continue



        changes = [x['change_pct'] for x in members]

        positive = sum(x > 0 for x in changes)

        breadth = positive / len(members)



        avg_change = sum(changes) / len(changes)

        med_change = median(changes)



        rvols = [x['rvol'] for x in members if x['rvol'] > 0]

        avg_rvol = sum(rvols) / len(rvols) if rvols else 0.0



        leader = max(

            members,

            key=lambda x: (

                x['change_pct'],

                x['rvol']

            )

        )



        # Score blocks normalized to 0~100.

        change_score = clamp(50 + avg_change * 8)

        breadth_score = clamp(breadth * 100)

        volume_score = clamp(avg_rvol * 45)

        leader_score = clamp(50 + leader['change_pct'] * 6)



        raw_power = (

            change_score * 0.30

            + breadth_score * 0.35

            + volume_score * 0.20

            + leader_score * 0.15

        )



        # V4.8.1: a single mover must not masquerade as a strong theme.

        seen=len(members)

        if seen >= 5:

            confidence='HIGH'

            confidence_factor=1.00

        elif seen >= 3:

            confidence='MEDIUM'

            confidence_factor=0.90

        elif seen == 2:

            confidence='LOW'

            confidence_factor=0.65

        else:

            confidence='INSUFFICIENT'

            confidence_factor=0.45



        power=raw_power*confidence_factor



        theme_rows.append({

            'theme': theme,

            'power': round(power, 1),

            'avg_change_pct': round(avg_change, 2),

            'median_change_pct': round(med_change, 2),

            'breadth_pct': round(breadth * 100, 1),

            'avg_rvol': round(avg_rvol, 2),

            'members_seen': len(members),

            'confidence': confidence,

            'leader': leader['symbol'],

            'leader_change_pct': round(leader['change_pct'], 2),

        })



    theme_rows.sort(

        key=lambda x: x['power'],

        reverse=True

    )



    theme_power = {

        x['theme']: x['power']

        for x in theme_rows

    }



    symbol_theme = {}

    for theme, syms in THEMES.items():

        for sym in syms:

            symbol_theme.setdefault(sym, []).append(theme)



    candidates = []



    for sym, q in quotes.items():

        change = f(q.get('change_pct'))

        price = f(q.get('price'))

        volume = f(q.get('volume'))



        m = metrics.get(sym, {})

        avg5v = f(m.get('avg5_volume'))

        avg5d = f(m.get('avg5_dollar_volume'))

        atr = f(m.get('atr5_pct'))



        rvol = volume / avg5v if avg5v > 0 else 0.0

        dollar = price * volume



        themes = symbol_theme.get(sym, [])

        best_theme = None

        best_theme_power = 0.0



        for t in themes:

            tp = theme_power.get(t, 0.0)

            if tp > best_theme_power:

                best_theme_power = tp

                best_theme = t



        # V4.8.1 candidate quality guard.

        # Cap extreme gap / RVOL so micro-cap spikes cannot dominate.

        gap_abs=abs(change)



        change_score=clamp(

            50 + max(-10,min(change,15))*4

        )



        rvol_capped=min(rvol,5.0)

        rvol_score=clamp(rvol_capped/5.0*100)



        if avg5d > 0:

            dollar_ratio=dollar/avg5d

        else:

            dollar_ratio=0.0



        dollar_score=clamp(dollar_ratio*60)



        liquidity_score=clamp(

            (dollar/5000000.0)*100

        )



        volatility_penalty=max(

            0.0,

            atr-15.0

        )*1.5



        extreme_gap_penalty=max(

            0.0,

            gap_abs-25.0

        )*1.2



        score=(

            change_score*0.25

            + rvol_score*0.20

            + dollar_score*0.15

            + liquidity_score*0.15

            + best_theme_power*0.25

            - volatility_penalty

            - extreme_gap_penalty

        )



        if change <= -3:

            score-=12



        quality_ok=bool(

            price>=2.0

            and volume>=50000

            and dollar>=1000000

            and gap_abs<=30

        )



        candidates.append({

            'symbol': sym,

            'price': round(price, 4),

            'change_pct': round(change, 2),

            'volume': round(volume, 0),

            'rvol': round(rvol, 2),

            'theme': best_theme or '-',

            'theme_power': round(best_theme_power, 1),

            'atr5_pct': round(atr, 2),

            'dollar_volume': round(dollar,0),

            'quality_ok': quality_ok,

            'score': round(score, 1),

        })



    candidates.sort(

        key=lambda x: x['score'],

        reverse=True

    )



    quality_candidates=[

        x for x in candidates

        if x.get('quality_ok')

    ]



    speculative=[

        x for x in candidates

        if not x.get('quality_ok')

    ]



    return {

        'version':'V4.8.1',

        'themes':theme_rows,

        'candidates':quality_candidates[:20],

        'speculative':speculative[:10]

    }





if __name__ == '__main__':

    report = build_report()



    print('=== THEME POWER ===')

    for x in report['themes']:

        print(

            f"{x['theme']:18} "

            f"P={x['power']:5.1f} "

            f"AVG={x['avg_change_pct']:+5.2f}% "

            f"BREADTH={x['breadth_pct']:5.1f}% "

            f"RVOL={x['avg_rvol']:4.2f} "

            f"CONF={x.get('confidence','-'):12} "

            f"LEADER={x['leader']} "

            f"{x['leader_change_pct']:+.2f}%"

        )



    print()

    print('=== QUALITY DAY-TRADE CANDIDATES ===')

    for i,x in enumerate(report['candidates'][:10],1):

        print(

            f"{i:2}. {x['symbol']:6} "

            f"SCORE={x['score']:5.1f} "

            f"CHG={x['change_pct']:+6.2f}% "

            f"RVOL={x['rvol']:5.2f} "

            f"THEME={x['theme']} "

            f"TP={x['theme_power']:5.1f}"

        )



    print()

    print('=== SPECULATIVE MOVERS ===')

    for i,x in enumerate(report.get('speculative',[])[:10],1):

        print(

            f"{i:2}. {x['symbol']:6} "

            f"SCORE={x['score']:5.1f} "

            f"CHG={x['change_pct']:+6.2f}% "

            f"RVOL={x['rvol']:5.2f}"

        )

