"""질문 그래프 — 순수 데이터. 모든 경로가 유효한 폼을 내는지는 프런트 전수 시험이 본다."""
import pytest

from app.coach_graph import (
    FIRST_KEY, GRAPH, PAGE_SIZE, choices_for, defaults_for_remaining, next_key, patch_for, to_json,
)

ALL_KEYS = ("goal", "risk", "watch", "rule", "symbols", "weights", "period",
            "capital", "entry_filter", "bundle_risk")


def test_first_key_is_goal():
    assert FIRST_KEY == "goal"


def test_every_key_is_in_the_graph():
    assert set(GRAPH) == set(ALL_KEYS)


def test_choice_nodes_have_three_to_five_visible_options():
    """한 번에 보여 주는 것은 다섯 개까지 — 더 있으면 '다른 선택지 보기' 로 넘긴다."""
    for key, q in GRAPH.items():
        if q.kind != "choice":
            continue
        assert len(q.choices) >= 3, key
        assert len(q.choices[:PAGE_SIZE]) <= 5, key


def test_choice_values_are_unique_per_node():
    for key, q in GRAPH.items():
        values = [c.value for c in q.choices]
        assert len(values) == len(set(values)), key


def test_labels_are_short_enough_for_the_panel():
    """패널은 고정 폭 340px 다 — 라벨이 길면 잘린다(1차 교훈)."""
    for key, q in GRAPH.items():
        for c in q.choices:
            assert len(c.label) <= 28, (key, c.value, c.label)


def test_patch_keys_are_flat_form_keys():
    """패치는 평평한 폼 키만 담는다 — 프런트가 setForm 한 번으로 끝내야 한다."""
    for key, q in GRAPH.items():
        for c in q.choices:
            for k, v in c.patch.items():
                assert "." not in k and not isinstance(v, dict), (key, c.value, k)


# --- 흐름 -------------------------------------------------------------
def test_flow_reaches_the_end():
    answers, seen, key = {}, [], FIRST_KEY
    while key is not None:
        assert key not in seen, f"같은 질문을 두 번 묻는다: {key}"
        seen.append(key)
        q = GRAPH[key]
        answers[key] = choices_for(key, answers)[0].value if q.kind == "choice" else _input_for(key)
        key = next_key(answers)
    assert "rule" in seen and "symbols" in seen


def _input_for(key):
    return {"symbols": "BTCUSDT", "period": "1y", "capital": "1000"}[key]


def test_weights_is_skipped_for_a_single_symbol():
    answers = {"goal": GRAPH["goal"].choices[0].value, "risk": GRAPH["risk"].choices[0].value,
               "watch": GRAPH["watch"].choices[0].value}
    answers["rule"] = choices_for("rule", answers)[0].value
    answers["symbols"] = "BTCUSDT"
    assert next_key(answers) != "weights"


def test_weights_is_asked_for_two_symbols():
    answers = {"goal": GRAPH["goal"].choices[0].value, "risk": GRAPH["risk"].choices[0].value,
               "watch": GRAPH["watch"].choices[0].value}
    answers["rule"] = choices_for("rule", answers)[0].value
    answers["symbols"] = "BTCUSDT, ETHUSDT"
    assert next_key(answers) == "weights"


def test_bundle_risk_is_skipped_for_a_single_symbol():
    answers = {k: GRAPH[k].choices[0].value for k in ("goal", "risk", "watch")}
    answers["rule"] = choices_for("rule", answers)[0].value
    answers.update({"symbols": "BTCUSDT", "period": "1y", "capital": "1000",
                    "entry_filter": choices_for("entry_filter", answers)[0].value})
    assert next_key(answers) != "bundle_risk"


