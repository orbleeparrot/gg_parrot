# 포지션 뉴스 수집과 Prefect 운영

매크로 시작 직후 웹 서버가 RSS 기사와 Binance Square 공개 게시글을 먼저 공개하고, Prefect 워커가 Playwright로 공개 웹 페이지를 탐색해 수집 범위를 넓힙니다. 제목은 공유 캐시를 통해 한국어로 번역하며, 에이전트 조회 API는 저장된 결과를 읽습니다.

CHIPUSDT는 USD.AI의 CHIP입니다. 프로젝트명 `USD.AI`·`USDai`·`유에스디에이아이`로 검색하고 일반 반도체 기사와 구분합니다. 등록되지 않은 티커는 Binance 공개 상품 목록의 `an`/`adn` 이름을 조회합니다. 이름 목록 전체를 하루 단위로 공유 캐시하며 장애 시 마지막 성공본을 사용합니다. 이 웹 endpoint는 무료 보조 메타데이터 경로이므로 조회 실패가 티커 검색을 막지 않습니다.

종목 뉴스는 최근 기사부터 수집하며 10건에 못 미치면 최대 5년 범위의 검색을 추가합니다. `POSITION_NEWS_ARCHIVE_MAX_AGE_DAYS` 기본값은 1826일입니다. 오래된 기사에는 `is_historical`와 실제 게시일을 표시하며 현재 포지션 방향 신호 및 새 관측 배지로 취급하지 않습니다. 시세 차트·상품 변환 페이지·무관한 동명 기사는 계속 제외합니다. 시장 전체의 당일 브리핑은 기존 최근 기사 범위를 유지합니다.

웹의 첫 수집이 빈 결과이거나 실패해도 즉시 Prefect에 작업 기회를 넘깁니다. 웹은 마지막 시도 후 최소 120초 또는 수집 주기의 두 배 동안 다시 선점하지 않습니다. Prefect의 `active_ticker_count`는 활성 매크로 티커 수, `due_ticker_count`는 지금 수집할 수 있는 티커 수입니다. 처리 대상이 0개인 정상 실행만으로 활성 매크로나 기사 수집의 정상 여부를 판정하면 안 됩니다.

```text
runner/start → 웹 수집기 깨움 → 티커 DB lease → RSS 병렬 수집
                                                ↓
                                    첫 기사·규칙 분석 즉시 공개
                                                ↓
                               lease 해제, Prefect 수집 대상으로 유지
                                                ↓
Prefect 60초 스케줄 → 수집 시각이 된 활성 티커 → RSS 수집·초기 공개
                                                ↓
                                  Playwright 공개 웹·티커 검색 확장
                                                ↓
                              중복 제거 → 최종 분석 1회·제목 번역 배치
                                                ↓
                                      공용 DB → 내 에이전트
```

웹과 워커에는 동일한 Postgres `DATABASE_URL`을 설정합니다. 같은 티커는 사용자·포지션 방향에 관계없이 공유하며, DB lease가 웹·워커 간 중복 수집을 막습니다. 최종 처리 직전에 lease를 갱신하고, 소유권을 잃은 워커는 처리하지 않습니다.

## 웹과 워커 역할

Render 웹에서는 `POSITION_NEWS_EMBEDDED_BOOTSTRAP_ONLY=true`가 기본입니다. Chromium 없이 새 티커의 RSS와 규칙 분석을 공개하고, 유료 분석·번역은 호출하지 않습니다. 정상 공개 직후 다음 수집 시각을 바로 열어 두므로 Prefect의 확장을 5분 동안 막지 않습니다. 워커 장애로 결과가 수집 간격의 두 배 이상 오래되면 웹이 RSS로 갱신합니다.

로컬 실행은 bootstrap 전용 모드가 기본으로 꺼져 있으며 웹 수집기가 전체 단계를 처리합니다. 외부 워커 없이 웹만 운영할 때는 `POSITION_NEWS_EMBEDDED_BOOTSTRAP_ONLY=false`로 설정하고 Chromium을 설치하세요. 요청 처리 스레드는 수집 완료를 기다리지 않습니다.

## Playwright 범위와 제한

### Binance Square 커뮤니티

