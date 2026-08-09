
from __future__ import annotations
from datetime import datetime, timezone
import requests

def _num(v):
    try:
        return float(str(v).replace(',','').strip())
    except Exception:
        return 0.0

class KoreaMarketAdapter:
    """V2.5 phase-1 domestic market adapter.

    The first release deliberately uses only the request body verified from the
    official Kiwoom domestic quote guide (ka10004). Ranking request bodies are
    exposed as capabilities but are not guessed here.
    """
    def __init__(self, kiwoom_client):
        self.k = kiwoom_client

    def quote(self, stk_cd:str='005930'):
        code=str(stk_cd or '').strip()
        r=requests.post(
            self.k.s.rest_base+'/api/dostk/mrkcond',
            headers=self.k.headers('ka10004'),
            json={'stk_cd':code},
            timeout=20
        )
        d=r.json()
        if d.get('return_code') not in (None,0):
            raise RuntimeError(f"ka10004 {code}: {d.get('return_code')} {d.get('return_msg')}")
        # Preserve raw payload because domestic field names can differ by instrument/exchange.
        return {
            'ok':True,
            'api_id':'ka10004',
            'symbol':code,
            'checked_at':datetime.now(timezone.utc).isoformat(),
            'raw':d
        }

    def status(self):
        return {
            'ok':True,
            'phase':'KOREA_BASE',
            'market':'KOREA',
            'adapter_ready':True,
            'quote_probe_ready':True,
            'ranking_live':False,
            'score_live':False,
            'preopen_live':False,
            'planned_sources':[
                {'api_id':'ka10032','name':'거래대금상위','status':'NEXT'},
                {'api_id':'ka10030','name':'당일거래량상위','status':'NEXT'},
                {'api_id':'ka10023','name':'거래량급증','status':'NEXT'},
                {'api_id':'ka10027','name':'전일대비등락률상위','status':'NEXT'},
                {'api_id':'ka10029','name':'예상체결등락률상위','status':'PREOPEN_NEXT'},
                {'api_id':'ka10046','name':'체결강도추이시간별','status':'LATER_SCORE'},
                {'api_id':'ka10054','name':'VI 발동종목','status':'LATER_SCORE'},
                {'api_id':'1h','name':'VI발동/해제 실시간','status':'LATER_WS'},
            ],
            'notes':[
                'V2.5는 국내 REST 연결 틀과 UI를 먼저 고정합니다.',
                '순위 API 요청 body는 공식 스펙 확인 후 V2.5.1에서 연결합니다.',
                '미국장 CURRENT 점수/주문 로직은 변경하지 않습니다.',
                'NO AUTO ORDER 유지.'
            ]
        }
