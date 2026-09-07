# 포지션 뉴스 수집과 Prefect 운영

매크로 시작 직후 웹 서버가 RSS 기사를 먼저 공개하고, Prefect 워커가 Playwright로 공개 웹 페이지를 탐색해 수집 범위를 넓힙니다. 분석과 번역은 최종 기사 묶음에만 적용하며, 에이전트 조회 API는 저장된 결과를 읽습니다.

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

RSS 결과가 있어도 브라우저 확장을 수행합니다. 공개 뉴스 사이트의 기사 목록, 프로젝트 태그, 티커 검색 결과를 읽고 관련 기사를 RSS 결과와 합칩니다. 첫 RSS 결과는 브라우저 탐색 전에 공개합니다. 소스 차단·시간 초과는 소스별 상태에 남기며 정상 RSS 결과를 유지합니다. RSS 전체 장애에서도 브라우저 소스로 복구할 수 있습니다.

기본 탐색 예산은 35초, 동시 탭은 3개입니다. 기사와 출처 캐시를 재사용하며 최종 기사가 바뀌지 않았다면 저장된 분석·번역을 재사용합니다. 브라우저 탐색 자체에는 모델 API를 사용하지 않습니다.

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
POSITION_NEWS_BROWSER_BUDGET_SECONDS=35
POSITION_NEWS_BROWSER_CONCURRENCY=3
POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN=2
POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY=10
POSITION_NEWS_TRANSLATION_MAX_CALLS_PER_DAY=10
NEWS_TRANSLATION_MAX_CALLS_PER_DAY=20
ANTHROPIC_MODEL=claude-haiku-4-5
ANTHROPIC_POSITION_NEWS_MAX_TOKENS=512
POSITION_NEWS_REQUIRE_POSTGRES=true
```

`POSITION_NEWS_BROWSER_FALLBACK_ENABLED`는 이전 설정입니다. 현재 확장은 `POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED`로 제어합니다. 기사·번역과 일일 예산은 DB에 저장하고, 번역 실패 시 원문을 보존합니다. 기사와 분석 순서를 함께 보존하며, 오류·빈 결과가 마지막 정상 스냅샷을 삭제하지 않습니다.

전체 구조는 [에이전트 실행 구조](../docs/agent-runtime.md), Render 설정은 루트 `render.yaml`과 `render.prefect-worker.example.yaml`을 참고하세요.

브라우저 공개 페이지 캐시는 `BrowserNewsPageCache`에 최대 512개 저장합니다. Prefect의 다음 subprocess도 만료 전 결과를 재사용하며 성공은 15분, 빈 결과·실패는 5분 후 갱신합니다. 배치 조회·원자적 upsert를 사용하고 만료 시간을 조회 시 연장하지 않습니다.

Render 기본 브라우저 동시성은 1입니다. 공개 섹션·태그는 JavaScript 없이 읽고 검색 페이지에만 활성화합니다. 소스 오류에는 driver 시작/브라우저 시작/탐색/추출 단계를 기록합니다. 모든 브라우저 소스가 실패하면 RSS와 최종 분석은 보존하되 enrichment 태스크와 flow를 Failed로 표시합니다.
