"""코치 질문 그래프를 프런트가 읽을 JSON 으로 내보낸다.

프런트 전수 경로 시험이 이 파일을 읽어 '코치가 낼 수 있는 모든 폼이 유효한가' 를 증명한다.
파이썬에 폼 빌더를 한 벌 더 베끼는 대신 이 길을 택했다 — 두 벌은 어긋나는 날이 온다.

쓰기: cd backend && .venv/Scripts/python.exe scripts/dump_coach_graph.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.coach_graph import to_json   # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "coachGraph.generated.json"


def main() -> None:
    OUT.write_text(json.dumps(to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