`https://www.binance.com/en/square/hashtag/{ticker}`의 **Latest** 탭을 Playwright로 확인했습니다. 해당 탭의 공개 `GET /bapi/composite/v4/friendly/pgc/content/queryByHashtag` 요청을 서버에서도 재사용합니다. 파라미터는 `hashtag=#ticker`, `orderBy=LATEST`, `pageIndex=1..2`, `pageSize=20`입니다. 인증·거래 API 키가 필요하지 않습니다. RSS 링크는 확인되지 않았고 초기 HTML/SSR에는 오래된 Hot 게시글이 섞여 있어 수집에 사용하지 않습니다.

RSS와 병렬로 최대 2페이지를 수집하며 브라우저 배치와 별도로 6초의 요청 예산을 적용합니다. 티커별 5분 공유 DB 캐시·동시 요청 합치기를 사용하고, 401/403/429는 출처 공통 대기시간을 저장합니다. 추가 페이지 실패 때 첫 페이지를 보존하고 갱신 장애 때는 최근 7일 범위의 마지막 성공 결과를 유지하며 `partial`/`stale`를 기록합니다. 공개 페이지만 읽으며 로그인이나 채팅방 가입을 하지 않습니다.

최근 7일의 유효 게시글 중 최신 5건을 기사 10건과 별도로 표시합니다. 작성자당 최대 2건, 동일 ID·동일 제목은 중복 제거하며 태그만 붙인 무관한 글과 사설방 가입 광고를 제외합니다. 본문 전체는 저장하지 않고 짧은 첫 문단 제목, 작성자, 실제 게시일, 원문 링크를 저장합니다. `content_type=community`를 내 에이전트와 코인동향에 보존해 **커뮤니티**로 표시하고 포지션 유불리 분석에는 넣지 않습니다. 표시하는 제목은 다른 언어도 모두 한국어 번역 대상이며 일일 번역 제한은 없습니다.

Prefect `fetch_ticker_news_task` 로그의 `news_source/name=binance_square`에서 `status`, `fetched_count`, `item_count`, `pages_fetched`, `cached`, `elapsed_ms`, `http_status`, `retry_at`를 확인할 수 있습니다. 실행 설정의 `binance_square`에 활성 여부와 수집 범위가 기록됩니다. 새 게시글은 초기 공개 단계에서 저장되며 같은 기사의 분석은 재사용합니다.

### 기사 웹 페이지

RSS 결과가 있어도 브라우저 확장을 수행합니다. 공개 뉴스 사이트의 기사 목록, 프로젝트 태그, 티커 검색 결과를 읽고 관련 기사를 RSS 결과와 합칩니다. 첫 RSS 결과는 브라우저 탐색 전에 공개합니다. 소스 차단·시간 초과는 소스별 상태에 남기며 정상 RSS 결과를 유지합니다. RSS 전체 장애에서도 브라우저 소스로 복구할 수 있습니다.

CoinDesk 섹션·검색·태그 외에 Decrypt 뉴스 목록과 CryptoSlate 뉴스 목록·프로젝트 뉴스 허브를 사용합니다. 출처에 해당 프로젝트 허브가 없거나 최근 기사가 없으면 기존 RSS 결과를 유지합니다. 확인된 발행일이 최대 5년 범위인 기사 중 최신 10건을 중복 제거해 표시하며, 최근 30일보다 오래된 기사는 과거 기사로 구분합니다.

기본 탐색 예산은 90초, 페이지별 예산은 15초, 동시 탭은 Render에서 1개·로컬에서 3개입니다. 기사와 출처 캐시를 재사용하며 최종 기사가 바뀌지 않았다면 저장된 분석·번역을 재사용합니다. 브라우저 탐색 자체에는 모델 API를 사용하지 않습니다.