# --- rule 노드가 앞 답에서 좁혀진다 -----------------------------------
def test_rule_choices_narrow_from_earlier_answers():
    """좁혀지지 않으면 코치가 아니라 목록이다 — 서로 다른 답에서 서로 다른 후보가 나와야 한다."""
    base = {"risk": GRAPH["risk"].choices[0].value, "watch": GRAPH["watch"].choices[0].value}
    seen = set()
    for goal in GRAPH["goal"].choices:
        got = tuple(c.value for c in choices_for("rule", {**base, "goal": goal.value}))
        assert 3 <= len(got) <= 5, (goal.value, got)
        seen.add(got)
    assert len(seen) > 1, "goal 을 바꿰도 규칙 후보가 그대로다 — 좁히지 않고 있다"


def test_rule_choices_are_valid_rule_types():
    from app.engine.schema import RuleType
    valid = {r.value for r in RuleType}
    for goal in GRAPH["goal"].choices:
        for risk in GRAPH["risk"].choices:
            for watch in GRAPH["watch"].choices:
                answers = {"goal": goal.value, "risk": risk.value, "watch": watch.value}
                for c in choices_for("rule", answers):
                    assert c.value in valid, c.value


# --- 패치 -------------------------------------------------------------
def test_patch_for_choice_returns_that_choice_patch():
    q = GRAPH["risk"]
    c = q.choices[0]
    assert patch_for("risk", c.value, {}) == c.patch


def test_patch_for_unknown_answer_raises():
    with pytest.raises(ValueError):
        patch_for("risk", "존재하지않는값", {})


def test_patch_for_input_node_fills_its_field():
    assert patch_for("capital", "2500", {}) == {"initial_capital": 2500}
    assert patch_for("symbols", "BTCUSDT, ETHUSDT", {}) == {"symbol": "BTCUSDT, ETHUSDT"}
    assert patch_for("period", "3m", {}) == {"preset": "3m"}


def test_capital_rejects_a_non_number():
    with pytest.raises(ValueError):
        patch_for("capital", "스물", {})


# --- 마무리(턴 상한) --------------------------------------------------
def test_defaults_for_remaining_fills_every_unanswered_node():
    answers = {"goal": GRAPH["goal"].choices[0].value}
    patch = defaults_for_remaining(answers)
    assert patch, "남은 칸이 있는데 기본값을 안 냈다"
    assert "rule_type" in patch or "symbol" in patch


def test_defaults_for_remaining_is_empty_when_everything_is_answered():
    answers = {k: GRAPH[k].choices[0].value for k in GRAPH if GRAPH[k].kind == "choice"}
    answers.update({"symbols": "BTCUSDT", "period": "1y", "capital": "1000"})
    assert defaults_for_remaining(answers) == {}


# --- 내보내기 ---------------------------------------------------------
def test_to_json_is_serializable_and_complete():
    import json
    data = to_json()
    json.dumps(data, ensure_ascii=False)       # 터지지 않아야 한다
    assert data["first"] == FIRST_KEY
    assert set(data["nodes"]) == set(ALL_KEYS)
    node = data["nodes"]["risk"]
    assert node["kind"] == "choice"
    assert all({"value", "label", "patch", "why"} <= set(c) for c in node["choices"])


# =======================================================================
# 아래는 브리프 밖에서 더한 시험 — 패치가 실제 폼 · 서버 스키마와 맞는지 본다.
# =======================================================================
import itertools
import json
import shutil
import subprocess
from pathlib import Path

from app.coach_graph import CAPITAL_SCALED, FILTERABLE, ORDER, _TYPE_DEFAULTS

_REPO = Path(__file__).resolve().parents[2]
_FIRSTS = {k: [c.value for c in GRAPH[k].choices] for k in ("goal", "risk", "watch")}


def _all_choices():
    """(자리, 선택지 dict) — rule · weights 는 펼친 조합까지 전부."""
    for key, node in to_json()["nodes"].items():
        for c in node["choices"]:
            yield key, c
        for combo, rows in node.get("by", {}).items():
            for c in rows:
                yield f"{key}[{combo}]", c


