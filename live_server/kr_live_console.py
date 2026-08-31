from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.responses import HTMLResponse

from live_server.kiwoom_mock_broker import KiwoomMockBroker
from live_server.engine5_v22_live_kr import ENGINE_NAME

KST = ZoneInfo('Asia/Seoul')


def _num(v):
    try:
        s=str(v or '').replace(',','').replace('+','').strip()
        if not s:
            return 0.0
        return float(s)
    except Exception:
        return 0.0


def _pick(d,*keys):
    for k in keys:
        if k in d and d.get(k) not in (None,''):
            return d.get(k)
    return None


def _retry_account(b, api_id, body, attempts=3):
    last=None
    for i in range(attempts):
        try:
            return b.request_account(api_id, body)
        except Exception as e:
            last=e
            if i+1<attempts:
                time.sleep(.6*(i+1))
    raise last


def _holdings(balance):
    raw=balance.get('stk_acnt_evlt_prst') or balance.get('acnt_evlt_prst') or []
    out=[]
    for x in raw:
        if not isinstance(x,dict):
            continue
        qty=int(_num(_pick(x,'rmnd_qty','hldg_qty','hold_qty','qty')))
        if qty<=0:
            continue
        code=str(_pick(x,'stk_cd','stk_no','code') or '').replace('A','').zfill(6)
        avg=abs(_num(_pick(x,'avg_prc','avg_buy_prc','pur_prc')))
        cur=abs(_num(_pick(x,'cur_prc','now_prc','prpr')))
        value=abs(_num(_pick(x,'evlt_amt','evlt_prst','cur_amt')))
        if value<=0 and cur>0:
            value=cur*qty
        pnl=_num(_pick(x,'evlt_pl','pl_amt','profit_loss'))
        rate=_num(_pick(x,'pl_rt','prft_rt','profit_rate'))
        out.append({
            'symbol':code,
            'name':str(_pick(x,'stk_nm','name') or code),
            'qty':qty,'avg_price':avg,'current_price':cur,
            'value':value,'pnl':pnl,'pnl_rate':rate,
        })
    return out


def _events(db_path,limit=40):
    try:
        con=sqlite3.connect(str(db_path),timeout=3)
        con.row_factory=sqlite3.Row
        rows=con.execute("""
            SELECT ts,symbol,event_type,state_from,state_to,power,message,payload_json
            FROM v4_signal_events
            WHERE market='KOREA'
            ORDER BY id DESC LIMIT ?
        """,(int(limit),)).fetchall()
        con.close()
        out=[]
        for r in rows:
            d=dict(r)
            try:d['payload']=json.loads(d.pop('payload_json') or '{}')
            except Exception:d['payload']={}
            out.append(d)
        return out
    except Exception as e:
        return [{'ts':None,'symbol':'','event_type':'DB_ERROR','state_to':'ERROR','message':str(e)}]


def _fills(b):
    try:
        d=_retry_account(b,'ka10076',{'qry_tp':'0','sell_tp':'0','stex_tp':'1'},2)
        raw=d.get('cntr') or d.get('cntr_infr') or []
        out=[]
        for x in raw[:30]:
            if not isinstance(x,dict):continue
            out.append({
                'time':str(_pick(x,'ord_tm','cntr_tm','time') or ''),
                'symbol':str(_pick(x,'stk_cd','code') or '').replace('A','').zfill(6),
                'name':str(_pick(x,'stk_nm','name') or ''),
                'side':str(_pick(x,'io_tp_nm','ord_tp_nm','side') or ''),
                'qty':int(_num(_pick(x,'cntr_qty','ord_qty','qty'))),
                'price':abs(_num(_pick(x,'cntr_prc','ord_prc','price'))),
                'order_no':str(_pick(x,'ord_no','order_no') or ''),
            })
        return out
    except Exception as e:
        return [{'time':'','symbol':'','name':'','side':'조회오류','qty':0,'price':0,'order_no':'','error':str(e)}]


