# 배포 가이드 (Vercel 프론트 + Render 백엔드)

프론트(React/Vite)는 **Vercel**, 백엔드(FastAPI)는 **Render**에 올립니다.
앱 기능 코드는 수정하지 않았고, 연결용 설정 파일 2개만 추가돼 있습니다.

```
브라우저 ──▶ gg-parrot.vercel.app (프론트, Vercel)
                   │  /api/* 요청은 vercel.json 이 Render로 프록시
                   └──▶ gg-parrot.onrender.com (백엔드, Render)
```

## 1) 백엔드 → Render

1. https://render.com 로그인 → **New +** → **Blueprint**
2. GitHub 레포 `RHOHEEJAE/gg_parrot` 연결 → `render.yaml` 자동 인식 → **Apply**
   - (수동으로 하려면: **New Web Service** →
     Root Directory `backend`,
     Build `pip install -r requirements.txt`,
     Start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`)
3. 배포 완료 후 상단의 **서비스 URL 확인** (예: `https://gg-parrot.onrender.com`)
   - ⚠️ 이름이 이미 쓰였으면 `https://gg-parrot-xxxx.onrender.com` 처럼 다를 수 있음.
4. `/api/health` 열어 `{"ok": true}` 나오면 정상.

## 2) vercel.json 의 백엔드 URL 맞추기

`frontend/vercel.json` 의 `destination` 이 1)에서 받은 **실제 Render URL**과 같은지 확인.
다르면 그 한 줄만 고쳐서 다시 push:

```json
{ "source": "/api/:path*", "destination": "https://<실제-render-url>/api/:path*" }
```

## 3) 프론트 → Vercel

1. https://vercel.com 로그인 → **Add New… → Project** → 레포 `gg_parrot` import
2. **Root Directory** 를 `frontend` 로 지정 (Framework: Vite 자동 인식)
3. **Deploy** → 완료 후 URL 확인 (예: `https://gg-parrot.vercel.app`)

끝. 프론트의 `/api/*` 요청이 Render 백엔드로 프록시되어 백테스트·페이퍼·리더보드가 모두 동작합니다.

## 무료 티어 주의점
- **Render 잠자기:** 15분간 백엔드로 요청이 없으면 잠들고, 다음 접속 때 30~60초 후 깨어남.
- **SQLite 리셋:** Render 무료는 재배포/재시작 시 디스크가 초기화되어 `app.db`(매크로·리더보드)가 리셋됨.
  - 데이터 유지가 필요하면 무료 Postgres(Neon 등)로 옮기고 `backend/app/db.py` 의 연결 문자열만 교체.

## CORS
`backend/app/main.py` 가 이미 `allow_origins=["*"]` 라 별도 설정 불필요.
운영 시 보안을 위해 Vercel 도메인만 허용하도록 좁히는 것을 권장.

## 포지션 뉴스 중앙 워커

포지션 뉴스 수집은 FastAPI 요청과 분리된 Prefect worker가 담당합니다. 루트
`render.yaml`에 `gg-parrot-position-news` Background Worker가 포함되어 있습니다.
변경을 push한 뒤 Render Blueprint에서 **Sync Blueprint**를 실행하고, worker에
웹과 같은 `DATABASE_URL` 및 `PREFECT_API_URL`·`PREFECT_API_KEY`를 입력합니다.
Background Worker에는 무료 플랜과 HTTP health check가 없습니다. 구조, 로컬 실행,
Prefect Cloud 연결, 상세 검증 절차는
[backend/PREFECT_POSITION_NEWS.md](backend/PREFECT_POSITION_NEWS.md)를 참고하세요.

에이전트 API는 중앙 DB만 읽으므로, worker와 웹이 같은 Postgres를 사용하고 첫 뉴스 스냅샷이 생성되는지 확인해야 합니다.
