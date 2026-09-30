# OpenAI 전환 (2026-09-30)

모든 앱 AI 요청은 `app/ai_runtime.py`의 공식 OpenAI SDK Responses API를 사용합니다.
기본 모델은 `gpt-6-luna`, 추론 수준은 `max`로 고정합니다. Gemini/Anthropic 키로 돌아가는 대체 경로는 없습니다.
키는 서버의 `OPENAI_API_KEY` 하나만 사용하며 소스·브라우저·로그에 포함하지 않습니다.

## 호출 목록

| 기능 | 호출 파일 | purpose |
| --- | --- | --- |
| 뉴스 제목 번역·검증 후 교정 | `app/news.py` | `title_translation` |
| 시장 브리핑 | `app/news.py` | `market_news_summary` |
| 커뮤니티 본문 요약 | `app/community_summaries.py` | `community_summaries` |
| 에이전트 뉴스 분류·본문 요약 | `app/agent_features/position_news/classifier.py` | `position_news` |
| 백테스트 해설 | `app/ai_explain.py` | `ai_explain` |
| 일일 챌린지 | `app/ai_challenge.py` | `ai_challenge` |
| 매크로 제안 | `app/ask.py` | `ask` |
| 매크로 후보 제안 | `app/ask.py` | `ask-candidates` |

크롤링·RSS·거래소 API·규칙 기반 fallback은 생성형 AI API가 아니며 변경하지 않습니다.
`tests/test_openai_migration.py`가 전체 앱을 검색해 호출 목록과 구 공급자 키·SDK 잔존 여부를 검사합니다.

## 비용과 실패 처리

- SDK 자동 재시도는 `max_retries=0`. 기존 공통 캐시·동시성 제어와 기능별 한도를 유지합니다.
- 제목 번역(교정 포함)·커뮤니티 요약은 공용 DB의 항목별 누적 실제 요청 **최대 10회**.
  실패·타임아웃도 차감하며 모델 변경·재배포로 초기화하지 않습니다.
- `OPENAI_REASONING_TOKEN_RESERVE=4096`을 기존 답변 토큰 한도에 더합니다.
  Responses의 `max_output_tokens`는 추론+본문 합계이며 요청 전체 상한은 32768입니다.
  추론 수준 `max`는 무제한 출력이나 재시도를 의미하지 않습니다.
- `incomplete` 응답도 usage를 기록하지만 잘린 답변은 반환·캐시하지 않습니다.
- OpenAI의 출력 토큰에는 추론이 이미 포함되므로 두 번 합산하지 않습니다.
  캐시 읽기·쓰기, 장문 요청 할증을 반영합니다. Gemini 과거 사용 기록은 별도 제공자로 보존합니다.
- 실패한 제목은 공개 뉴스·에이전트 피드에 노출하지 않습니다. 미완성 번역을 원문으로 대체하지 않습니다.
- `AI_TIMEOUT_SECONDS=60`; 커뮤니티 요약과 매크로 제안 요청은 기본 45초입니다.

## 배포 조건

Render 웹과 `gg-parrot-position-news` 워커 **모두** 다음을 적용해야 합니다.

```dotenv
OPENAI_API_KEY=<기존 backend/.env의 값, Git 커밋 금지>
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_TOKEN_RESERVE=4096
AI_TIMEOUT_SECONDS=60
```

개별 `GEMINI_*_MAX_TOKENS` 설정은 같은 이름의 `OPENAI_*_MAX_TOKENS`로 옮겨야 합니다.
기존 Gemini 키가 남아 있어도 새 코드에서는 읽지 않습니다.
`newsaiitembudget` 테이블은 시작 시 `init_db()`가 생성하고 RLS를 켭니다.
SQL 배포에는 `supabase/migrations/20260930090000_news_ai_item_call_limit.sql`을 사용할 수 있습니다.
기존 운영 프로세스가 모두 교체돼야 새 공급자와 제한이 적용됩니다. 배포 이전 요청 횟수는 소급 복원되지 않습니다.

## 공식 근거

- [GPT-6 Luna 모델·max 추론·단가](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Responses 추론 토큰과 incomplete 처리](https://developers.openai.com/api/docs/guides/reasoning)

코드·격리 테스트 완료와 Render 운영 배포 완료는 별개입니다. 배포 인증이 없으면 운영 완료로 보고하지 않습니다.
