# DAY TRADER WEB V1

온라인 접속형 단타 시그널 대시보드 프로토타입.

## 현재 기능
- NASDAQ 단타 후보 TOP10 데모 스크리닝
- 장 -10분 / -1분 / +7분 추천 유지도 화면
- 선택 종목 집중 감시
- VWAP / EMA / RVOL / RSI / Breakout 기반 Signal Score
- WAIT / WATCH / SETUP / TRIGGER
- 체결가 등록 후 HOLD / ADD / TRIM / EXIT Position Mode
- 카카오톡 '나와의 채팅' 테스트 모듈
- 모바일/PC 반응형 Streamlit UI

## 현재 제한
- 시세는 Demo 데이터입니다.
- 실시간 Kiwoom/KIS WebSocket adapter는 다음 단계입니다.
- 자동 주문 기능은 없습니다.

## 로컬 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Render 배포
1. 이 폴더를 GitHub 저장소에 업로드
2. Render에서 New > Blueprint 또는 Web Service
3. 저장소 연결
4. `render.yaml` 또는 `Dockerfile` 감지 후 Deploy
5. 생성된 `https://...onrender.com` 주소로 어디서든 접속

## 환경변수
카카오 알림을 사용할 때 호스팅 서비스의 Secrets/Environment에 설정:
- KAKAO_REST_API_KEY
- KAKAO_ACCESS_TOKEN

실시간 데이터 연결 단계에서 추가:
- KIWOOM_APP_KEY
- KIWOOM_SECRET_KEY
