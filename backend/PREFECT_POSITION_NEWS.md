# 포지션 뉴스 중앙 수집 워커

포지션 뉴스는 사용자나 매크로마다 뉴스·AI를 실행하지 않습니다. 활성 매크로가 참조하는 자산 티커를 한 번 수집·분석해 공용 DB에 저장하고, API 조회 시 등록된 롱/숏 방향만 결정론적으로 적용합니다.

```text
`app.news._COIN_KO` 기본 코인 + 실행 중인 세션 티커
                         │
                         ▼
              Prefect 5분 스케줄/재시도
                         │
 티커별 Google RSS + CoinDesk RSS + EDEN 공식 RSS
                         │
       DB pending claim (동일 fingerprint 중복 방지)
                         │
             신규 묶음만 공용 AI 분석 1회
                         │
        TickerNewsSnapshot + TickerNewsState
                         │
       ┌─────────────┬─────────────┐
       ▼             ▼             ▼
  공개 코인 뉴스   롱 매크로 API   숏 매크로 API
    DB 우선        긍정 → 유리     긍정 → 불리
```

Prefect는 오케스트레이션만 담당합니다. Prefect 캐시나 프로세스 메모리가 아니라 서비스 DB가 수집 결과와 중복 방지의 기준입니다.

## 현재 계약

- 수집 범위는 `app.news._COIN_KO`에 정의된 전체 기본 지원 코인을 항상 포함합니다.
- `RunSession.status=running`인 에이전트 세션의 티커도 자산 심볼로 정규화해 수집 범위에 합칩니다.
- 기본 지원 목록 밖의 티커는 실행 세션이 유지되는 동안 수집하며, 세션이 끝난 뒤에는 새로 갱신하지 않습니다.
- `BTCUSDT`, `BTCUSDC`, `BTCBUSD`는 모두 공용 자산 `BTC` 하나로 정규화됩니다.
- 처리 대상은 DB의 `last_attempt_ms`가 오래된 티커부터 정렬해 deadline이 반복돼도 뒤쪽 티커가 굶지 않게 합니다.
- 수집은 기본 5분 주기이며, 기사 묶음 fingerprint가 바뀐 경우에만 분석을 시도합니다.
- Google News RSS는 기본적으로 티커별 한국어 검색 결과를 가져옵니다. EDEN은 한국어·영문 브랜드·영문 티커 검색을 각각 수행하고 쿼리당 최대 50개 후보를 검사합니다. CoinDesk 공식 RSS 25건은 cycle 안에서 한 번만 받아 모든 티커가 공유합니다.
- 두 RSS 모두 제목과 feed category를 자산 별칭으로 검사해 관련 헤드라인만 저장합니다. CoinDesk 항목은 원문 URL을 그대로 사용합니다.
- 한 cycle의 AI 시도는 최대 12회이고, 모든 worker가 공유하는 KST 일일 DB 예산은 최대 120회입니다. 초과 티커는 규칙 기반 분석으로 저장됩니다.
- RSS 오류·빈 응답·AI 재분석 실패는 마지막 사용 가능 스냅샷을 지우지 않습니다.
- 에이전트 사용자 API는 RSS 또는 AI를 호출하지 않고 최신 완료 스냅샷만 읽습니다.
- 공개 `/api/news/coin/{symbol}`도 기본 지원 코인은 같은 최신 완료 스냅샷을 우선 읽습니다. 스냅샷이 아직 없거나 DB 조회가 일시적으로 실패한 경우와 기본 지원 목록 밖의 티커에만 기존 프로세스 RSS 캐시를 fallback으로 사용합니다.
- API 응답은 마지막 성공 후 15분이 지나면 `collection.freshness=stale`로 표시합니다.
- AI 입력에는 헤드라인·매체·자산명만 들어갑니다. 사용자, 매크로, 포지션 방향은 전달하지 않습니다.

## 뉴스 소스

