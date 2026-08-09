
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


def _power(row:dict, qqq_context:float, smh_context:float, data_mode:str):
    """
    PREOPEN Intelligence V2.
    CURRENT/SHADOW are base evidence.
    Intraday/premarket momentum is added ONLY when timestamp-verified PREMARKET_LIVE data exists.
    """
    score=(_f(row.get('current_score'))+_f(row.get('shadow_score')))/2
    slope=_f(row.get('ma5_slope_pct'))
    atr=_f(row.get('atr_pct'))
    sym=str(row.get('symbol') or '').upper()

    raw=50.0
    raw += (score-50.0)*0.25
    raw += max(-7,min(7,slope*1.2))

    # Market context also uses timestamp-verified premarket changes when available.
    sector=smh_context if sym in {
        'NVDA','AMD','AVGO','MU','ARM','TSM','ASML','INTC','QCOM','SMH','SOXL','SOXS'
    } else qqq_context
    raw += max(-6,min(6,sector*2.0))

    if data_mode=='PREMARKET_LIVE':
        pm=_f(row.get('premarket_change_pct'))
        vol_pct=_f(row.get('premarket_volume_pct_avg_daily'))
        raw += max(-14,min(14,pm*2.4))
        # This is NOT called RVOL. It is the fraction of normal full-day volume already traded premarket.
        if vol_pct >= 20: raw += 5
        elif vol_pct >= 10: raw += 3
        elif vol_pct >= 5: raw += 1

    if atr>12: raw-=4
    long_power=max(1,min(99,round(raw,1)))
    short_power=round(100-long_power,1)
    return long_power,short_power

def _rec(long_power:float, pm_change, data_mode:str):
    if data_mode=='PREMARKET_LIVE' and pm_change is not None and pm_change >= 12:
        return 'CHASE_RISK'
    if long_power >= 78: return 'STRONG_LONG'
    if long_power >= 65: return 'LONG'
    if long_power <= 28: return 'STRONG_SHORT'
    if long_power <= 40: return 'SHORT'
    return 'WATCH'

