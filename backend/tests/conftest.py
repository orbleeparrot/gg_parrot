"""테스트는 운영 DB·유료 API 에 절대 붙지 않는다.

`app.main` 이 import 되며 `load_dotenv()` 로 backend/.env 를 읽는데, 거기 DATABASE_URL
이 있으면 뒤이어 import 되는 `app.db` 가 그 값으로 Postgres 엔진을 만든다 — 실제로
테스트 픽스처(가짜 날짜 carryover, 'claim-*' 챌린지, u1a2b 계정)가 운영 Supabase 에
쓰였다. load_dotenv 는 이미 있는 키를 덮어쓰지 않으므로, 어떤 app 모듈보다 먼저 빈
값을 박아 두면 SQLite 로 고정된다. SQLite 도 개발용 app.db 가 아니라 세션마다 새
임시 파일을 써서, 이전 실행이 남긴 행이 순서 의존 실패를 만들지 않게 한다.
"""
import os
import tempfile

for _key in ("DATABASE_URL", "ANTHROPIC_API_KEY", "PREFECT_API_URL"):
    os.environ[_key] = ""

_TMP = tempfile.NamedTemporaryFile(prefix="ggp-test-", suffix=".db", delete=False)
_TMP.close()
os.environ["SQLITE_PATH"] = _TMP.name
