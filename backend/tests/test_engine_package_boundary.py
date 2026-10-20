"""엔진 패키지의 경계 — 판단 코드가 서버 인프라와 섞이지 않게.

``engine`` 은 **판단의 단일 소유자**다. 백테스트도, 서버 실시간도, (언젠가) Rust 실행기가
맞춰야 할 계약(tests/fixtures/engine_golden.json)도 전부 여기서 나온다. 그러려면 둘이 참이어야 한다:

1. ``engine`` 이 ``app`` 을 거꾸로 참조하지 않는다 — 참조하는 순간 판단을 읽으려면 DB · 설정 ·
   웹 프레임워크를 다 들고 와야 하고, 계약을 만들 때마다 서버를 띄워야 한다.
2. 실거래 판단 경로를 import 해도 pandas · numpy 가 딸려 오지 않는다 — 그 둘은 백테스트 ·
   리포트(서버 전용)의 도구다. 판단 경로에 섞이면 "무엇이 판단이고 무엇이 분석인가" 의
   경계가 사라진다.

이 시험이 깨지면 깨진 쪽을 고치지 말고 **왜 그 import 가 필요한지** 부터 보라 — 대개 서버
전용 코드가 엔진으로 흘러든 것이다.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"

# 실행기가 실제로 쓰는 모듈 — 봉 판정과 주문 결정에 필요한 전부.
LIVE_PATH = [
    "engine.version",
    "engine.schema",
    "engine.exchanges",
    "engine.leverage",
    "engine.stepper",
    "engine.candles",
    "engine.driver",
    "engine.live_levels",
    "engine.entry_filter",
    "engine.bundle",
]

# 서버에만 있는 무거운 의존성. 실거래 경로가 끌어오면 안 된다.
SERVER_ONLY = ("pandas", "numpy", "fastapi", "sqlmodel", "sqlalchemy", "app")


def _module_files() -> list[Path]:
    files = sorted(ENGINE_DIR.glob("*.py"))
    assert files, f"{ENGINE_DIR} 에 파이썬 파일이 없다 — 경로가 바뀌었나?"
    return files


def _imported_roots(path: Path) -> set[str]:
    """``path`` 가 import 하는 최상위 모듈 이름들(함수 안의 지연 import 포함)."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_engine_does_not_import_the_server(path: Path) -> None:
    assert "app" not in _imported_roots(path), (
        f"{path.name} 이 app 을 참조한다 — 엔진은 서버를 모른다. "
        "서버 전용 코드라면 backend/app/ 으로 옮겨라."
    )


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_engine_uses_no_parent_relative_imports(path: Path) -> None:
    """``from ..x import`` 는 engine 이 backend/ 밑에 있다는 가정이다 — 실행기엔 그 위가 없다."""
    source = ast.parse(path.read_text(encoding="utf-8"), str(path))
    offenders = [
        f"{'.' * node.level}{node.module or ''}"
        for node in ast.walk(source)
        if isinstance(node, ast.ImportFrom) and node.level > 1
    ]
    assert not offenders, f"{path.name}: 상위 상대 import {offenders} — engine 바깥을 가리킨다"


def test_live_path_pulls_in_nothing_heavy() -> None:
    """별도 프로세스에서 실거래 경로만 import 해 본다 — 이 시험 과정이 이미 읽어 둔 모듈에 속지 않게."""
    code = (
        "import sys, json;"
        f"[__import__(m) for m in {LIVE_PATH!r}];"
        f"print(json.dumps(sorted({{m.split('.')[0] for m in sys.modules}} & set({SERVER_ONLY!r}))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ENGINE_DIR.parent,
        capture_output=True,
        text=True,
        check=True,
    )
    heavy = out.stdout.strip().splitlines()[-1]
    assert heavy == "[]", (
        f"실거래 판단 경로가 {heavy} 를 끌어왔다 — 판단과 분석의 경계가 무너졌다. "
        "engine/__init__.py 의 지연 import(_LAZY) 가 풀렸는지 확인하라."
    )


def test_live_path_needs_only_pydantic() -> None:
    """바깥 의존성은 pydantic 하나 — 실행기 requirements 에 더 넣을 게 없어야 한다."""
    allowed = {"pydantic", "__future__"}
    stdlib = set(sys.stdlib_module_names)
    extra: dict[str, set[str]] = {}
    for name in LIVE_PATH:
        path = ENGINE_DIR / f"{name.split('.')[1]}.py"
        outside = {
            root
            for root in _imported_roots(path)
            if root not in allowed and root not in stdlib and root != "engine"
        }
        if outside:
            extra[path.name] = outside
    assert not extra, f"실거래 경로가 pydantic 말고 다른 것을 쓴다: {extra}"
