# 배포 가이드 (Vercel 프론트 + Render 백엔드)

프론트(React/Vite)는 **Vercel**, 백엔드(FastAPI)는 **Render**에 올립니다.
매크로 실행 상태는 웹 API가, 뉴스 보강은 Prefect와 Playwright 워커가 처리합니다.

```
브라우저 ──▶ gg-parrot.vercel.app (프론트, Vercel)
                   │  /api/* 요청은 vercel.json 이 Render로 프록시
                   └──▶ gg-parrot.onrender.com (백엔드, Render)
```

## 1) 백엔드 → Render

1. https://render.com 로그인 → **New +** → **Blueprint**
2. GitHub 레포 `orbleeparrot/gg_parrot` 연결 → `render.yaml` 자동 인식 → **Apply**
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

웹은 새 티커의 RSS를 즉시 저장하고, Prefect worker는 Playwright 공개 페이지 수집과 AI 보강을 담당합니다. 루트
`render.yaml`에 `gg-parrot-position-news` Background Worker가 포함되어 있습니다.
변경을 push한 뒤 Render Blueprint에서 **Sync Blueprint**를 실행하고, worker에
웹과 같은 `DATABASE_URL` 및 `PREFECT_API_URL`·`PREFECT_API_KEY`를 입력합니다.
worker는 Playwright Chromium이 포함된 Docker 이미지로 배포되며 CoinDesk·Decrypt·CryptoSlate의 공개
뉴스 목록과 프로젝트 페이지를 수집합니다. Blueprint 동기화 시 Python runtime에서 Docker
runtime으로 바뀌는 변경도 함께 적용해야 합니다.
Background Worker에는 무료 플랜과 HTTP health check가 없습니다. 구조, 로컬 실행,
Prefect Cloud 연결, 상세 검증 절차는
[backend/PREFECT_POSITION_NEWS.md](backend/PREFECT_POSITION_NEWS.md)를 참고하세요.

에이전트 API는 뉴스 소스를 직접 수집하지 않고 중앙 DB 스냅샷을 읽으며, 미완료 번역·요약은 공유 캐시를 통해 보강합니다. 웹과 worker가 같은 Postgres를 사용하는지, Prefect의 `shared-ticker-news`가 일시정지되지 않았는지, 배포 버전과 RSS → 초기 저장 → 브라우저 → 최종 저장 태스크를 확인합니다. `pause_on_shutdown=False`로 롤링 배포 시 기존 worker가 새 스케줄을 정지하는 문제를 방지합니다.

Binance Square는 커뮤니티 게시글로 구분합니다. 짧은 글은 공개 Latest 응답의 본문을 사용하고, 장문은 같은 사이트의 공개 상세 응답 `bodyTextOnly`를 조회합니다. 목록·상세 조회는 합계 6초 예산 안에서 수행하며 게시글 ID별 상세 캐시를 여러 티커가 공유합니다. 본문은 내부에만 저장하고 화면에는 한국어 제목과 본문을 바탕으로 만든 요약을 표시합니다. 수집·요약 입력이 잘리면 부분 요약으로 표시하고, 본문이 없거나 조회에 실패하면 제목을 본문으로 대신하지 않습니다.

요약은 기본 2~3문장으로 만들며 매우 짧은 게시글은 1문장이 될 수 있습니다. 게시글당 저장 본문은 최대 20,000자·JSON 20KB, 모델 입력은 최대 12,000자이며 입력이 잘리면 화면에 `본문 일부 요약`으로 표시합니다. `ANTHROPIC_MODEL`을 사용하고 일일 횟수 제한은 없습니다. 게시글 ID·본문 해시·모델/프롬프트 버전으로 30일 동안 재사용하며, 공유 DB 작업 선점에 실패하면 유료 호출을 하지 않습니다. 웹 종료 시 대기 작업을 취소하고 실행 중인 요약이 끝난 뒤 AI 연결을 닫습니다.

본문 요약은 제목보다 긴 배치를 처리하므로 `COMMUNITY_SUMMARY_TIMEOUT_SECONDS` 기본 45초(10~60초 범위)를 따로 사용합니다. HTTP 읽기는 이 시간을 기다리지 않습니다. 시간 초과 시 즉시 유료 호출을 반복하지 않고 성공한 요약을 보존하며 공유 실패 항목만 기존 재시도 간격 후 처리합니다.

`publish_initial_news_task`는 번역·요약 AI를 호출하지 않고 최초 본문 스냅샷을 먼저 발행합니다. `process_ticker_news_task`에서 미번역 원문도 보존한 채 제목 번역과 `community_summaries.enrich_items(..., wait=True)`를 수행합니다. 같은 게시글의 본문 해시가 달라지면 새 스냅샷·요약을 만들고, 바뀌지 않은 뉴스 기사의 유료 분석은 재사용합니다. HTTP 응답은 요약 완료를 기다리지 않으며 공유 캐시에서 완료된 결과를 표시합니다.

