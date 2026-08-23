from pathlib import Path

API=Path('live_server/api.py')
MOD=Path('live_server/portfolio_v5.py')

MODULE='''from __future__ import annotations
import sqlite3
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


class PortfolioV5Store:
    def __init__(self,path:str):
        self.path=path
        self._init()

    def _c(self):
        c=sqlite3.connect(self.path,timeout=20)
        c.row_factory=sqlite3.Row
        return c

    def _init(self):
        with self._c() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS v5_portfolio_assets(
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                asset_class TEXT NOT NULL DEFAULT 'EQUITY',
                bucket TEXT NOT NULL DEFAULT 'LONG_TERM',
                quantity REAL NOT NULL DEFAULT 0,
                avg_price REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                manual_price REAL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(market,symbol)
            );
            CREATE TABLE IF NOT EXISTS v5_portfolio_daily_snapshots(
                snapshot_date TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                asset_class TEXT,
                bucket TEXT,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                current_price REAL NOT NULL,
                cost_basis REAL NOT NULL,
                market_value REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                unrealized_pnl_pct REAL,
                currency TEXT,
                captured_at TEXT NOT NULL,
                PRIMARY KEY(snapshot_date,market,symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_v5_portfolio_snapshots_date
              ON v5_portfolio_daily_snapshots(snapshot_date);
            """)

    def assets(self,active_only=True):
        sql='SELECT * FROM v5_portfolio_assets'
        args=[]
        if active_only:
            sql+=' WHERE active=1'
        sql+=' ORDER BY market,bucket,symbol'
        with self._c() as c:
            return [dict(r) for r in c.execute(sql,args).fetchall()]

    def upsert_asset(self,row:dict):
        market=str(row.get('market') or '').upper().strip()
        symbol=str(row.get('symbol') or '').upper().strip()
        if not market or not symbol:
            raise ValueError('market and symbol are required')
        qty=float(row.get('quantity') or 0)
        avg=float(row.get('avg_price') or 0)
        if qty<0 or avg<0:
            raise ValueError('quantity and avg_price must be >= 0')
        now=_now()
        currency=str(row.get('currency') or ('KRW' if market=='KOREA' else 'USD')).upper()
        payload={
            'market':market,'symbol':symbol,'name':row.get('name'),
            'asset_class':str(row.get('asset_class') or 'EQUITY').upper(),
            'bucket':str(row.get('bucket') or 'LONG_TERM').upper(),
            'quantity':qty,'avg_price':avg,'currency':currency,
            'manual_price':row.get('manual_price'),'active':1 if row.get('active',True) else 0,
            'created_at':now,'updated_at':now,
        }
        with self._c() as c:
            c.execute("""INSERT INTO v5_portfolio_assets
              (market,symbol,name,asset_class,bucket,quantity,avg_price,currency,manual_price,active,created_at,updated_at)
              VALUES(:market,:symbol,:name,:asset_class,:bucket,:quantity,:avg_price,:currency,:manual_price,:active,:created_at,:updated_at)
              ON CONFLICT(market,symbol) DO UPDATE SET
                name=excluded.name,asset_class=excluded.asset_class,bucket=excluded.bucket,
                quantity=excluded.quantity,avg_price=excluded.avg_price,currency=excluded.currency,
                manual_price=excluded.manual_price,active=excluded.active,updated_at=excluded.updated_at""",payload)
        return self.asset(market,symbol)

    def asset(self,market,symbol):
        with self._c() as c:
            r=c.execute('SELECT * FROM v5_portfolio_assets WHERE market=? AND symbol=?',
                        (str(market).upper(),str(symbol).upper())).fetchone()
            return dict(r) if r else None

    def deactivate(self,market,symbol):
        with self._c() as c:
            c.execute('UPDATE v5_portfolio_assets SET active=0,updated_at=? WHERE market=? AND symbol=?',
                      (_now(),str(market).upper(),str(symbol).upper()))
        return self.asset(market,symbol)

    def save_snapshot(self,snapshot_date:str,rows:list[dict]):
        captured=_now()
        with self._c() as c:
            for x in rows:
                qty=float(x.get('quantity') or 0)
                avg=float(x.get('avg_price') or 0)
                cur=float(x.get('current_price') or 0)
                cost=qty*avg
                value=qty*cur
                pnl=value-cost
                pct=(pnl/cost*100) if cost else None
                c.execute("""INSERT OR IGNORE INTO v5_portfolio_daily_snapshots
                  (snapshot_date,market,symbol,name,asset_class,bucket,quantity,avg_price,current_price,
                   cost_basis,market_value,unrealized_pnl,unrealized_pnl_pct,currency,captured_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (snapshot_date,str(x.get('market') or '').upper(),str(x.get('symbol') or '').upper(),
                   x.get('name'),x.get('asset_class'),x.get('bucket'),qty,avg,cur,cost,value,pnl,pct,
                   x.get('currency'),captured))
        return self.snapshot(snapshot_date)

    def snapshot(self,snapshot_date:str):
        with self._c() as c:
            return [dict(r) for r in c.execute(
                'SELECT * FROM v5_portfolio_daily_snapshots WHERE snapshot_date=? ORDER BY market,bucket,symbol',
                (snapshot_date,)).fetchall()]

    def history(self,limit=90):
        with self._c() as c:
            rows=c.execute("""SELECT snapshot_date,
                SUM(cost_basis) cost_basis,SUM(market_value) market_value,SUM(unrealized_pnl) unrealized_pnl
                FROM v5_portfolio_daily_snapshots
                GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT ?""",(int(limit),)).fetchall()
            return [dict(r) for r in rows]
'''