## 실행과 관리

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-prefect.txt
python -m playwright install --with-deps chromium
# DATABASE_URL, PREFECT_API_URL, PREFECT_API_KEY는 실행 환경에 설정
python -m app.workflows.position_news serve
```

Render 워커는 `Dockerfile.prefect`의 Chromium 포함 이미지를 사용합니다. Prefect deployment는 `gg-parrot-position-news/shared-ticker-news`이며, 60초마다 대상을 확인합니다. 티커별 기본 재수집 간격은 300초이므로 같은 티커를 매분 크롤링하지 않습니다.

Prefect 이력에는 `fetch_ticker_news_task`, `publish_initial_news_task`, `enrich_ticker_news_task`, `process_ticker_news_task`가 분리되어 보입니다. RSS 읽기만 재시도하며 브라우저·유료 모델 작업에는 task 재시도를 적용하지 않습니다. 브라우저 task 로그에는 소스별 결과와 경과 시간이 남고, 실행 요약에는 배포 커밋, 수집·스케줄 간격, AI 한도와 브라우저 설정이 기록됩니다. 키와 DB 접속 정보는 출력하지 않습니다.

`serve`는 `paused=False`, `pause_on_shutdown=False`, `limit=1`, `global_limit=1`을 사용합니다. 교체 배포 중 이전 워커 종료가 새 스케줄을 정지시키는 문제를 방지합니다. 의도적으로 운영을 중단할 때는 Prefect에서 deployment를 직접 pause하세요.

```bash
# 활성 운영 티커를 실제 수집합니다. 키가 있으면 예산 내 모델 API가 호출됩니다.
python -m app.workflows.position_news once
```

테스트는 격리된 SQLite와 모의 소스를 사용하는 pytest로 실행하세요. 운영 점검은 배포 버전, deployment의 paused 상태, 최근 flow/task 상태, 소스별 수집 결과를 함께 확인합니다.

## 주요 설정

```dotenv
POSITION_NEWS_EMBEDDED_ENABLED=true
POSITION_NEWS_EMBEDDED_BOOTSTRAP_ONLY=true
POSITION_NEWS_COLLECTION_SECONDS=300
POSITION_NEWS_SCHEDULE_SECONDS=60
POSITION_NEWS_ACTIVE_SESSION_SECONDS=60
POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED=true
POSITION_NEWS_BROWSER_BUDGET_SECONDS=90
POSITION_NEWS_BROWSER_CONCURRENCY=3
POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN=2
POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY=10
BINANCE_SQUARE_ENABLED=true
BINANCE_SQUARE_CACHE_SECONDS=300
BINANCE_SQUARE_MAX_ITEMS=5
BINANCE_SQUARE_MAX_AGE_DAYS=7
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_POSITION_NEWS_MAX_TOKENS=512
POSITION_NEWS_REQUIRE_POSTGRES=true
```

`POSITION_NEWS_BROWSER_FALLBACK_ENABLED`는 이전 설정입니다. 현재 확장은 `POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED`로 제어합니다. 기사·번역과 일일 예산은 DB에 저장하고, 번역 실패 시 원문을 보존합니다. 기사와 분석 순서를 함께 보존하며, 오류·빈 결과가 마지막 정상 스냅샷을 삭제하지 않습니다.

전체 구조는 [에이전트 실행 구조](../docs/agent-runtime.md), Render 설정은 루트 `render.yaml`과 `render.prefect-worker.example.yaml`을 참고하세요.

브라우저 공개 페이지 캐시는 `BrowserNewsPageCache`에 최대 512개 저장합니다. Prefect의 다음 subprocess도 만료 전 결과를 재사용하며 성공은 15분, 빈 결과·실패는 5분 후 갱신합니다. 배치 조회·원자적 upsert를 사용하고 만료 시간을 조회 시 연장하지 않습니다.

HTTP 429가 발생하면 같은 호스트의 남은 페이지 요청을 중단하고 `Retry-After`를 반영한 대기 시간(기본 5분, 최대 24시간)을 동일 캐시에 저장합니다. 새로운 티커와 다음 worker 프로세스도 이 상태를 공유하며 다른 출처는 계속 수집합니다. 접근 제한을 우회하지 않습니다.

Render 기본 브라우저 동시성은 1입니다. 공개 섹션·태그는 JavaScript 없이 읽고 검색 페이지에만 활성화합니다. 소스 오류에는 driver 시작/브라우저 시작/탐색/추출 단계를 기록합니다. 모든 브라우저 소스가 실패하면 RSS와 최종 분석은 보존하되 enrichment 태스크와 flow를 Failed로 표시합니다.