def build_state(db_path):
    now=datetime.now(timezone.utc).astimezone(KST)
    minute=now.hour*60+now.minute
    regular=now.weekday()<5 and 540<=minute<930
    auto=(os.getenv('WILLIAMS_KIWOOM_MOCK_AUTO') or os.getenv('KIWOOM_MOCK_AUTO_ENABLED') or '0').lower() in ('1','true','yes','on')
    runtime_mode=str(os.getenv('DAY_TRADER_RUNTIME_MODE','DAYTRADE') or 'DAYTRADE').upper()
    result={
        'ok':False,'updated_at':now.isoformat(),'market_open':regular,
        'engine':ENGINE_NAME,'runtime_mode':runtime_mode,'execution_switch':auto,
        'broker':'KIWOOM_KR_MOCK','account':None,'cash':0,'total_assets':0,
        'stock_value':0,'holdings':[],'fills':[],'events':_events(db_path),
        'status':'DATA_ERROR','error':None,
    }
    try:
        b=KiwoomMockBroker()
        acct=b.validate_account()
        bal=_retry_account(b,'kt00004',{'qry_tp':'0','dmst_stex_tp':'KRX'},3)
        holdings=_holdings(bal)
        cash=_num(_pick(bal,'entr','dnca_tot_amt','deposit','cash'))
        total=_num(_pick(bal,'tot_evlt_amt','tot_est_amt','estimated_assets','tot_assets'))
        stock=sum(float(x['value']) for x in holdings)
        if total<=0:total=cash+stock
        result.update({
            'ok':True,'account':acct,'cash':cash,'total_assets':total,'stock_value':stock,
            'holdings':holdings,'fills':_fills(b),'order_enabled':bool(b.cfg.order_enable),
            'endpoint':b.cfg.rest_base,
        })
        armed=bool(b.cfg.order_enable and auto and runtime_mode=='DAYTRADE')
        if armed and regular:result['status']='RUNNING'
        elif armed:result['status']='ARMED_MARKET_CLOSED'
        else:result['status']='STOPPED'
    except Exception as e:
        result['error']=f'{type(e).__name__}: {e}'
    return result


