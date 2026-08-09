
from __future__ import annotations
import json, sqlite3, math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

def _f(v, default=0.0):
    try:
        x=float(v)
        return default if math.isnan(x) else x
    except Exception:
        return default

class PreOpenReportStore:
    """Immutable scheduled pre-open snapshots + generated intelligence report."""
    def __init__(self, db_path:str):
        self.db_path=db_path
        self._init()

    def con(self):
        c=sqlite3.connect(self.db_path,timeout=20)
        c.row_factory=sqlite3.Row
        return c

    def _init(self):
        with self.con() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS preopen_report_meta(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                label TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                scheduled INTEGER NOT NULL DEFAULT 0,
                model_version TEXT NOT NULL,
                qqq_pct REAL,
                smh_pct REAL,
                market_long_power REAL,
                market_short_power REAL,
                universe_count INTEGER,
                report_text TEXT,
                extra_json TEXT,
                UNIQUE(market,trade_date,label)
            )""")
            c.execute("""
            CREATE TABLE IF NOT EXISTS preopen_report_rows(
                report_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                current_rank INTEGER,
                shadow_rank INTEGER,
                current_score REAL,
                shadow_score REAL,
                price REAL,
                change_pct REAL,
                ma5 REAL,
                ma5_slope_pct REAL,
                rvol REAL,
                atr_pct REAL,
                long_power REAL,
                short_power REAL,
                recommendation TEXT,
                rationale TEXT,
                extra_json TEXT,
                PRIMARY KEY(report_id,symbol),
                FOREIGN KEY(report_id) REFERENCES preopen_report_meta(id) ON DELETE CASCADE
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_preopen_date ON preopen_report_meta(trade_date DESC,generated_at DESC)")
            c.commit()

    def save(self, report:dict):
        market=str(report.get('market') or 'USA').upper()
        trade_date=str(report.get('trade_date') or '')
        label=str(report.get('label') or 'PREOPEN_30').upper()
        generated_at=str(report.get('generated_at') or datetime.now(timezone.utc).isoformat())
        with self.con() as c:
            c.execute("""
              INSERT INTO preopen_report_meta(
                market,trade_date,label,generated_at,scheduled,model_version,
                qqq_pct,smh_pct,market_long_power,market_short_power,
                universe_count,report_text,extra_json
              ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(market,trade_date,label) DO UPDATE SET
                generated_at=excluded.generated_at,
                scheduled=excluded.scheduled,
                model_version=excluded.model_version,
                qqq_pct=excluded.qqq_pct,
                smh_pct=excluded.smh_pct,
                market_long_power=excluded.market_long_power,
                market_short_power=excluded.market_short_power,
                universe_count=excluded.universe_count,
                report_text=excluded.report_text,
                extra_json=excluded.extra_json
            """,(
                market,trade_date,label,generated_at,1 if report.get('scheduled') else 0,
                str(report.get('model_version') or 'V2.0'),
                report.get('qqq_pct'),report.get('smh_pct'),
                report.get('market_long_power'),report.get('market_short_power'),
                report.get('universe_count'),report.get('report_text'),
                json.dumps(report.get('extra') or {},ensure_ascii=False,default=str)
            ))
            rid=c.execute(
                "SELECT id FROM preopen_report_meta WHERE market=? AND trade_date=? AND label=?",
                (market,trade_date,label)
            ).fetchone()[0]
            c.execute("DELETE FROM preopen_report_rows WHERE report_id=?",(rid,))
            for row in report.get('rows') or []:
                known={'symbol','current_rank','shadow_rank','current_score','shadow_score',
                       'price','change_pct','ma5','ma5_slope_pct','rvol','atr_pct',
                       'long_power','short_power','recommendation','rationale'}
                extra={k:v for k,v in row.items() if k not in known}
                c.execute("""
                  INSERT INTO preopen_report_rows(
                    report_id,symbol,current_rank,shadow_rank,current_score,shadow_score,
                    price,change_pct,ma5,ma5_slope_pct,rvol,atr_pct,
                    long_power,short_power,recommendation,rationale,extra_json
                  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,(
                    rid,str(row.get('symbol') or ''),
                    row.get('current_rank'),row.get('shadow_rank'),
                    row.get('current_score'),row.get('shadow_score'),
                    row.get('price'),row.get('change_pct'),row.get('ma5'),
                    row.get('ma5_slope_pct'),row.get('rvol'),row.get('atr_pct'),
                    row.get('long_power'),row.get('short_power'),
                    row.get('recommendation'),row.get('rationale'),
                    json.dumps(extra,ensure_ascii=False,default=str)
                ))
            c.commit()
        return rid

    def latest(self, market:str='USA'):
        with self.con() as c:
            meta=c.execute("""
              SELECT * FROM preopen_report_meta
              WHERE market=? ORDER BY trade_date DESC,generated_at DESC LIMIT 1
            """,(market.upper(),)).fetchone()
            if not meta: return None
            rows=c.execute("""
              SELECT * FROM preopen_report_rows WHERE report_id=?
              ORDER BY COALESCE(current_rank,999),COALESCE(shadow_rank,999),symbol
            """,(meta['id'],)).fetchall()
        return self._decode(meta,rows)

    def get(self, report_id:int):
        with self.con() as c:
            meta=c.execute("SELECT * FROM preopen_report_meta WHERE id=?",(report_id,)).fetchone()
            if not meta: return None
            rows=c.execute("""
              SELECT * FROM preopen_report_rows WHERE report_id=?
              ORDER BY COALESCE(current_rank,999),COALESCE(shadow_rank,999),symbol
            """,(report_id,)).fetchall()
        return self._decode(meta,rows)

    def history(self, market:str='USA', limit:int=60):
        with self.con() as c:
            rows=c.execute("""
              SELECT id,market,trade_date,label,generated_at,scheduled,model_version,
                     qqq_pct,smh_pct,market_long_power,market_short_power,
                     universe_count
              FROM preopen_report_meta WHERE market=?
              ORDER BY trade_date DESC,generated_at DESC LIMIT ?
            """,(market.upper(),limit)).fetchall()
            return [dict(x) for x in rows]

    def _decode(self,meta,rows):
        m=dict(meta)
        try: m['extra']=json.loads(m.pop('extra_json') or '{}')
        except Exception: m['extra']={}
        out=[]
        for rr in rows:
            d=dict(rr)
            try: d['extra']=json.loads(d.pop('extra_json') or '{}')
            except Exception: d['extra']={}
            out.append(d)
        return {'meta':m,'rows':out}


def _power(row:dict, qqq_pct:float, smh_pct:float):
    """Pre-open directional power v1; transparent heuristic, not production Trading Score."""
    score=(_f(row.get('current_score'))+_f(row.get('shadow_score')))/2
    day=_f(row.get('change_pct'))
    slope=_f(row.get('ma5_slope_pct'))
    rvol=_f(row.get('rvol'))
    atr=_f(row.get('atr_pct'))
    sym=str(row.get('symbol') or '').upper()
    sector=smh_pct if sym in {
        'NVDA','AMD','AVGO','MU','ARM','TSM','ASML','INTC','QCOM','SMH','SOXL','SOXS'
    } else qqq_pct

    raw=50.0
    raw += (score-50.0)*0.28
    raw += max(-12,min(12,day*2.0))
    raw += max(-8,min(8,slope*1.5))
    raw += max(-5,min(7,(rvol-1.0)*3.0))
    raw += max(-6,min(6,sector*2.0))
    if atr>12: raw-=4
    long_power=max(1,min(99,round(raw,1)))
    short_power=round(100-long_power,1)
    return long_power,short_power

def _rec(long_power:float, change_pct:float):
    if change_pct >= 12:
        return 'CHASE_RISK'
    if long_power >= 78:
        return 'STRONG_LONG'
    if long_power >= 65:
        return 'LONG'
    if long_power <= 28:
        return 'STRONG_SHORT'
    if long_power <= 40:
        return 'SHORT'
    return 'WATCH'

def build_usa_preopen_report(current:list[dict], shadow:list[dict], quotes:list[dict],
                             universe_count:int, scheduled:bool, label:str='PREOPEN_30'):
    now_utc=datetime.now(timezone.utc)
    et=now_utc.astimezone(ZoneInfo('America/New_York'))
    qmap={str(q.get('symbol') or '').upper():q for q in quotes}
    qqq=_f((qmap.get('QQQ') or {}).get('change_pct'))
    smh=_f((qmap.get('SMH') or {}).get('change_pct'))

    cr={r.get('symbol'):i for i,r in enumerate(current,1)}
    sr={r.get('symbol'):i for i,r in enumerate(shadow,1)}
    cm={r.get('symbol'):r for r in current}
    sm={r.get('symbol'):r for r in shadow}
    syms=[]
    for r in current+shadow:
        s=r.get('symbol')
        if s and s not in syms: syms.append(s)

    rows=[]
    for sym in syms:
        c=cm.get(sym) or {}
        sh=sm.get(sym) or {}
        base=sh or c
        row={
            'symbol':sym,
            'current_rank':cr.get(sym),
            'shadow_rank':sr.get(sym),
            'current_score':c.get('score'),
            'shadow_score':sh.get('score'),
            'price':base.get('price'),
            'change_pct':base.get('change_pct'),
            'ma5':base.get('ma5'),
            'ma5_slope_pct':base.get('ma5_slope_pct'),
            'rvol':base.get('rvol'),
            'atr_pct':base.get('atr_pct'),
            'current_parts':c.get('parts'),
            'shadow_parts':sh.get('parts'),
            'penalties':base.get('penalties') or [],
        }
        lp,sp=_power(row,qqq,smh)
        row['long_power']=lp; row['short_power']=sp
        row['recommendation']=_rec(lp,_f(row.get('change_pct')))
        reasons=[]
        day=_f(row.get('change_pct')); slope=_f(row.get('ma5_slope_pct')); rv=_f(row.get('rvol'))
        if day>=2: reasons.append(f'장전/현재 모멘텀 {day:+.1f}%')
        elif day<=-2: reasons.append(f'장전/현재 약세 {day:+.1f}%')
        if slope>=1: reasons.append(f'MA5 기울기 +{slope:.1f}%')
        elif slope<0: reasons.append(f'MA5 기울기 {slope:.1f}%')
        if rv>=1.5: reasons.append(f'RVOL {rv:.1f}x')
        if cr.get(sym) and sr.get(sym) and sr[sym]<cr[sym]: reasons.append(f'SHADOW {cr[sym]}→{sr[sym]}위 상향')
        if row['penalties']: reasons.append('주의: '+', '.join(row['penalties'][:2]))
        row['rationale']=' · '.join(reasons[:4]) or '기존 기술/시장 점수 기반 관찰'
        rows.append(row)

    rows.sort(key=lambda x:(x['long_power'], -(x['current_rank'] or 999)), reverse=True)
    market_raw=50 + qqq*8 + smh*4
    market_long=max(1,min(99,round(market_raw,1)))
    market_short=round(100-market_long,1)

    top=rows[:5]
    text_lines=[
        f"🇺🇸 USA PRE-OPEN INTELLIGENCE · {et.strftime('%Y-%m-%d %H:%M ET')}",
        f"Market LONG {market_long:.0f} : SHORT {market_short:.0f} · QQQ {qqq:+.2f}% · SMH {smh:+.2f}%",
        "",
        "TOP 5"
    ]
    for i,r in enumerate(top,1):
        text_lines.append(
            f"{i}. {r['symbol']} · {r['recommendation']} · LONG {r['long_power']:.0f} / SHORT {r['short_power']:.0f} "
            f"· {(r.get('change_pct') or 0):+.2f}% · {r['rationale']}"
        )
    text_lines += [
        "",
        "※ V2.0은 저장/스케줄/기술·시장 Intelligence 기반입니다.",
        "※ 뉴스 Catalyst와 외부 AI 뉴스판단은 V2.1에서 연결 예정입니다.",
        "※ CURRENT 운영 Trading Score와 자동주문 로직은 변경하지 않습니다."
    ]

    return {
        'market':'USA',
        'trade_date':et.strftime('%Y-%m-%d'),
        'label':label,
        'generated_at':now_utc.isoformat(),
        'scheduled':scheduled,
        'model_version':'V2.0_PREOPEN_INTEL_1',
        'qqq_pct':qqq,
        'smh_pct':smh,
        'market_long_power':market_long,
        'market_short_power':market_short,
        'universe_count':universe_count,
        'rows':rows,
        'report_text':'\n'.join(text_lines),
        'extra':{
            'timezone':'America/New_York',
            'target_time':'09:00 ET (regular open -30m)',
            'news_catalyst':'PENDING_V2_1',
            'korea_engine':'PENDING_KOREA_MARKET_ADAPTER'
        }
    }
