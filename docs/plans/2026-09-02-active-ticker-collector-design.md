# 활성 티커 공유 수집기 설계

## 목표와 수명주기

뉴스 수집 대상을 고정 코인 목록으로 유지하지 않고, 실제 실행 중인 매크로의 티커만 활성화한다. 매크로 실행기가 `RunSession`을 만들고 heartbeat를 보내는 행위를 뉴스 수집 요청으로 간주한다. 최근 heartbeat가 설정된 유효 시간 안에 있는 `running` 세션만 활성 lease이며, `stopped`/`error` 세션과 heartbeat가 끊긴 세션은 즉시 또는 lease 만료 후 제외한다. 별도 활성화 테이블이나 사용자별 crawler를 만들지 않는다.

수집 단위는 사용자가 아니라 canonical asset symbol이다. 예를 들어 여러 사용자가 `BMTUSDT`, `BMTUSDC` 매크로를 동시에 실행해도 모두 `BMT` 하나로 정규화한다. discovery 결과는 집합이므로 Prefect 한 주기에서 BMT를 한 번만 fetch한다. `TickerNewsSnapshot.snapshot_key`의 기존 unique claim과 공용 `TickerNewsState`를 그대로 사용해 여러 worker가 겹쳐도 분석과 저장을 한 번만 수행한다. 마지막 BMT 세션이 종료되면 다음 주기부터 BMT 네트워크 수집은 멈추지만, 저장된 스냅샷은 retention 정책에 따라 남는다.

## 실행 흐름과 실패 처리

Prefect worker는 5분 주기로 계속 깨어 있지만 활성 티커가 없으면 RSS·Playwright·AI 호출을 전혀 만들지 않고 retention 정리만 수행한다. 새 매크로는 생성 시점의 heartbeat 때문에 다음 주기에 자동 포함된다. 에이전트 화면은 첫 수집 전에는 기존 pending 응답을 사용하고, 같은 티커의 두 번째 사용자는 첫 사용자가 만든 최신 DB 스냅샷을 즉시 재사용한다.

heartbeat 유효 시간은 `POSITION_NEWS_ACTIVE_SESSION_SECONDS`로 관리하고 기본값은 60초다. 미래 시각은 제한된 clock skew만 허용한다. DB 조회 실패는 기존 flow 실패/재시도 경로를 따르며, source 실패는 ticker 상태에 기록한다. 사용자별 수집 상태나 요청 횟수를 저장하지 않으므로 사용자 증가가 데이터·AI 호출 증가로 이어지지 않는다.

성공 조건은 활성 세션이 없을 때 discovery가 빈 목록이고, 같은 티커 세션 N개가 있어도 결과가 하나이며, 다른 활성 티커는 각각 하나씩 포함되고, 종료·stale 세션은 제외되는 것이다.

## Supabase RLS 경계

gg_parrot 프론트엔드는 Supabase Data API를 직접 사용하지 않고 모든 요청을 FastAPI로 보낸다. 저장소가 소유하는 21개 public 테이블은 `anon`과 `authenticated`에 노출할 이유가 없다. 따라서 해당 테이블은 RLS를 활성화하고 두 client role의 모든 table privilege를 회수한다. FastAPI와 Prefect가 사용하는 Session Pooler 역할은 현재 `postgres`이며 `bypassrls`가 확인됐으므로 서버 동작은 유지된다.

같은 Supabase 프로젝트의 레거시 트래커·분석·카탈로그 테이블 15개에는 Supabase Auth 소유권과 연결할 컬럼이 없고, 일부는 `password_hash`, IP, User-Agent를 포함하면서 `anon`과 `authenticated`에 전체 권한이 열려 있었다. 임의의 공개 정책을 만들지 않고 RLS를 활성화한 뒤 두 Data API 역할의 권한을 회수해 서버 전용으로 전환한다. `postgres`와 `service_role`의 직접 권한은 유지한다. 현재 migration history에 포함된 `profiles`의 무조건 허용 INSERT 정책은 `authenticated` 사용자의 자기 행만 허용하도록 교정하고, 경고된 함수는 schema-qualified body와 고정된 빈 `search_path`를 사용한다. 적용 전 transaction 안에서 RLS와 grant 검증 SQL을 실행하고 rollback하며, 적용 후 CLI Security Advisor와 서버 DB 연결 smoke test를 다시 실행한다.