HTML='''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>국장 가동 콘솔</title><style>
:root{color-scheme:dark;--bg:#07111f;--card:#0d1b2a;--line:#21364a;--muted:#91a4b7;--good:#37d67a;--bad:#ff6673;--warn:#f5c451;--txt:#edf4fb}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#06101d,#0a1420);color:var(--txt);font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif}.wrap{max-width:1440px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:18px}.title{font-size:27px;font-weight:800}.sub{color:var(--muted);font-size:13px}.badge{padding:8px 12px;border-radius:999px;font-weight:800;background:#1a2a39}.running{color:var(--good)}.stopped{color:var(--bad)}.armed{color:var(--warn)}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{background:rgba(13,27,42,.94);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 10px 30px #0004}.label{color:var(--muted);font-size:12px}.value{font-size:24px;font-weight:800;margin-top:7px}.engine{font-size:17px;word-break:break-all}.section{margin-top:14px}.section h2{font-size:16px;margin:0 0 10px}.tablewrap{overflow:auto;border-radius:12px;border:1px solid var(--line)}table{width:100%;border-collapse:collapse;background:#091522}th,td{padding:10px 12px;text-align:right;border-bottom:1px solid #15283a;white-space:nowrap;font-size:13px}th{color:#9fb2c4;background:#0d1b2a;position:sticky;top:0}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}.pos{color:var(--good)}.neg{color:var(--bad)}.muted{color:var(--muted)}.error{padding:12px;border:1px solid #7b2c36;background:#2b1116;border-radius:10px;color:#ffafb7;margin-bottom:12px}.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:900px){.grid{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}.wrap{padding:12px}.title{font-size:22px}}
</style></head><body><div class="wrap"><div class="top"><div><div class="title">🇰🇷 국장 가동 콘솔</div><div class="sub">Kiwoom 모의투자 · 5초 자동 갱신</div></div><div id="status" class="badge">LOADING</div></div><div id="error"></div>
<div class="grid"><div class="card"><div class="label">보유잔고 / 총자산</div><div id="assets" class="value">-</div><div id="cash" class="sub"></div></div><div class="card"><div class="label">보유주식 평가액</div><div id="stock" class="value">-</div><div id="holdCount" class="sub"></div></div><div class="card"><div class="label">가동 엔진</div><div id="engine" class="value engine">-</div><div id="mode" class="sub"></div></div><div class="card"><div class="label">계좌 / 주문상태</div><div id="account" class="value engine">-</div><div id="order" class="sub"></div></div></div>
<div class="section"><h2>보유주식</h2><div class="tablewrap"><table><thead><tr><th>종목</th><th>종목명</th><th>수량</th><th>평균단가</th><th>현재가</th><th>평가액</th><th>평가손익</th><th>수익률</th></tr></thead><tbody id="holdings"></tbody></table></div></div>
<div class="two"><div class="section"><h2>최근 체결 / 주문</h2><div class="tablewrap"><table><thead><tr><th>시간</th><th>종목</th><th>구분</th><th>수량</th><th>가격</th><th>주문번호</th></tr></thead><tbody id="fills"></tbody></table></div></div><div class="section"><h2>엔진 / 매매 이벤트</h2><div class="tablewrap"><table><thead><tr><th>시간</th><th>종목</th><th>이벤트</th><th>상태</th><th>메시지</th></tr></thead><tbody id="events"></tbody></table></div></div></div><div id="updated" class="sub" style="margin-top:12px"></div></div>
<script>
const won=n=>Math.round(Number(n||0)).toLocaleString('ko-KR')+'원';const num=n=>Number(n||0).toLocaleString('ko-KR');const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function pnlClass(n){return Number(n)>0?'pos':Number(n)<0?'neg':''}
async function refresh(){try{const r=await fetch('/api/korea-live/state',{cache:'no-store'});const d=await r.json();document.getElementById('error').innerHTML=d.error?`<div class="error">${esc(d.error)}</div>`:'';let sc=d.status==='RUNNING'?'running':d.status==='STOPPED'?'stopped':'armed';document.getElementById('status').className='badge '+sc;document.getElementById('status').textContent=d.status;document.getElementById('assets').textContent=won(d.total_assets);document.getElementById('cash').textContent='현금 '+won(d.cash);document.getElementById('stock').textContent=won(d.stock_value);document.getElementById('holdCount').textContent=`${(d.holdings||[]).length} 종목`;document.getElementById('engine').textContent=d.engine||'-';document.getElementById('mode').textContent=`runtime ${d.runtime_mode} · market ${d.market_open?'OPEN':'CLOSED'} · switch ${d.execution_switch?'ON':'OFF'}`;document.getElementById('account').textContent=d.account||'-';document.getElementById('order').textContent=`주문 ${d.order_enabled?'ENABLED':'DISABLED'} · KIWOOM MOCK`;
const h=(d.holdings||[]);document.getElementById('holdings').innerHTML=h.length?h.map(x=>`<tr><td>${esc(x.symbol)}</td><td>${esc(x.name)}</td><td>${num(x.qty)}</td><td>${won(x.avg_price)}</td><td>${won(x.current_price)}</td><td>${won(x.value)}</td><td class="${pnlClass(x.pnl)}">${won(x.pnl)}</td><td class="${pnlClass(x.pnl_rate)}">${Number(x.pnl_rate||0).toFixed(2)}%</td></tr>`).join(''):'<tr><td colspan="8" class="muted" style="text-align:center">보유주식 없음</td></tr>';
const f=(d.fills||[]);document.getElementById('fills').innerHTML=f.length?f.map(x=>`<tr><td>${esc(x.time)}</td><td>${esc(x.symbol)} ${esc(x.name)}</td><td>${esc(x.side)}</td><td>${num(x.qty)}</td><td>${won(x.price)}</td><td>${esc(x.order_no)}</td></tr>`).join(''):'<tr><td colspan="6" class="muted">체결내역 없음</td></tr>';
const e=(d.events||[]);document.getElementById('events').innerHTML=e.length?e.map(x=>`<tr><td>${esc(x.ts||'')}</td><td>${esc(x.symbol||'')}</td><td>${esc(x.event_type||'')}</td><td>${esc(x.state_to||'')}</td><td style="text-align:left;max-width:360px;overflow:hidden;text-overflow:ellipsis">${esc(x.message||'')}</td></tr>`).join(''):'<tr><td colspan="5" class="muted">이벤트 없음</td></tr>';document.getElementById('updated').textContent='최종 갱신 '+d.updated_at;}catch(e){document.getElementById('error').innerHTML=`<div class="error">화면 갱신 실패: ${esc(e)}</div>`}}refresh();setInterval(refresh,5000);
</script></body></html>'''


def register_kr_live_console(app, db_path):
    @app.get('/korea-live', response_class=HTMLResponse)
    def korea_live_page():
        return HTMLResponse(HTML)

    @app.get('/api/korea-live/state')
    def korea_live_state():
        return build_state(db_path)
