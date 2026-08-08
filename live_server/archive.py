
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

class RankingArchive:
    """Persistent daily ranking archive stored in the same SQLite DB file."""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init()

    def con(self):
        c=sqlite3.connect(self.db_path,timeout=20)
        c.row_factory=sqlite3.Row
        return c

    def _init(self):
        with self.con() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS ranking_archive_meta(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                label TEXT NOT NULL,
                model TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                qqq_pct REAL,
                smh_pct REAL,
                UNIQUE(trade_date,label,model)
            )""")
            c.execute("""
            CREATE TABLE IF NOT EXISTS ranking_archive_rows(
                meta_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                score REAL,
                bias TEXT,
                price REAL,
                change_pct REAL,
                ma5 REAL,
                ma5_slope_pct REAL,
                rvol REAL,
                atr_pct REAL,
                dollar_volume REAL,
                exchange TEXT,
                extra_json TEXT,
                PRIMARY KEY(meta_id,rank),
                FOREIGN KEY(meta_id) REFERENCES ranking_archive_meta(id) ON DELETE CASCADE
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ranking_archive_date ON ranking_archive_meta(trade_date DESC)")
            c.commit()

    def save(self, trade_date:str, label:str, model:str, rows:list[dict],
             captured_at:str|None=None, qqq_pct:float|None=None, smh_pct:float|None=None):
        captured_at=captured_at or datetime.now(timezone.utc).isoformat()
        model=model.upper(); label=label.upper()
        with self.con() as c:
            c.execute("""
                INSERT INTO ranking_archive_meta(trade_date,label,model,captured_at,row_count,qqq_pct,smh_pct)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(trade_date,label,model) DO UPDATE SET
                  captured_at=excluded.captured_at,row_count=excluded.row_count,
                  qqq_pct=excluded.qqq_pct,smh_pct=excluded.smh_pct
            """,(trade_date,label,model,captured_at,len(rows),qqq_pct,smh_pct))
            meta_id=c.execute(
                "SELECT id FROM ranking_archive_meta WHERE trade_date=? AND label=? AND model=?",
                (trade_date,label,model)
            ).fetchone()[0]
            c.execute("DELETE FROM ranking_archive_rows WHERE meta_id=?",(meta_id,))
            for i,row in enumerate(rows,1):
                known={'symbol','score','bias','price','change_pct','ma5','ma5_slope_pct',
                       'rvol','atr_pct','dollar_volume','exchange'}
                extra={k:v for k,v in row.items() if k not in known}
                c.execute("""
                    INSERT INTO ranking_archive_rows(
                      meta_id,rank,symbol,score,bias,price,change_pct,ma5,ma5_slope_pct,
                      rvol,atr_pct,dollar_volume,exchange,extra_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,(
                    meta_id,i,str(row.get('symbol') or ''),
                    row.get('score'),row.get('bias'),row.get('price'),row.get('change_pct'),
                    row.get('ma5'),row.get('ma5_slope_pct'),row.get('rvol'),row.get('atr_pct'),
                    row.get('dollar_volume'),row.get('exchange'),
                    json.dumps(extra,ensure_ascii=False,default=str)
                ))
            c.commit()
        return meta_id

    def dates(self, limit:int=120):
        with self.con() as c:
            rows=c.execute("""
              SELECT trade_date,
                     COUNT(*) snapshots,
                     MIN(captured_at) first_capture,
                     MAX(captured_at) last_capture
              FROM ranking_archive_meta
              GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?
            """,(limit,)).fetchall()
            return [dict(x) for x in rows]

    def snapshots(self, trade_date:str):
        with self.con() as c:
            rows=c.execute("""
              SELECT id,trade_date,label,model,captured_at,row_count,qqq_pct,smh_pct
              FROM ranking_archive_meta WHERE trade_date=?
              ORDER BY captured_at,label,model
            """,(trade_date,)).fetchall()
            return [dict(x) for x in rows]

    def ranking(self, trade_date:str, label:str, model:str='CURRENT'):
        with self.con() as c:
            meta=c.execute("""
              SELECT * FROM ranking_archive_meta
              WHERE trade_date=? AND label=? AND model=?
            """,(trade_date,label.upper(),model.upper())).fetchone()
            if not meta:
                return None
            rows=c.execute("""
              SELECT rank,symbol,score,bias,price,change_pct,ma5,ma5_slope_pct,
                     rvol,atr_pct,dollar_volume,exchange,extra_json
              FROM ranking_archive_rows WHERE meta_id=? ORDER BY rank
            """,(meta['id'],)).fetchall()
            out=[]
            for r in rows:
                d=dict(r)
                try: extra=json.loads(d.pop('extra_json') or '{}')
                except Exception: extra={}
                d.update(extra); out.append(d)
            return {'meta':dict(meta),'rows':out}

    def recent(self, limit:int=50):
        with self.con() as c:
            rows=c.execute("""
              SELECT trade_date,label,model,captured_at,row_count,qqq_pct,smh_pct
              FROM ranking_archive_meta ORDER BY trade_date DESC,captured_at DESC LIMIT ?
            """,(limit,)).fetchall()
            return [dict(x) for x in rows]