Prefect에서 `community_content` 로그의 `stage=fetched/processed`를 비교하면 본문 `ready/missing/error` 건수와 요약 `ready_count/pending_count/unavailable_count`를 확인할 수 있습니다. 본문 원문과 API 키는 로그에 넣지 않습니다. 요약·유료 모델 태스크는 자동 재시도하지 않고 공유 캐시의 작업 소유권과 다음 수집 주기로 복구합니다. 요약 캐시는 웹·워커가 같은 Postgres를 사용해야 중복 호출을 막을 수 있습니다. 기존 수집 유지보수 태스크가 30일 지난 요약을 한 번에 최대 500건 정리하며 HTTP 읽기마다 정리 쿼리를 실행하지 않습니다. 정리 결과는 `news_cache_maintenance` 및 flow의 `community_summaries_pruned`로 확인합니다.

## 대규모 체결 중앙 수집

기존 `python -m app.workflows.position_news serve` 명령이 뉴스·소스 진단과 고래 체결을 함께 관리합니다. Prefect 관리 프로세스는 공유하고 실행 슬롯은 분리합니다. 뉴스·Playwright 진단은 기존 슬롯 1개를 공유하며, `gg-parrot-whale-activity/shared-whale-trades`는 별도 슬롯 1개로 30초마다 실행됩니다. 느린 뉴스 수집이 체결 수집을 대기시키지 않으며 추가 Render 서비스나 유료 모델은 사용하지 않습니다. 네 배포 모두 같은 커밋 버전과 `pause_on_shutdown=False`를 사용합니다.

`whales.py`의 공개 Binance aggregate trades 로직을 사용합니다. 최근 heartbeat가 있는 실행 세션의 `(market, symbol)`을 합쳐 한 번 수집하고, 매크로가 없으면 외부 호출을 하지 않습니다. 매크로 시작 직후 웹의 별도 백그라운드 수집기가 첫 결과를 만들며, 운영에서는 이후 120초 동안 Prefect에 양보합니다. Prefect 장애 시 같은 DB lease·재시도 간격을 따르는 웹 복구 경로가 작동합니다. HTTP 세션 API는 공용 DB 결과만 읽으며 조회 결과를 프로세스 내 2초 동안 공유합니다.

수집마다 최대 500개 공개 체결을 확인하고, 기본 `AGENT_LARGE_TRADE_MIN_QUOTE=100000` USDT/USDC 이상 체결을 최근 10분 동안 합쳐 최신 30개까지 보관합니다. 체결 ID로 중복을 제거하며 시장을 구분합니다. 활발한 종목의 모든 체결을 보장하는 데이터가 아니며 특정 지갑·고래의 보유량 변화로 해석하지 않습니다. 기존 비활성 온체인 보유량 분석과 배너는 그대로 비활성입니다.

`WhaleTradeState`는 30초 갱신 커서, 60초 작업 소유권, 마지막 정상 스냅샷을 공용 Postgres에 보관합니다. 작업 소유권을 얻지 못하면 외부 호출을 하지 않으며, 만료된 작업은 새 결과를 덮을 수 없습니다. 429·418의 재시도 시각은 같은 시장의 다른 종목에도 공유하고 실패해도 마지막 정상 관측 시각을 갱신하지 않습니다. 7일간 비활성인 데이터는 수집 유지보수에서 최대 500행씩 정리합니다. 가장 오래 수집하지 못한 종목부터 순환하며 웹 보조 수집 대상도 한 번의 일괄 상태 조회로 선택합니다.

Prefect의 `whale_discovery`, `whale_source`, `whale_collection` 로그에서 활성 거래쌍, HTTP 상태, 표본·대규모 체결 수, 소요 시간과 재시도 간격을 확인합니다. 정상 빈 결과는 `empty`, 소스 실패는 실패한 flow로 남습니다. 늦은 스케줄을 재생하지 않고, 개별 flow는 60초 제한과 20초 수집 예산을 사용합니다. `configuration`에는 실제 커밋과 AI 호출 0이 표시됩니다. 배포 뒤 새 Prefect 배포가 READY이고 새 버전의 정기 실행이 완료되는지 확인합니다.

선택적 운영 스위치는 `WHALE_TRADE_PREFECT_ENABLED=false`(기존 뉴스만 실행), `WHALE_TRADE_EMBEDDED_ENABLED=false`(웹 보조 수집 끄기), `WHALE_TRADE_EMBEDDED_BOOTSTRAP_ONLY=true`(웹 첫 수집·장애 복구만)입니다. 현물 기본 주소는 Binance 공식 공개 시세 전용 `data-api.binance.vision`, 선물은 `fapi.binance.com`이며 `BINANCE_API_BASE`·`BINANCE_FAPI_BASE` 설정을 존중합니다. [Binance 공개 시세 API 문서](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md)

