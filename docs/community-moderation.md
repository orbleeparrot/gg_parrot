# 커뮤니티 비속어 검사

2026-09-10 · `korcen==1.0.3` · 등록을 거절하고 표현 수정을 안내한다.

## 적용 범위

| 입력 | 검사 시점 |
| --- | --- |
| 리더보드 채팅 | 메시지 저장 전 |
| 게시글 제목·본문 | 새 글 등록과 기존 글 수정 |
| 댓글·답글 | 등록과 수정 |

서버의 공통 함수 `app.moderation.require_clean_text`에서 검사한다. API를 직접 호출해도 같은 정책이 적용된다. 거절 응답은 HTTP 400이며, 예를 들어 `댓글에 비속어가 포함되어 있어요. 표현을 수정해 주세요.`를 반환한다. 탐지된 단어 자체는 응답에 포함하지 않는다.

프런트엔드의 기존 오류 표시를 사용한다. 실패 시 입력한 내용은 유지하고, 수정 후 다시 등록할 수 있다. 글 수정 실패 시 기존 제목·본문·이미지를 보존하며, HTML 글 검사 전에 임시로 flush한 새 글·이미지도 최종 commit 없이 rollback된다. 차단된 채팅·댓글은 작성 횟수 제한을 소모하지 않는다.

검사는 쓰기 경로에만 추가했다. 과거 글을 소급 수정하거나 조회할 때 다시 검사하지 않는다. 프로필명·소개글·신고 사유·문의 내용은 이번 적용 범위에 포함하지 않는다.

## 라이브러리 선택

사용자가 제공한 [한국어 비속어 리소스 목록](https://github.com/Tanat05/korean-profanity-resources)을 시작점으로 실제 저장소와 배포 패키지를 확인했다. 다음은 이 Python/FastAPI 서비스에 대한 선택 판단이며, 모든 후보의 탐지 정확도를 같은 데이터셋으로 비교한 결과는 아니다.

| 후보 | 확인한 특징과 판단 |
| --- | --- |
| [korcen](https://github.com/Tanat05/korcen) | Python 키워드 필터, 사용자 정의 사전, 외부 API 없이 실행. 현재 서버에 바로 통합할 수 있어 채택. 검토한 PyPI 1.0.3과 실제 wheel의 MIT 라이선스를 확인했다. |
| [badwordDetection2](https://github.com/0-inf/badwordDetection2) | 키보드·발음·이미지 유사도 필터를 조합하는 방식. 이미지 필터는 OpenCV와 폰트가 필요하다. 이번 작업에서는 통합 복잡성에 비해 교체 이점이 입증되지 않았다. |
| [badword_check](https://github.com/Nam-SW/badword_check) | README의 실행 환경은 Python 3.5–3.7/TensorFlow 2.0이고 모델 로딩이 필요하다. 현재 서버에 바로 넣을 대안으로 채택하지 않았다. |
| [CurseDetector](https://github.com/mangto/CurseDetector) | 한글·발음 유사도 방식. 확인 시점에 릴리스와 명시적 라이선스가 확인되지 않아 서비스 의존성으로 채택하지 않았다. |
| [korcen-kogpt2](https://github.com/Tanat05/korcen-kogpt2) | 실험적 모델 기반 대안. 예제는 TensorFlow 2.10, 모델과 토크나이저 로딩을 요구한다. 모델 운영과 별도 품질 검증 없이 즉시 등록 차단에 도입하지 않았다. |
| 목록의 JS/TS 필터 | Python 서버의 최종 검사에 사용하려면 별도 런타임이나 포팅이 필요하다. 클라이언트에만 넣으면 API 직접 호출을 막을 수 없다. |

리소스 목록의 korcen 라이선스 표기는 실제 저장소·1.0.3 wheel과 달랐다. 배포 패키지의 MIT 라이선스를 기준으로 확인했다. 모델이나 사전을 외부에서 요청하지 않으며 작성한 본문을 외부 서비스로 보내지 않는다.

## 기본 라이브러리에 추가한 처리

- 1.0.3 실측에서 누락된 `씨발`을 `profanity_include.txt`로 보완했다.
- Unicode NFC/NFKC, 보이지 않는 포맷 문자, 문장부호, 한글 사이의 숫자 삽입을 검사한다. 원문은 변경하지 않는다.
- HTML은 이미지 주소 확정 및 정제 후 보이는 텍스트를 검사한다. 인라인 서식으로 단어를 나누거나 HTML 엔티티로 입력한 경우도 검사한다.
- 링크 문구와 이미지 `alt`는 검사하며, `href`·`src` 같은 속성은 본문으로 해석하지 않는다. 일반 URL 문자열은 upstream 정책처럼 제외한다.
- korcen에 회원 ID를 전달하지 않는다. 1.0.3의 ID 옵션은 캐시 설정이 없으면 탐지를 생략하는 동작이 있어 사용하지 않는다.
- 기본 한국어 범주를 사용하며 `foreign=True`는 활성화하지 않는다. 영문 비속어 전반이나 이미지 내부 텍스트를 검사하는 기능은 아니다.

`profanity_exclude.txt`에는 현재 예외가 없다. 사전을 수정하면 서버 재시작이 필요하다. include는 upstream 내부의 예외 처리보다 우선하며, exclude는 정규화된 탐지 표현과 일치하는 경우에 적용된다. 예외를 추가할 때는 정상 문장과 그 문장에 비속어를 함께 넣은 사례를 모두 검증해야 한다.

키워드 기반이므로 문맥을 완전히 판단하지 못한다. 예를 들어 띄어쓰기를 합치는 처리 때문에 `수박씨 발라 먹어요`도 거절될 수 있다. 임의의 모든 우회 표기나 새로운 은어를 잡는다고 보장하지 않는다. 테스트에 포함한 시바이누·코인 티커·매매 용어·숫자·스티커는 정상 통과하며, 오탐이나 누락을 보완할 때 같은 테스트에 실제 사례를 추가한다.

## 검증

격리된 임시 SQLite DB로 다음을 실행해 **101개 통과**했다. 운영·공유 DB를 사용하지 않았다.

```sh
cd backend
python -m pytest -q tests/test_moderation.py tests/test_chat_members.py tests/test_board.py tests/test_board_performance.py tests/test_avatars.py
```

실제 프런트엔드를 빌드하고 모든 API를 로컬 fixture로 대체한 Chromium 검사도 **390px·1440px 모두 통과**했다. 채팅, 댓글 등록·편집, 게시글 등록에서 HTTP 400 안내, 입력 보존, 수정 후 성공을 확인했다.

```sh
cd frontend
npm run build
cd ..
BROWSER_EXECUTABLE_PATH=/path/to/chrome python frontend/tests/communityModerationBrowser.smoke.py
```

정상 매매 문장을 반복한 로컬 Python 3.11 측정에서 공통 검사 함수는 300자 약 1.22ms, 5,000자 약 18.48ms, 20,000자 약 55.23ms였다. 각각 20회씩 3번 측정한 가장 빠른 묶음의 평균이며 서버 전체 응답 시간이나 운영 환경의 지연을 의미하지 않는다. 목록·상세 조회의 추가 DB 쿼리는 없고, 기존 게시판 쿼리 예산 테스트도 통과했다.