PATCH='''\n# ===== V38 V5 PORTFOLIO LEDGER + DAILY SNAPSHOT =====\nfrom .portfolio_v5 import PortfolioV5Store\nportfolio_v5=PortfolioV5Store(s.db_path)\n\n@app.get('/api/v5/portfolio/assets')\ndef v5_portfolio_assets(active_only:bool=True):\n    return {'ok':True,'rows':portfolio_v5.assets(active_only)}\n\n@app.post('/api/v5/portfolio/assets')\ndef v5_portfolio_asset_upsert(payload:dict):\n    try:\n        return {'ok':True,'asset':portfolio_v5.upsert_asset(payload)}\n    except Exception as e:\n        raise HTTPException(status_code=400,detail=str(e))\n\n@app.post('/api/v5/portfolio/assets/{market}/{symbol}/deactivate')\ndef v5_portfolio_asset_deactivate(market:str,symbol:str):\n    return {'ok':True,'asset':portfolio_v5.deactivate(market,symbol)}\n\n@app.post('/api/v5/portfolio/snapshot')\ndef v5_portfolio_snapshot(payload:dict):\n    snapshot_date=str(payload.get('snapshot_date') or datetime.now(timezone.utc).date().isoformat())\n    rows=[]\n    for a in portfolio_v5.assets(True):\n        cur=a.get('manual_price')\n        if str(a.get('asset_class') or '').upper()=='CASH':\n            cur=1.0\n        if cur in (None,'',0,0.0):\n            q=db.quote(a.get('symbol'))\n            cur=(q or {}).get('price')\n        if cur in (None,'',0,0.0):\n            continue\n        rows.append({**a,'current_price':float(cur)})\n    saved=portfolio_v5.save_snapshot(snapshot_date,rows)\n    return {'ok':True,'snapshot_date':snapshot_date,'count':len(saved),'rows':saved}\n\n@app.get('/api/v5/portfolio/snapshot/{snapshot_date}')\ndef v5_portfolio_snapshot_get(snapshot_date:str):\n    rows=portfolio_v5.snapshot(snapshot_date)\n    return {'ok':True,'snapshot_date':snapshot_date,'count':len(rows),'rows':rows}\n\n@app.get('/api/v5/portfolio/history')\ndef v5_portfolio_history(limit:int=90):\n    rows=portfolio_v5.history(max(1,min(int(limit),1000)))\n    return {'ok':True,'count':len(rows),'rows':rows}\n'''


def main():
    if not MOD.exists():
        MOD.write_text(MODULE)
    else:
        existing=MOD.read_text()
        if 'class PortfolioV5Store' not in existing:
            MOD.write_text(MODULE)

    s=API.read_text()
    if 'V38 V5 PORTFOLIO LEDGER + DAILY SNAPSHOT' not in s:
        anchor='@app.get("/health")'
        if anchor not in s:
            anchor="@app.get('/health')"
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: health route anchor')
        pos=s.index(anchor)
        s=s[:pos]+PATCH+'\n'+s[pos:]
        API.write_text(s)

    print('V38_PORTFOLIO_LEDGER_SNAPSHOT_BACKEND_OK')


if __name__=='__main__':
    main()