def _frontend():
    """macro.js 의 defaultForm() 과 TYPE_DEFAULTS. node 가 없으면 시험을 건너뛴다."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 가 없다")
    code = ("import {defaultForm, TYPE_DEFAULTS} from './src/lib/macro.js';"
            "console.log(JSON.stringify({form: defaultForm(), td: TYPE_DEFAULTS}));")
    out = subprocess.run([node, "--input-type=module", "-e", code], cwd=_REPO / "frontend",
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_every_patch_key_is_a_real_form_key():
    """짐작한 키 이름이 폼에 없으면 setForm 이 조용히 새 칸을 만든다 — 실제 defaultForm 과 맞춘다."""
    form = _frontend()["form"]
    for where, c in _all_choices():
        for k in c["patch"]:
            assert k in form, (where, c["value"], k)
    for rows in to_json()["capital_scaled"].values():
        for row in rows:
            assert row["key"] in form, row


def test_rule_patch_carries_the_type_defaults_exactly():
    """TYPE_DEFAULTS 를 옮겨 적다 틀리면 폼에 이전 규칙의 값이 남는 것과 같다."""
    fe = _frontend()
    for rule, ours in _TYPE_DEFAULTS.items():
        if rule in fe["td"]:
            assert ours == fe["td"][rule], rule
        else:                                    # TYPE_DEFAULTS 에 없는 A · C 는 defaultForm 의 자기 칸
            for k, v in ours.items():
                assert fe["form"][k] == v, (rule, k)


def test_filterable_matches_the_server():
    from app.engine.schema import FILTERABLE_TYPES
    assert FILTERABLE == {r.value for r in FILTERABLE_TYPES}


def test_all_labels_fit_including_narrowed_nodes():
    for where, c in _all_choices():
        assert len(c["label"]) <= 28, (where, c["label"])
        assert c["why"], (where, c["value"])


def test_to_json_expands_every_rule_combination():
    data = to_json()
    by = data["nodes"]["rule"]["by"]
    assert len(by) == 5 * 4 * 3
    for goal, risk, watch in itertools.product(*_FIRSTS.values()):
        rows = by[f"{goal}|{risk}|{watch}"]
        assert 3 <= len(rows) <= 5
        want = choices_for("rule", {"goal": goal, "risk": risk, "watch": watch})
        assert [r["value"] for r in rows] == [c.value for c in want]
    assert set(data["nodes"]["weights"]["by"]) == {"2", "3", "4", "5"}


def test_rule_patch_resets_stale_values_and_keeps_rules_legal():
    seen = set()
    for _, c in _all_choices():
        patch = c["patch"]
        if "rule_type" not in patch:
            continue
        rule = patch["rule_type"]
        seen.add(rule)
        assert patch["position_side"] == "long" and patch["leverage"] == 1
        for k in _TYPE_DEFAULTS[rule]:
            assert k in patch, (rule, k)
        if rule not in FILTERABLE:
            assert patch["use_entry_filter"] is False and patch["use_bundle_risk"] is False
        if rule == "C":
            assert patch["candle_interval"] == "1d"
    assert seen == set(_TYPE_DEFAULTS), "모든 규칙이 어느 조합에선가 나와야 한다(B · D 는 제외)"


def _model_input(patch, model):
    """폼 키 -> 규칙 params. 빈 칸("")은 서버가 받는 None."""
    return {k: (None if v == "" else v) for k, v in patch.items() if k in model.model_fields}


def test_every_rule_patch_validates_against_the_server_params():
    from app.engine.schema import _PARAMS_MODEL
    for rule, model in _PARAMS_MODEL.items():
        if rule.value not in _TYPE_DEFAULTS:
            continue
        patch = {**_TYPE_DEFAULTS[rule.value],
                 **patch_for("capital", "1000000", {"rule": rule.value})}
        model(**_model_input(patch, model))


def test_capital_scaling_keeps_martingale_affordable_for_every_split():
    """H 는 최악의 필요 자금이 (자금 x 레그 몫 x 투입 비율) 안이어야 서버가 받는다."""
    from app.engine.schema import ParamsH
    ratios = {c.value: c.patch["invest_ratio_pct"] / 100 for c in GRAPH["risk"].choices}
    # (종목, 비중 답, 가장 작은 레그의 몫)
    splits = [("BTCUSDT", None, 1.0), ("BTCUSDT, ETHUSDT", "even", 0.5),
              ("BTCUSDT, ETHUSDT", "lead", 0.4), ("A1, B2, C3, D4, E5", "even", 0.2),
              ("A1, B2, C3, D4, E5", "lead", 0.15)]
    for risk, ratio in ratios.items():
        for symbols, weights, share in splits:
            for capital in ("1", "1000", "2500.5", "1000000", "123456789"):
                answers = {"risk": risk, "symbols": symbols, "rule": "H"}
                if weights:
                    answers["weights"] = weights
                patch = {**_TYPE_DEFAULTS["H"], **patch_for("capital", capital, answers)}
                budget = float(patch["initial_capital"]) * share * ratio
                need = ParamsH(**_model_input(patch, ParamsH)).required_funds()
                assert need <= budget + 1e-9, (risk, symbols, weights, capital, need, budget)


def test_capital_scaling_for_dca_is_a_fraction_of_the_leg_capital():
    assert patch_for("capital", "100000", {"rule": "C", "symbols": "BTCUSDT"}) == \
        {"initial_capital": 100000, "amount_per_buy": 5000.0}
    two = patch_for("capital", "100000", {"rule": "C", "symbols": "BTCUSDT, ETHUSDT"})
    assert two["amount_per_buy"] == 2500.0
    assert patch_for("capital", "100000", {"rule": "E"}) == {"initial_capital": 100000}
    assert set(CAPITAL_SCALED) == {"C", "H"}


def test_symbols_are_normalised_and_validated():
    assert patch_for("symbols", " btcusdt ,ETHUSDT, BTCUSDT", {}) == {"symbol": "BTCUSDT, ETHUSDT"}
    for bad in ("", " , ", "A1,B2,C3,D4,E5,F6", "BTC USDT!", None, 5):
        with pytest.raises(ValueError):
            patch_for("symbols", bad, {})


def test_names_that_dedupe_to_one_symbol_do_not_ask_for_weights():
    answers = {"goal": "steady", "risk": "tight", "watch": "daily", "rule": "E",
               "symbols": "BTCUSDT, btcusdt"}
    assert next_key(answers) == "period"


def test_capital_rejects_bad_numbers():
    for bad in ("0", "-5", "nan", "inf", "1e99", "", True, None):
        with pytest.raises(ValueError):
            patch_for("capital", bad, {})
    assert patch_for("capital", "1,000", {}) == {"initial_capital": 1000}
    assert patch_for("capital", 12.5, {}) == {"initial_capital": 12.5}


def test_period_accepts_only_the_listed_presets():
    for ok in ("1y", "6m", "3m", "1m", "1w"):
        assert patch_for("period", ok, {}) == {"preset": ok}
    for bad in ("custom", "1d", "2y", ""):
        with pytest.raises(ValueError):
            patch_for("period", bad, {})


def test_rule_answer_outside_the_narrowed_list_is_refused():
    """위변조 차단 — 좁혀진 후보에 없는 규칙은 (유효한 규칙이어도) 받지 않는다."""
    answers = {"goal": "steady", "risk": "tight", "watch": "daily"}
    for bad in ("K", "B", "D", "Z"):
        with pytest.raises(ValueError):
            patch_for("rule", bad, answers)
    with pytest.raises(ValueError):
        choices_for("rule", {**answers, "goal": "존재하지않는값"})


def test_rough_rules_only_reach_people_who_accept_risk():
    base = {"goal": "dip", "watch": "daily"}
    tight = {c.value for c in choices_for("rule", {**base, "risk": "tight"})}
    wide = {c.value for c in choices_for("rule", {**base, "risk": "wide"})}
    assert "H" not in tight and "H" in wide


def test_dca_is_offered_only_to_people_who_look_daily():
    for watch in ("hours", "often"):
        got = {c.value for c in choices_for("rule", {"goal": "steady", "risk": "tight", "watch": watch})}
        assert "C" not in got


def test_entry_filter_patches_are_valid_filters():
    from app.engine.schema import EntryFilter
    for c in GRAPH["entry_filter"].choices:
        if not c.patch["use_entry_filter"]:
            continue
        kind = c.patch["filter_kind"]
        raw = {k[len(f"filter_{kind}_"):]: (None if v == "" else v)
               for k, v in c.patch.items() if k.startswith(f"filter_{kind}_")}
        params = {"ma": lambda r: {"ma_type": r["type"], "period": r["period"], "side": r["side"]},
                  "rsi": lambda r: {"period": r["period"], "min": r["min"], "max": r["max"]},
                  "bb": lambda r: {"period": r["period"], "num_std": r["num_std"], "zone": r["zone"]},
                  }[kind](raw)
        EntryFilter(kind=kind, params=params)


def test_bundle_risk_patches_are_valid_for_every_symbol_count():
    from app.engine.schema import BundleRisk
    for c in GRAPH["bundle_risk"].choices:
        if not c.patch["use_bundle_risk"]:
            continue
        mp, ex = c.patch["bundle_max_positions"], c.patch["bundle_max_exposure_pct"]
        BundleRisk(max_positions=None if mp == "" else mp, max_exposure_pct=None if ex == "" else ex)
        assert mp == "" or mp < 2          # 종목 수(2 이상)보다 작아야 한다


def test_lead_weights_sum_to_100():
    for n, rows in to_json()["nodes"]["weights"]["by"].items():
        lead = next(c for c in rows if c["value"] == "lead")["patch"]["leg_weights"]
        parts = [float(x) for x in lead.split(",")]
        assert len(parts) == int(n) and abs(sum(parts) - 100) < 0.01 and parts[0] == max(parts)


def test_every_walk_ends_and_never_repeats_a_question():
    """모든 (목적 · 위험 · 확인 · 규칙 · 종목 수) 조합을 걷는다 — 끝나고, 되묻지 않고, 건너뛰기가 지켜진다."""
    for goal, risk, watch in itertools.product(*_FIRSTS.values()):
        base = {"goal": goal, "risk": risk, "watch": watch}
        for rule in choices_for("rule", base):
            for syms in ("BTCUSDT", "BTCUSDT, ETHUSDT", "A1, B2, C3, D4, E5"):
                answers, seen = {**base, "rule": rule.value, "symbols": syms}, []
                key = next_key(answers)
                while key is not None:
                    assert key not in seen
                    seen.append(key)
                    q = GRAPH[key]
                    answers[key] = (choices_for(key, answers)[-1].value
                                    if q.kind in ("choice", "period") else "1000")
                    key = next_key(answers)
                multi = len(syms.split(",")) > 1
                assert ("weights" in seen) == multi
                assert ("entry_filter" in seen) == (rule.value in FILTERABLE)
                assert ("bundle_risk" in seen) == (multi and rule.value in FILTERABLE)


def test_defaults_for_remaining_after_each_early_stop_is_complete():
    """어디서 멈춰도 남은 칸이 메꿔진다 — 규칙 · 종목 · 기간 · 자금이 전부 들어 있다."""
    for stop in range(len(ORDER) + 1):
        answers = {}
        for key in ORDER[:stop]:
            if next_key(answers) != key:
                continue
            q = GRAPH[key]
            answers[key] = (choices_for(key, answers)[0].value if q.kind in ("choice", "period")
                            else {"symbols": "BTCUSDT", "capital": "1000"}[key])
        merged = {}
        for key, ans in answers.items():
            merged.update(patch_for(key, ans, {k: v for k, v in answers.items() if k != key}))
        merged.update(defaults_for_remaining(answers))
        for need in ("rule_type", "symbol", "preset", "initial_capital", "use_stop_loss", "candle_interval"):
            assert need in merged, (stop, need)
