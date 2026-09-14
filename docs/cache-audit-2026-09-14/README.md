# 캐시 전략 점검 — 2026-09-14

기준: `design/round-2026-09-14`, 앱 커밋 `f30fbee`.

> 후속 작업: 아래는 수정 전 감사 기록이다. 확인된 항목의 수정 내용과 회귀 검증은 [캐시 보완 결과](IMPLEMENTATION.md)에 정리했다.

**캐시는 여러 계층에 적용되어 있다. 다만 계정 분리, 데이터 변경 후 무효화, 만료 후 갱신, 용량 제한, HTTP 전달 정책이 일관되지 않다.** 사전 계산을 추가한 것만으로 모든 조회 지연이 사라지는 구조는 아니다. 우선순위는 계정 경계와 데이터 정확성 보완 → 공개 응답 재사용 → 용량·계측 관리다.

이번 작업은 감사다. 앱 코드, 운영 설정, 운영 데이터는 수정하지 않았다. 아래 스크립트는 현재 결함을 재현하는 감사 자료이며, 결함을 수정한 뒤에도 통과해야 하는 회귀 테스트가 아니다.

## 확인 범위와 한계

- 백엔드 메모리/SQLite/공유 DB 캐시, 프런트 메모리/브라우저 저장소, 로그인 전환, HTTP/CDN, PWA 정적 자원 경로를 확인했다.
- 로컬 공개 뉴스·리더보드의 HTTP 헤더를 실제 조회했다. 요청은 준비된 결과를 읽으며 수집·번역을 직접 실행하지 않는다.
- 운영 사이트는 HTML·정적 파일만 읽었다. 운영 API의 캐시 상태나 실제 사용자 데이터 노출 여부를 시험하지 않았다.
- 계정·봉·만료·용량 문제는 모의 API, 임시 SQLite, 네트워크를 차단한 재현으로 검증했다. 에이전트 화면은 로컬 빌드와 모의 API를 사용하는 브라우저로 검증했다.
- 합성 291개 캐시 키, 뉴스 1,000개 키, 채팅 5,000개 메시지는 운영 사용량이 아니다. 운영 적중률, 메모리 사용량, p95 응답속도는 측정하지 않았다.

## 적용되어 있는 전략

| 대상 | 현재 전략 | 평가 |
|---|---|---|
| 리더보드 | 약 5초 주기 백그라운드 계산, DB snapshot, 작업 lease, 마지막 완료 결과 유지 | 페이지 요청에서 계산을 분리한 방향은 적절함. 사용자별 잠금·투표는 별도로 읽음 |
| 공개/에이전트 뉴스 | 준비된 기사·번역·분석 DB 저장, revision/cursor, worker lease | 수집·번역 대기를 페이지 조회에서 분리. 공개 뉴스 전체 응답 재사용은 보완 가능 |
| AI·요약 | 입력/모델/프롬프트 hash, 동시 작업 합치기, AI 15분/512개, 본문 요약 1시간/1,024개 LRU와 DB 저장 | 비싼 계산 재사용과 용량 제한이 존재 |
| 최적화 | 데이터 fingerprint, 5분/64개 LRU, 같은 작업 합치기 | 입력과 시세 데이터 변경을 키에 반영 |
| 게시판 목록 | 프런트 30초 TTL, 20개 상한, 계정별 키, 쓰기 후 버전 증가·무효화 | 늦은 GET이 수정 후 캐시를 덮는 것을 방지. 프로필 변경 연결은 누락 |
| 뉴스 화면 | 메모리+sessionStorage, 마지막 정상 자료 선표시, 준비 상태별 갱신, 실패 backoff, 숨겨진 탭 중지 | 재방문과 일시적 오류 대응이 존재. 전체 저장 용량 상한은 없음 |
| 채팅 | 회원별 피드·읽음 커서, 새 메시지 증분 조회, 표시 메시지 재검증, 날짜 경계 정리 | 계정 분리와 증분 읽기는 적용. 200개 제한은 DOM에만 적용 |
| 동일 GET | 계정별 진행 중 요청 합치기 | 완료된 응답을 재사용하는 캐시와는 다름 |
| 아바타 | 버전 URL, ETag/304, 300초 캐시, 없는 이미지 `no-store` | 사진 변경과 조건부 조회를 처리 |
| 정적 배포 | 운영 Vercel CDN HIT, ETag, HTML 재검증 | CDN은 동작함. 해시 JS의 브라우저 장기 캐시는 누락 |