- Google News RSS: `https://news.google.com/rss/search`, 기본 티커는 `when:7d` 검색
- EDEN Google RSS: 최근 30일 동안 `OpenEden`, `Open Eden`, `EDEN coin`, `EDEN crypto`, `EDEN token`, `$EDEN`, `EDEN USDT`, `EDEN listing`, `EDEN price`를 한국어·영문 locale에서 검색합니다. 일반 단어 `Eden`의 지명·인명·기업 동음이의어는 제목 문맥 필터로 제거합니다.
- OpenEden 공식 RSS: `https://openeden.com/news/feed/`, 최근 30일의 공식 헤드라인을 1시간 캐시로 수집합니다.
- CoinDesk 공식 RSS: `https://www.coindesk.com/arc/outboundfeeds/rss/`, 최신 25건
- CoinDesk RSS의 `Markets`, `Policy`, `Tech`, `Finance` category와 `Bitcoin News`, `Ethereum News` 같은 태그 메타데이터를 티커 관련성 판정에 사용합니다.
- CoinDesk 섹션·태그 HTML 페이지는 자동 수집하지 않습니다. 실행 환경에서 Vercel 보안 체크포인트가 HTTP 429를 반환하고, CoinDesk 이용약관도 허가 없는 robot/scraper 접근을 금지합니다.
- 기사 본문·RSS description/content는 저장하지 않고 제목·매체·원문 링크·발행 시각만 사용합니다.

공용 결과를 소비하는 인증 API는 실행 세션용
`GET /api/me/agents/sessions/{session_id}/position-news`와 저장 매크로용
`GET /api/me/agents/macros/{macro_id}/position-news?symbol=ETHUSDT` 두 경로입니다.
포트폴리오 매크로의 `symbol`은 반드시 그 매크로에 포함된 티커여야 합니다.

공개 뉴스 화면은 `GET /api/news/coin/{symbol}`을 사용합니다. 응답의
`data_source=prefect_db`이면 중앙 스냅샷, `data_source=rss_cache`이면 fallback
결과입니다. 브라우저는 Supabase에 직접 연결하지 않으며 DB 비밀값은 계속
FastAPI와 Prefect worker에만 둡니다.

## 로컬 실행

웹 서버 의존성과 Prefect 워커 의존성을 분리했습니다.

```bash
cd backend
python -m pip install -r requirements-prefect.txt
```

먼저 `.env`에 웹 서버와 같은 `DATABASE_URL`을 설정합니다. 로컬 검증은 SQLite도 가능하지만, 웹과 워커가 서로 다른 프로세스나 서비스라면 반드시 같은 Postgres를 사용해야 합니다.

한 cycle만 실행:

```bash
python -m app.workflows.position_news once
```

Prefect Cloud에 연결된 장기 실행 worker:

```bash
export PREFECT_API_URL="https://api.prefect.cloud/api/accounts/.../workspaces/..."
export PREFECT_API_KEY="..."
python -m app.workflows.position_news serve
```

`serve` 모드는 기본 300초 간격 deployment를 만들고 계속 실행됩니다. 로컬·Prefect 전역 동시 실행을 각각 1개로 제한하며, 각 티커 task는 중앙 AI 예산이 흔들리지 않도록 순차 처리합니다. RSS 읽기만 재시도하고 AI·저장은 자동 재시도하지 않습니다.

## Render 배포

현재 루트 `render.yaml`은 무료 FastAPI 웹 서비스만 유지합니다. Render background worker는 유료 인스턴스이므로 자동으로 추가하지 않았습니다.
중요: 이 브랜치의 에이전트 API는 중앙 DB만 읽습니다. worker를 생성하고 첫 스냅샷이 `ready`인 것을 확인하기 전에 웹만 먼저 배포하면 화면은 계속 `pending`으로 남습니다.


배포할 때 [render.prefect-worker.example.yaml](../render.prefect-worker.example.yaml)의 worker 항목을 실제 Blueprint에 병합하거나 Render 대시보드에서 별도 Background Worker로 생성합니다. 예시 파일을 기존 `render.yaml`의 대체 파일로 그대로 적용하지 마세요.

필수 환경변수:

- `DATABASE_URL`: 웹 서버와 같은 Postgres 연결 문자열
- `POSITION_NEWS_REQUIRE_POSTGRES=true`: 잘못된 SQLite fallback 즉시 차단
- `PREFECT_API_URL`, `PREFECT_API_KEY`: Prefect Cloud workspace
- `ANTHROPIC_API_KEY`: 선택 사항. 없으면 안전한 규칙 기반 분석
- `ANTHROPIC_MODEL`: 사용할 공용 분석 모델

웹과 워커를 동시에 처음 배포하기보다, 웹 배포에서 신규 테이블 생성이 완료된 뒤 워커를 시작하는 편이 안전합니다. 현재 프로젝트는 Alembic이 아니라 `SQLModel.metadata.create_all()`을 사용하므로 이후 테이블 변경 전에는 정식 migration 도입을 권장합니다.

관련 공식 문서:

- [Prefect serve deployment](https://docs.prefect.io/v3/how-to-guides/deployment_infra/run-flows-in-local-processes)
- [Prefect deployments](https://docs.prefect.io/v3/concepts/deployments)
- [Render background workers](https://render.com/docs/background-workers)

## 주요 설정

| 환경변수 | 기본값 | 의미 |
|---|---:|---|
| `POSITION_NEWS_COLLECTION_SECONDS` | 300 | 수집 주기(최소 60초) |
| `POSITION_NEWS_MAX_SCHEDULE_LAG_SECONDS` | 600 | 이보다 늦은 backlog run은 no-op |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | 10 | Postgres 연결 timeout |
| `DATABASE_STATEMENT_TIMEOUT_MS` | 30000 | Postgres statement timeout |
| `POSITION_NEWS_MAX_TICKERS_PER_RUN` | 100 | cycle당 최대 티커 |
| `POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN` | 12 | cycle당 최대 AI 시도 |
| `POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY` | 120 | Postgres가 강제하는 KST 일일 AI 하드 상한 |
| `POSITION_NEWS_MAX_CYCLE_SECONDS` | 240 | 한 cycle의 최대 처리 시간 |
| `POSITION_NEWS_MAX_CONSECUTIVE_FETCH_FAILURES` | 3 | RSS source circuit breaker 기준 |
| `POSITION_NEWS_RETENTION_DAYS` | 30 | 최신 포인터를 제외한 이력 보존 |
| `POSITION_NEWS_CLAIM_TIMEOUT_SECONDS` | 300 | 중단된 pending 작업 회수 시간 |
| `POSITION_NEWS_DEGRADED_RETRY_SECONDS` | 300 | AI 실패 지수 backoff의 시작 간격(최대 6시간) |
| `POSITION_NEWS_STALE_SECONDS` | 900 | API stale 판단 기준 |

## 장애 동작

- 한 티커의 RSS/분석 실패는 다른 티커 수집을 중단하지 않습니다.
- 네트워크 RSS 읽기만 Prefect가 두 번 재시도합니다. AI SDK 내부 재시도와 AI 포함 task 재시도는 꺼져 있습니다.
- 분석 전에 UUID lease를 조건부 DB update로 claim하고 완료·실패에도 같은 token을 검사하므로, 중복 worker와 늦은 worker가 같은 기사 묶음을 덮어쓰지 못합니다.
- 처리 중 worker가 종료되면 claim timeout 뒤 다음 cycle이 작업을 회수합니다. degraded 분석은 마지막 결과를 제공한 채 지수 backoff로 갱신합니다.
- 모든 RSS 소스가 연속으로 실패한 티커가 3건이면 남은 티커를 건너뛰고 flow를 실패시켜 Prefect 알림 대상으로 만듭니다.
- cycle은 내부 240초 deadline과 Prefect hard timeout을 함께 사용하며, 10분보다 늦은 backlog run은 no-op 처리합니다.
- 매 cycle마다 가장 오래 시도되지 않은 티커부터 골라 deadline 뒤쪽 티커의 영구 누락을 막습니다.
- 첫 수집 전 API는 HTTP 200과 `analysis_status=pending`을 반환해 화면을 안정적으로 유지합니다.
