"""실행기가 서버와 같은 판단 코드를 집어 오는지.

이 시험이 깨지면 실행기는 **엔진 없이** 빌드되거나, 더 나쁘게는 자기만의 사본을 쓰게 된다.
그 순간 백테스트 결과와 실거래가 갈린다.
"""
import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
REPO_ROOT = RUNNER_DIR.parent


class SharedEngineTest(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("shared_engine")

    def test_엔진을_backend_에서_가져온다(self):
        """사본이 아니라 서버가 쓰는 바로 그 모듈이어야 한다."""
        self.assertEqual(self.mod.Macro.__module__, "engine.schema")
        source = Path(sys.modules["engine.schema"].__file__).resolve()
        self.assertEqual(source, (REPO_ROOT / "backend" / "engine" / "schema.py").resolve(),
                         "engine 이 backend/engine 이 아닌 곳에서 왔다 — 사본이 생겼나?")

    def test_실거래_판단에_필요한_것이_다_있다(self):
        for name in ("Macro", "RuleType", "StrategyDriver", "Leg", "make_candle_sim",
                     "strategy_levels", "sim_state", "sim_strategy_state", "Fill"):
            self.assertTrue(hasattr(self.mod, name), f"{name} 이 없다")

    def test_매크로_파일을_실제로_읽는다(self):
        """서버가 내려준 .ggm.json 과 같은 모양을 파싱할 수 있어야 한다."""
        macro = self.mod.Macro(
            exchange="bithumb", symbol="KRW-AZIT", rule_type="I", position_side="long",
            candle_interval="1h", market="spot", leverage=1,
            params={"k": 0.6, "exit_mode": "trailing", "trail_percent": 2.0,
                    "ma_filter_period": 20, "initial_capital": 40000.0},
        )
        self.assertEqual(macro.rule_type, self.mod.RuleType.I)
        self.assertIsNotNone(self.mod.make_candle_sim(macro))

    def test_번들에서는_경로를_건드리지_않는다(self):
        """``sys.frozen`` 이면 묶인 engine 을 써야 한다 — 끼워 넣으면 엉뚱한 파일을 읽는다."""
        before = list(sys.path)
        sys.frozen = True  # type: ignore[attr-defined]
        try:
            self.assertFalse(self.mod.bootstrap_import_path())
            self.assertEqual(sys.path, before)
        finally:
            del sys.frozen  # type: ignore[attr-defined]

    def test_무거운_의존성을_끌어오지_않는다(self):
        """pandas · numpy 가 딸려 오면 번들이 수십 MB 불어난다. 별도 프로세스로 확인한다."""
        heavy = ["pandas", "numpy", "fastapi", "sqlmodel", "sqlalchemy"]
        code = (
            "import sys, json; sys.path.insert(0, %r); import shared_engine;"
            "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules} & set(%r))))"
            % (str(RUNNER_DIR), heavy)
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, check=True, cwd=str(REPO_ROOT))
        self.assertEqual(out.stdout.strip().splitlines()[-1], "[]",
                         "실행기가 서버 전용 의존성을 끌어왔다")

    def test_빌드_설정이_엔진_경로를_가리킨다(self):
        """pathex 나 --paths 가 빠지면 빌드는 성공하고 실행기는 기동에 실패한다."""
        for spec in RUNNER_DIR.glob("*.spec"):
            self.assertIn("../backend", spec.read_text(encoding="utf-8"),
                          f"{spec.name} 의 pathex 에 backend 가 없다")
        workflow = (REPO_ROOT / ".github/workflows/runner-release.yml").read_text(encoding="utf-8")
        self.assertIn("--paths ../backend", workflow,
                      "릴리스 워크플로는 .spec 을 쓰지 않고 명령줄로 빌드한다")

    def test_엔진_의존성이_requirements_에_적혀_있다(self):
        text = (RUNNER_DIR / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pydantic", text, "엔진이 pydantic 을 쓴다 — 빠지면 번들이 기동하지 못한다")

    def test_실행기가_엔진_사본을_두지_않는다(self):
        """runner/ 안에서 매매 판단을 다시 구현하면 이 다리가 무의미해진다."""
        shared = {"Macro", "StrategyDriver", "RuleType", "Leg", "Fill"}
        for path in RUNNER_DIR.glob("*.py"):
            if path.name in ("shared_engine.py", Path(__file__).name):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            defined = {n.name for n in ast.walk(tree)
                       if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
            self.assertFalse(shared & defined,
                             f"{path.name} 이 {shared & defined} 를 따로 정의한다 — "
                             "shared_engine 에서 가져다 써라")


if __name__ == "__main__":
    unittest.main()