근거: [leaderboard_runtime.py](../../backend/app/leaderboard_runtime.py#L12), [public_news.py](../../backend/app/public_news.py#L188), [repository.py](../../backend/app/agent_features/position_news/repository.py#L572), [ai_runtime.py](../../backend/app/ai_runtime.py#L186), [community_summaries.py](../../backend/app/community_summaries.py#L22), [optimize_runtime.py](../../backend/app/optimize_runtime.py#L24), [boardListCache.js](../../frontend/src/lib/boardListCache.js#L2), [requestCoordinator.js](../../frontend/src/lib/requestCoordinator.js#L53), [chatStore.js](../../frontend/src/lib/chatStore.js#L63), [main.py](../../backend/app/main.py#L438).

## 우선 수정할 정합성 문제

### 1. 계정 전환 시 이전 계정의 상태·작업 캐시가 남음 — 높음

세 경로를 재현했다.

| 경로 | 재현된 결과 | 필요한 처리 |
|---|---|---|
| 직접 만들기 | A의 조건·테스트 매크로·결과를 저장 → 로그아웃 → B 로그인 후 A의 작업 복원 가능 | 회원별 저장 키, 로그아웃 시 정리, 비회원 작업을 회원 작업으로 넘기는 명시적 규칙 |
| 내 에이전트 | A의 LINKUSDT 실행 화면 → 다른 탭의 로그인 변경을 나타내는 storage event로 B 전환 → B 조회 실패 시 A의 차트·실행 정보가 계속 표시 | 데이터에 요청 계정 표시, 계정 변경 즉시 이전 데이터 숨김/초기화, 이전 폴링 응답 무시 |
| 리더보드 잠금 해제 | A의 요청 대기 → B(900P) 로그인 → A의 80P 응답이 B의 로컬 포인트를 80P로 덮음 | 응답 적용 전에 요청 당시 계정·토큰 확인 |

이는 같은 브라우저의 로컬 정보 혼입이다. 서버 권한 우회나 실제 포인트 이전을 확인한 것은 아니다. `MyPage`와 프로필 편집에는 계정 가드가 이미 있어 해당 패턴을 재사용할 수 있다.

근거: [studioSession.js](../../frontend/src/lib/studioSession.js#L3), [auth.js](../../frontend/src/lib/auth.js#L63), [Agents.jsx](../../frontend/src/pages/Agents.jsx#L136), [Leaderboard.jsx](../../frontend/src/pages/Leaderboard.jsx#L231), [MyPage.jsx](../../frontend/src/pages/MyPage.jsx#L154). 증거: [클라이언트 결과](client-cache-results.json), [에이전트 브라우저 결과](agents-account-browser-result.json), [포인트 결과](unlock-account-cache-result.json).

### 2. 마감 전 봉이 장기 캐시에 들어가 다시 쓰일 수 있음 — 높음

백테스트의 `get_klines`는 받은 봉을 마감 여부 확인 없이 SQLite에 저장하고 요청 구간을 검증 완료로 표시한다. 이후에는 구간 coverage/행 경계가 맞으면 최신 가격을 확인하지 않고 반환한다. 기간 프리셋은 현재 시각까지 포함하므로 진행 중인 봉에도 이 경로가 적용된다.

재현: 종가 11 저장 → 같은 봉의 제공자 응답을 14로 변경 → 재조회 결과 11, 제공자 호출 총 1회. 별도 실시간 차트 경로는 미확정 봉을 영구 저장하지 않도록 처리하고 있다.

마감된 봉만 장기 저장하고, 미확정 봉은 별도 짧은 캐시로 처리하거나 백테스트에서 제외해야 한다. 이미 저장된 미확정 가격도 재검증할 필요가 있다. 캐시 시간을 늘리는 방식으로 해결할 수 없는 정확성 문제다.

근거: [binance.py](../../backend/app/data/binance.py#L143), [캐시 반환·저장](../../backend/app/data/binance.py#L375), [차트의 마감 봉 처리](../../backend/app/data/binance.py#L458). 증거: [historical_open_bar_cache](backend-cache-results.json).

### 3. 종목·매크로 목록에 만료와 갱신 이벤트가 빠짐 — 보통

- 종목 목록은 `fetchedAt`을 저장하지만 재사용할 때 확인하지 않는다. `stale: true`나 빈 목록도 성공 캐시로 저장한다. 모의 시계를 10일 뒤로 이동해도 이전 종목을 반환하고 API 호출은 총 1회였다.
- 채팅의 매크로 선택 목록은 첫 정상 응답 이후 다시 받지 않는다. 등록·삭제·잠금 해제·날짜 변경 후의 갱신 이벤트가 없다.
- 코인동향의 경주마 종목·가격은 진입 시 한 번 받고, 하단 경주마는 45초마다 다시 받는다. 같은 데이터에 서로 다른 갱신 규칙을 사용한다.

종목 목록에는 TTL과 화면 재활성화 재검증, 실패/빈 응답의 재시도가 필요하다. 매크로 목록은 날짜·권한·등록 이벤트로 무효화하고, 경주마는 공용 데이터 상태와 갱신 구독을 공유하는 것이 적절하다.

근거: [useSymbolList.js](../../frontend/src/hooks/useSymbolList.js#L11), [ChatBox.jsx](../../frontend/src/components/ChatBox.jsx#L574), [News.jsx](../../frontend/src/pages/News.jsx#L463), [HotCoinsMarquee.jsx](../../frontend/src/components/HotCoinsMarquee.jsx#L35). 종목 TTL은 [격리 재현](client-cache-results.json), 나머지는 코드 경로 확인이다.

### 4. 프로필 변경이 게시판 작성자 정보까지 갱신하지 않음 — 보통

프로필 저장은 게시판 목록 캐시를 무효화하지 않는다. 게시판 조회 → 이름 수정 → 30초 안에 재방문하면 이전 이름을 반환한다. 목록 화면은 TTL 경과만으로 자동 재조회하지 않아 그 뒤에도 이전 표시가 남을 수 있다. 본인 사진은 `AuthorAvatar`가 로그인 상태의 최신 URL로 덮어쓰므로 사진까지 항상 낡게 보인다고 단정할 수 없다.

추가로 댓글 작성자명은 DB에 복사해 둔 이름 자체를 수정하지 않는다. 프로필 변경 후 글 작성자만 새 이름이고 댓글은 이전 이름인 것을 임시 SQLite에서 확인했다. 탈퇴 익명화 경로에도 댓글 누락이 있다. 이 부분은 캐시를 비워도 해결되지 않는 원본 데이터 정합성 문제다.

근거: [api.js](../../frontend/src/api.js#L129), [useBoardList.js](../../frontend/src/hooks/useBoardList.js#L10), [UserAvatar.jsx](../../frontend/src/components/UserAvatar.jsx#L22), [profile.py](../../backend/app/profile.py#L54), [board.py](../../backend/app/board.py#L474). 증거: [목록 캐시](client-cache-results.json), [댓글 DB](profile-comment-snapshot-result.json).

### 5. 개인 응답의 HTTP 저장 금지 정책이 일부 빠짐 — 우선 보완

실제 ASGI 라우트를 가짜 회원·가짜 저장소 응답으로 실행했다. `/api/auth/me`, `/api/me/dashboard`, `/api/me/macros`, `/api/me/runner/key`, `/api/me/runner/sessions`는 모두 200 응답에 `Cache-Control`이 없었다. 같은 실행에서 스트림 토큰 발급은 `no-store`가 있었다.

회원 키·개인 활동·실행 상태에는 명시적인 `private, no-store` 정책이 필요하다. Authorization의 존재만으로 모든 브라우저/중간 계층의 저장 정책을 설명할 수는 없다. 이 결과는 정책 누락의 증거이며 실제 CDN 유출이 발생했다는 증거는 아니다. 앱 내부 상태의 계정 분리도 별도로 필요하다.

근거: [main.py](../../backend/app/main.py#L399), [대시보드](../../backend/app/main.py#L488), [회원 키](../../backend/app/main.py#L1502), [private-headers.json](private-headers.json). `no-cache`는 저장 금지가 아니라 재검증 지시이고, 저장 금지는 `no-store`다. [MDN Cache-Control](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control)

## 로딩과 자원 사용의 보완점

### 6. 만료 후 첫 요청은 외부 API를 기다리고 실패를 반복함 — 보통

`SingleFlightGroup`은 갱신이 이미 진행 중일 때 뒤따르는 요청에만 이전 값을 즉시 준다. 만료 직후 첫 요청은 갱신 함수를 동기 실행하므로 외부 API를 기다린다. 차트·경주마·공포지수·수온 등에 영향을 준다.

또한 공포지수의 만료 후 외부 실패를 5번 순차 재현하면 외부 호출도 5번이었다. 이전 값을 반환하더라도 실패 후 재시도 시각을 기록하지 않는다. 종목 목록은 전역 락 안에서 현물·선물 목록을 순차 조회하므로 다른 요청까지 기다릴 수 있다.

마지막 정상 값이 허용된 수명 안에 있으면 먼저 응답하고 백그라운드에서 한 번 갱신하도록 보완할 수 있다. 실패에는 backoff와 최대 stale 수명이 필요하다. 실시간 가격과 확정 과거 데이터에 같은 허용 수명을 적용해서는 안 된다.

근거: [http_runtime.py](../../backend/app/http_runtime.py#L145), [feargreed.py](../../backend/app/feargreed.py#L84), [symbols.py](../../backend/app/data/symbols.py#L61). 증거: [stale_singleflight / expired_cache_failure_retries](backend-cache-results.json).

### 7. 준비된 공개 결과에도 매번 DB 조회와 전체 응답 생성 — 보통

공개 뉴스는 매번 feed 상태와 기사 목록을 읽고 최대 100개 JSON을 해석한다. 공용 공개 응답에는 `Cache-Control`·ETag가 없어 HTTP 재사용/조건부 응답 정책도 없다. 짧은 공용 응답 캐시나 revision을 활용한 조건부 응답을 추가할 여지가 있다. ETag를 전체 DB 조회 뒤 계산하면 전송량은 줄여도 DB 부하는 그대로이므로 조회 경로까지 설계해야 한다.

이번 로컬 1회 표본은 다음과 같다. 3개 요청을 병렬로 실행한 단일 표본이므로 평소 평균·p95로 해석하지 않는다. `DB span`은 SQL 실행만의 시간이 아니며 연결/세션 대기를 포함할 수 있다.

| 경로 | 상태 | 전체 app span | DB span | SQL span |
|---|---:|---:|---:|---:|
| 시장 뉴스 | 200 | 970ms | 962ms | 576ms / 2회 |
| BTC 뉴스 | 200 | 1,122ms | 1,111ms | 740ms / 2회 |
| 리더보드 | 200 | 1,829ms | 1,820ms | 602ms / 2회 |

사전 계산 후에도 DB 조회와 연결·세션 대기 시간이 남아 있음을 보여준다. 공개 뉴스는 우선 1~3초 범위의 공용 응답 캐시를 검토할 수 있다. 작업 관심 등록과 기사 준비 상태의 재조회 주기는 유지해야 한다. 리더보드는 공용 순위 snapshot과 개인 잠금·투표 상태가 섞여 있으므로 전체 응답을 그대로 공용 CDN에 저장하면 안 된다.

프런트 공통 API 래퍼가 공개 GET에도 로그인 토큰을 붙이므로 공개 캐시를 추가할 때 토큰 전달과 응답 범위를 함께 검토해야 한다. 현재 Vercel 외부 rewrite는 원본의 캐시 헤더를 따르므로 API 정책을 명시하는 것이 실제 전달 계층에도 중요하다. [Vercel 외부 원본 캐시 정책](https://vercel.com/changelog/vercels-cdn-now-respects-cache-control-headers-from-external-origins-by-default)

근거: [public_news.py](../../backend/app/public_news.py#L112), [articles.py](../../backend/app/agent_features/position_news/articles.py#L188), [api.js](../../frontend/src/api.js#L58), [vercel.json](../../frontend/vercel.json), [HTTP 실측](http-headers.json).

### 8. CDN은 동작하지만 해시 정적 파일의 브라우저 장기 캐시가 빠짐 — 보통

운영 HTML, SVG, manifest, `assets/index-B0LL36tW.js` 모두 `x-vercel-cache: HIT`와 ETag가 있었다. 따라서 정적 캐시가 전혀 없다고 볼 수 없다. 다만 해시 JS까지 `public, max-age=0, must-revalidate`여서 새 문서에서 해당 자원을 다시 필요로 할 때 브라우저 재검증을 요구하는 정책이다.

내용에 따라 파일명이 바뀌는 JS/CSS/폰트에는 긴 `max-age`와 `immutable`을 적용할 수 있다. HTML과 이름이 고정된 SVG·manifest에는 별도 재검증 정책을 유지해야 한다. 파일명이 고정된 자원 전체에 무조건 1년 캐시를 적용하면 배포 후 갱신이 늦어진다. [Vercel Cache-Control 문서](https://vercel.com/docs/caching/cache-control-headers)

근거: [HTTP 실측](http-headers.json), [vercel.json](../../frontend/vercel.json), [백엔드 정적 fallback](../../backend/app/main.py#L1691). 운영 헤더는 배포된 정적 파일의 관측이며 현재 브랜치 전체가 배포되었다는 의미는 아니다.

### 9. TTL은 있지만 만료 항목·용량을 정리하지 않는 캐시 — 보통

| 대상 | 확인된 동작 | 권장 방향 |
|---|---|---|
| 서버 차트 | symbol·interval·limit·market 조합마다 dict 추가. 만료 뒤에도 다른 키는 잔류 | 최대 항목/바이트 LRU, TTL 정리, 같은 범위 데이터를 잘라 반환 |
| 브라우저 뉴스 | 다른 키의 만료 항목을 남긴 채 전체 Map을 sessionStorage에 직렬화 | 항목/바이트 상한, 저장 전 만료 정리 |
| 브라우저 채팅 | DOM은 200개지만 받은 메시지는 캐시에 유지. 이전 날짜는 정리하나 당일 메시지 수·계정별 Map 상한은 없음 | 최근 페이지 중심 보관, 이전 기록 재조회, 비활성 계정 해제 |

재현에서 만료 차트 키 291개가 남았고, 뉴스는 만료 1,000개+새 1개가 저장되었으며, 채팅은 공급한 5,000개를 보관하면서 200개만 표시했다. 이는 합성 동작 검증이다. 실제 메모리 장애가 발생했다고 단정하지 않는다.

근거: [chart.py](../../backend/app/chart.py#L43), [newsBriefings.js](../../frontend/src/lib/newsBriefings.js#L101), [chatFeed.js](../../frontend/src/lib/chatFeed.js#L32), [chatStore.js](../../frontend/src/lib/chatStore.js#L4). 증거: [백엔드](backend-cache-results.json), [클라이언트](client-cache-results.json).

### 10. 과거 구간 수집·환율·재시작 정책도 보완 가능

- 과거 봉을 10개에서 12개 구간으로 늘리면 부족한 2개만 받지 않고 처음부터 다시 조회한다. 누락 구간 수집과 동일 구간 동시 요청 합치기가 필요하다. 과거 펀딩도 별도 캐시 없이 외부 조회한다. [binance.py](../../backend/app/data/binance.py#L383), [펀딩](../../backend/app/data/binance.py#L479)
- 환율 TTL이 가격과 같은 10초다. 기존 정상 환율 1,500이 있어도 만료 뒤 실패한 첫 요청은 설정된 fallback 1,380을 반환하는 것을 재현했다. 환율 TTL을 분리하고 마지막 정상 값·관측 시각·허용 stale 기간을 함께 다루는 것이 적절하다. [kimchi.py](../../backend/app/kimchi.py#L35), [실패 처리](../../backend/app/kimchi.py#L122)
- 시장·차트·종목 메모리 캐시는 프로세스별이다. 과거 봉은 로컬 `backend/cache/market.db`에 저장되며 저장소의 `render.yaml`에는 해당 경로 영구 디스크 설정이 없다. 실제 운영 디스크/인스턴스 구성은 별도 확인이 필요하다. [binance.py](../../backend/app/data/binance.py#L36), [render.yaml](../../render.yaml)
- manifest는 있지만 서비스 워커 등록이나 앱 셸 오프라인 캐시 구현은 찾지 못했다. 모바일 오프라인 화면을 제공하려면 별도 설계가 필요하다. 이것은 제품 선택 사항이며 모든 API를 오프라인 캐시에 넣으라는 의미가 아니다. [manifest](../../frontend/public/favicon/manifest.json), [index.html](../../frontend/index.html#L30)
- 요청·DB·외부 호출 시간은 계측하지만 캐시 hit/miss/stale, eviction, 엔트리 수·용량 계측은 없다. 운영 효과를 판단하려면 추가해야 한다. [observability.py](../../backend/app/observability.py#L198)

## 권장 적용 순서

1. **계정과 정확성:** Studio 회원별 저장, Agents 계정 전환 초기화, 늦은 응답 가드, 미확정 봉 장기 저장 보완, 개인 API 저장 금지.
2. **변경 반영:** 종목 TTL, 매크로 목록 이벤트, 프로필→게시판 무효화, 댓글 저장 이름 갱신, 경주마 공용 구독.
3. **조회 지연:** 공개 뉴스 짧은 응답 캐시, 리더보드 공용/개인 응답 경계 유지, 만료 값 선표시·백그라운드 갱신, 실패 backoff, 해시 정적 파일 장기 캐시.
4. **운영 관리:** 캐시 용량 제한, 과거 데이터 증분 수집, 환율 정책 분리, hit/miss와 p95 계측. 다중 인스턴스 중복 비용을 측정한 뒤 공유 메모리 캐시 필요성을 판단.

현재 DB에 저장된 뉴스·리더보드 결과와 상한이 있는 캐시를 활용해 개선할 수 있다. Redis 도입 자체가 선행 조건은 아니다.

## 재현 자료

| 자료 | 검증 내용 |
|---|---|
| [verify_backend_cache.py](verify_backend_cache.py) / [결과](backend-cache-results.json) | 외부 호출을 막은 7개 현행 캐시 동작 |
| [verify_client_cache.mjs](verify_client_cache.mjs) / [결과](client-cache-results.json) | 모의 저장소/API로 5개 현행 동작 |
| [verify_agents_account_browser.py](verify_agents_account_browser.py) / [결과](agents-account-browser-result.json) | 로컬 프런트 빌드와 모의 API에서 계정 전환 |
| [verify_unlock_account_cache.mjs](verify_unlock_account_cache.mjs) / [결과](unlock-account-cache-result.json) | 잠금 해제 응답 도착 전 계정 전환 |
| [verify_profile_comment_snapshot.py](verify_profile_comment_snapshot.py) / [결과](profile-comment-snapshot-result.json) | 임시 SQLite에서 댓글 작성자 이름 갱신 누락 |
| [verify_private_headers.py](verify_private_headers.py) / [결과](private-headers.json) | 가짜 회원/저장소를 사용하는 실제 ASGI 응답 헤더 |
| [verify_http_headers.py](verify_http_headers.py) / [결과](http-headers.json) | 로컬 공개 API와 운영 HTML·정적 파일의 읽기 전용 헤더 확인 |

Python 스크립트는 백엔드 의존성이 설치된 환경, Node 스크립트는 Node 24를 사용했다. 브라우저 검증은 Playwright·Chromium과 현재 프런트 빌드가 필요하며 `FRONTEND_BUILD`, `BROWSER_EXECUTABLE_PATH`로 지정한다. HTTP 실측 스크립트만 외부 공개 정적 파일에 접근한다. 결함 재현 스크립트의 assertion은 결함을 수정한 뒤 변경해야 한다.