## DB 초기화와 롤링 배포

웹 DB 초기화는 모듈 import 때 실행하지 않고 FastAPI lifespan에서 수행합니다. Postgres는 컬럼·인덱스·BIGINT 타입·번역 테이블 권한 상태를 먼저 조회해 이미 반영된 DDL을 생략합니다. `ADD COLUMN IF NOT EXISTS`도 테이블 잠금을 얻으므로 정상 시작에 반복하면 실행 중인 매크로의 갱신과 교착상태가 생길 수 있습니다.

실제 변경이 필요할 때만 트랜잭션 advisory lock으로 웹/워커의 마이그레이션을 직렬화하고, 2초 lock timeout을 적용합니다. `40P01`(교착상태)·`55P03`(잠금 대기 실패)은 트랜잭션 전체를 rollback한 뒤 최대 3회 시도하며, 다른 오류는 숨기지 않습니다. 번역 캐시의 RLS와 공개 역할 권한 회수는 유지합니다.

## 온체인 상위 주소 중앙 수집

`gg-parrot-onchain-holders/shared-onchain-holders`가 기존 워커에 함께 등록됩니다. 별도 Render 서비스나 관리 프로세스 없이 체결 수집 실행 슬롯을 공유하며, 매분 만기가 지난 소스 중 가장 오래 기다린 한 개만 확인합니다. HTTP 요청당 최대 12초와 응답 크기 제한을 적용하고, 실제 호출 간격은 공용 `OnchainHolderState`의 만기로 관리합니다. 첫 기준 수집은 세 번의 정기 실행에 걸쳐 완료됩니다.

| 소스 | 대상 | 실제 호출 간격 | 수집 범위 |
| --- | --- | --- | --- |
| Blockscout `getTokenHolders` | PEPE | 10분 | Ethereum PEPE 계약 상위 50개 주소에서 알려진 제외 주소 필터 |
| Blockscout `getTokenHolders` | WETH | 10분 | Ethereum WETH 계약의 동일 범위. ETH 매크로에도 WETH 범위를 명시 |
| XRPScan `/api/v1/balances` | XRP | 6시간 | 원본 최대 10,000개 중 상위 50개를 선택하고 제외 주소 필터 |

[XRPScan 원본은 매일 밤 갱신됩니다](https://docs.xrpscan.com/api-documentation/balance/balances). XRP를 실시간 잔고로 표현하지 않습니다. Blockscout 계약은 `backend/app/whales.py`에 명시하며 [공식 토큰 보유자 API](https://docs.blockscout.com/api-reference/token/get-token-holders)를 사용합니다. `WHALE_TOP_N`은 1~100개, `WHALE_EXCLUDE_ADDRESSES`는 추가 제외 주소이며 XRP 주소 대소문자는 유지됩니다. 별도 유료 모델 호출은 없습니다.

수집은 매크로 수와 무관하게 이 세 코인의 공통 기준을 유지합니다. 인증된 매크로 API에는 PEPE/1000PEPE, ETH/WETH, XRP에 해당하는 결과만 `onchain`으로 포함합니다. CHIP 등 다른 티커에 이 자료를 붙이지 않습니다. 첫 기준·변화 없음·새로 진입하거나 사라진 주소는 잔고 변화 알림을 만들지 않습니다. 직전 관측과 현재 관측에 모두 있는 주소의 정확한 정수 잔고만 비교하며, 긴 수집 공백은 기준을 새로 설정합니다. 알림은 관측 시각과 비교 기간을 표시하고 매수·매도 체결로 해석하지 않습니다.

`GET /api/whale-activity`에서 세 소스의 마지막 성공·다음 수집·추적 주소 수·상태를 확인할 수 있습니다. 이 공개 경로와 인증된 에이전트 경로 모두 짧은 읽기 캐시만 사용합니다. 원본 주소 목록·잔고·수집 권한 토큰은 공개하지 않습니다. DB 테이블은 RLS 및 공개 역할 권한 회수 대상이며, 429의 재시도 간격은 같은 공급자 전체에 적용합니다. 실패 시 기존 정상 관측은 보존합니다.

Prefect에서 `onchain_discovery`, `onchain_source`, `onchain_collection` 로그의 `coin`, `http_status`, `fetched_count`, `excluded_count`, `tracked_count`, `observed_at`, `error_code`를 확인합니다. 수집 실패는 실패 상태로 남기고 다음 만기에 재시도합니다. `not_due_or_claimed` 또는 수집 대상 0개는 DB 만기/중복 방지에 따른 정상 결과입니다. 기존 `WHALE_TRADE_PREFECT_ENABLED=false`는 체결과 온체인 수집을 함께 끄므로 뉴스만 운영할 때만 사용합니다.
