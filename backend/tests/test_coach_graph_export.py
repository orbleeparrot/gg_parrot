"""내보낸 그래프 JSON 이 코드와 맞는지 — 어긋나면 프런트 전수 시험이 거짓 안심을 준다."""
import json
import pathlib

from app.coach_graph import to_json

EXPORT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "tests" / "fixtures" / "coachGraph.generated.json"


def test_export_exists():
    assert EXPORT.exists(), f"없다: {EXPORT} — scripts/dump_coach_graph.py 를 돌려라"


def test_export_matches_the_code():
    """그래프를 고치고 내보내기를 안 하면 여기서 걸린다(test_runner_release 와 같은 패턴)."""
    on_disk = json.loads(EXPORT.read_text(encoding="utf-8"))
    assert on_disk == to_json(), \
        "coachGraph.generated.json 이 낡았다 — backend/scripts/dump_coach_graph.py 를 다시 돌려라"