def build_usa_preopen_report(current:list[dict], shadow:list[dict], quotes:list[dict],
                             metrics:list[dict], probes:dict, universe_count:int,
                             scheduled:bool, label:str='PREOPEN_30', news_result:dict|None=None):
    now_utc=datetime.now(timezone.utc)
    et=now_utc.astimezone(ZoneInfo('America/New_York'))
    qmap={str(q.get('symbol') or '').upper():q for q in quotes}
    mmap={str(m.get('symbol') or '').upper():m for m in metrics}

    qqq_probe=probes.get('QQQ') or {}
    smh_probe=probes.get('SMH') or {}
    qqq_live=qqq_probe.get('premarket_change_pct') if qqq_probe.get('is_fresh_premarket') else None
    smh_live=smh_probe.get('premarket_change_pct') if smh_probe.get('is_fresh_premarket') else None
    qqq_last=_f((qmap.get('QQQ') or {}).get('change_pct'))
    smh_last=_f((qmap.get('SMH') or {}).get('change_pct'))
    qqq_context=_f(qqq_live) if qqq_live is not None else 0.0
    smh_context=_f(smh_live) if smh_live is not None else 0.0

    fresh_count=sum(1 for x in probes.values() if x.get('is_fresh_premarket'))
    market_mode='PREMARKET_LIVE' if (qqq_probe.get('is_fresh_premarket') or smh_probe.get('is_fresh_premarket')) else 'LAST_SESSION_REFERENCE'

    cr={r.get('symbol'):i for i,r in enumerate(current,1)}
    sr={r.get('symbol'):i for i,r in enumerate(shadow,1)}
    cm={r.get('symbol'):r for r in current}; sm={r.get('symbol'):r for r in shadow}
    syms=[]
    for r in current+shadow:
        s=r.get('symbol')
        if s and s not in syms: syms.append(s)

    rows=[]
    for sym in syms:
        c=cm.get(sym) or {}; sh=sm.get(sym) or {}; base=sh or c
        probe=probes.get(sym) or {}
        m=mmap.get(sym) or {}
        avg5vol=_f(m.get('avg5_volume'))
        pmvol=probe.get('premarket_volume')
        pmvol_pct=(float(pmvol)/avg5vol*100) if pmvol is not None and avg5vol>0 else None
        mode=probe.get('data_mode') or 'LAST_SESSION'
        row={
            'symbol':sym,'current_rank':cr.get(sym),'shadow_rank':sr.get(sym),
            'current_score':c.get('score'),'shadow_score':sh.get('score'),
            'price':base.get('price'),'last_session_change_pct':base.get('change_pct'),
            'ma5':base.get('ma5'),'ma5_slope_pct':base.get('ma5_slope_pct'),
            'atr_pct':base.get('atr_pct'),
            'data_mode':mode,'market_phase':probe.get('phase'),
            'latest_bar_at':probe.get('latest_bar_at'),
            'latest_bar_et':probe.get('latest_bar_et'),
            'latest_age_minutes':probe.get('latest_age_minutes'),
            'premarket_price':probe.get('premarket_price'),
            'premarket_change_pct':probe.get('premarket_change_pct'),
            'premarket_volume':pmvol,
            'premarket_volume_pct_avg_daily':round(pmvol_pct,2) if pmvol_pct is not None else None,
            'premarket_bar_count':probe.get('premarket_bar_count'),
            'current_parts':c.get('parts'),'shadow_parts':sh.get('parts'),
            'penalties':base.get('penalties') or [],
        }
        lp,sp=_power(row,qqq_context,smh_context,mode)
        row['long_power']=lp; row['short_power']=sp
        row['recommendation']=_rec(lp,row.get('premarket_change_pct'),mode)

        reasons=[]
        if mode=='PREMARKET_LIVE':
            pm=_f(row.get('premarket_change_pct'))
            reasons.append(f'실제 프리마켓 {pm:+.1f}%')
            if pmvol_pct is not None:
                reasons.append(f'프리마켓 거래량/5일평균 일거래량 {pmvol_pct:.1f}%')
        else:
            reasons.append('프리마켓 미확인: 당일 모멘텀 가중치 제외')
        slope=_f(row.get('ma5_slope_pct'))
        if slope>=1: reasons.append(f'MA5 기울기 +{slope:.1f}%')
        elif slope<0: reasons.append(f'MA5 기울기 {slope:.1f}%')
        if cr.get(sym) and sr.get(sym) and sr[sym]<cr[sym]:
            reasons.append(f'SHADOW {cr[sym]}→{sr[sym]}위 상향')
        row['rationale']=' · '.join(reasons[:4])
        news=((news_result or {}).get('items') or {}).get(sym)
        if news:
            row['catalyst_strength']=news.get('catalyst_strength')
            row['news_bias']=news.get('news_bias')
            row['news_long_power']=news.get('news_long_power')
            row['news_short_power']=news.get('news_short_power')
            row['ai_confidence']=news.get('ai_confidence')
            row['price_reaction']=news.get('price_reaction')
            row['news_summary_ko']=news.get('summary_ko')
            row['news_risk_ko']=news.get('risk_ko')
        rows.append(row)

    # Final AI combination is done after the transparent technical/pre-market score is frozen.
    try:
        from .news_ai import combine_technical_and_news
        for row in rows:
            n=((news_result or {}).get('items') or {}).get(row['symbol'])
            fin=combine_technical_and_news(row,n,row.get('data_mode')=='PREMARKET_LIVE')
            row.update(fin)
    except Exception:
        for row in rows:
            row['final_long_power']=row.get('long_power')
            row['final_short_power']=row.get('short_power')
            row['final_signal']=row.get('recommendation')
            row['news_weight']=0.0

    rows.sort(key=lambda x:(x.get('final_long_power',x['long_power']), -(x['current_rank'] or 999)), reverse=True)
    market_raw=50 + qqq_context*8 + smh_context*4
    market_long=max(1,min(99,round(market_raw,1)))
    market_short=round(100-market_long,1)

    top=rows[:5]
    data_as_of=max(
        [x.get('latest_bar_at') for x in probes.values() if x.get('latest_bar_at')] or [None]
    )
    text_lines=[
        f"🇺🇸 USA PRE-OPEN INTELLIGENCE · generated {et.strftime('%Y-%m-%d %H:%M ET')}",
        f"DATA MODE: {market_mode} · Market data as of: {data_as_of or 'N/A'}",
    ]
    if market_mode=='PREMARKET_LIVE':
        text_lines.append(
            f"Market LONG {market_long:.0f} : SHORT {market_short:.0f} · "
            f"QQQ PM {qqq_context:+.2f}% · SMH PM {smh_context:+.2f}%"
        )
    else:
        text_lines.append(
            f"Last-session reference only · QQQ last {qqq_last:+.2f}% · SMH last {smh_last:+.2f}% "
            f"· premarket momentum intentionally NOT scored"
        )
    text_lines += ["","TOP 5"]
    for i,r in enumerate(top,1):
        pm = r.get('premarket_change_pct')
        pm_txt=f"{pm:+.2f}%" if pm is not None else "N/A"
        final_sig=r.get('final_signal') or r['recommendation']
        final_long=float(r.get('final_long_power') or r['long_power'])
        final_short=float(r.get('final_short_power') or r['short_power'])
        catalyst=r.get('catalyst_strength') or 'N/A'
        news_txt=r.get('news_summary_ko') or '뉴스 AI 미사용'
        text_lines.append(
            f"{i}. {r['symbol']} · {final_sig} · FINAL LONG {final_long:.0f} / SHORT {final_short:.0f} "
            f"· PM {pm_txt} · Catalyst {catalyst} · {r['rationale']} · {news_txt}"
        )
    text_lines += [
        "",
        "※ PREMARKET_LIVE는 실제 1분봉의 ET 날짜/시각이 당일 프리마켓이고 최근 15분 이내일 때만 표시합니다.",
        "※ 그렇지 않으면 LAST_SESSION_REFERENCE로 표시하고 프리마켓 모멘텀/거래량 가중치를 0으로 둡니다.",
        "※ '프리마켓 거래량/5일평균 일거래량%'은 full-day 평균 대비 비율이며 RVOL이라고 부르지 않습니다.",
        "※ News Catalyst는 OPENAI_API_KEY가 설정된 경우 OpenAI Responses API web search로 생성됩니다.",
        "※ AI/news 결과와 source metadata는 해당 PREOPEN 스냅샷에 함께 저장됩니다.",
        "※ CURRENT Trading Score 및 자동주문 로직은 변경하지 않습니다."
    ]

    return {
        'market':'USA','trade_date':et.strftime('%Y-%m-%d'),'label':label,
        'generated_at':now_utc.isoformat(),'scheduled':scheduled,
        'model_version':'V2.1_NEWS_AI_CATALYST',
        'qqq_pct':qqq_context if market_mode=='PREMARKET_LIVE' else None,
        'smh_pct':smh_context if market_mode=='PREMARKET_LIVE' else None,
        'market_long_power':market_long,'market_short_power':market_short,
        'universe_count':universe_count,'rows':rows,'report_text':'\n'.join(text_lines),
        'extra':{
            'timezone':'America/New_York',
            'target_time':'09:00 ET (regular open -30m)',
            'market_data_mode':market_mode,
            'market_data_as_of':data_as_of,
            'fresh_premarket_probe_count':fresh_count,
            'qqq_last_session_pct':qqq_last,
            'smh_last_session_pct':smh_last,
            'news_catalyst_provider':(news_result or {}).get('provider'),
            'news_ai_enabled':bool((news_result or {}).get('enabled')),
            'news_ai_error':(news_result or {}).get('error'),
            'news_sources':(news_result or {}).get('sources') or [],
            'korea_engine':'PENDING_KOREA_MARKET_ADAPTER'
        }
    }
