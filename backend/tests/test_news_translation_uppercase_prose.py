"""Uppercase trading sentences and company suffixes are not arbitrary tickers."""
import json

import pytest

from app import news


CFG = "✅ $CFG SWEPT TP1 AND TP2 IN THE SAME DAILY CANDLE — +6.16% to the higher of them"
CFG_KO = "✅ $CFG, 같은 일봉에서 TP1과 TP2 모두 달성 — 더 높은 목표까지 +6.16%"
CAPITAL_B = "Capital B Buys 376 Bitcoin for €25.3 Million, Lifting Treasury to 3,521 BTC: 10 outlets compared"
CAPITAL_B_KO = "캐피털 B, 2,530만 유로에 비트코인 376개 매수해 보유량 3,521 BTC로 확대: 매체 10곳 비교"


@pytest.mark.parametrize("original, translated", [(CFG, CFG_KO), (CAPITAL_B, CAPITAL_B_KO)])
def test_actual_failed_title_accepts_complete_korean_translation(original, translated):
    payload = json.dumps({"items": [{"id": news._title_translation_id(original), "title_ko": translated}]})
    assert news._parse_korean_title_translations(payload, [original]) == {original: translated}


def test_lowercase_tail_does_not_create_tickers_from_uppercase_prose():
    for original in (CFG, CFG.split(" to the higher")[0]):
        assert news._translation_protected_upper_tokens(original) == ("CFG", "TP1", "TP2")
    assert news._valid_title_translation(CFG, CFG_KO)


@pytest.mark.parametrize("original, translated", [
    ("$CFG CLOSED WITH GAINS IN DAILY CANDLES — this is a recap", "$CFG 일봉 상승 마감 — 요약"),
    ("XYZQ MOVED TO THE SAME DAILY TARGET — this is a recap", "XYZQ 같은 일봉 목표로 이동 — 요약"),
])
def test_alternate_grammar_shapes_translate_prose_and_preserve_unknown_subject(original, translated):
    assert news._valid_title_translation(original, translated)
    if original.startswith("XYZQ"):
        assert "XYZQ" in news._translation_protected_upper_tokens(original)
        assert not news._valid_title_translation(original, translated.replace("XYZQ", ""))
        assert not news._valid_title_translation(original, translated.replace("XYZQ", "BTC"))


@pytest.mark.parametrize("before, after", [
    ("CFG", "BTC"), ("CFG", ""), ("TP1", "TP3"), ("TP2", ""),
    ("6.16%", "6.61%"), ("6.16%", "6.16"),
])
def test_uppercase_prose_detection_does_not_relax_assets_targets_or_numbers(before, after):
    assert not news._valid_title_translation(CFG, CFG_KO.replace(before, after))


@pytest.mark.parametrize("original, identifier", [
    ("$XYZQ SWEPT TP1 AND THE SAME DAILY CANDLE — trailing explanation", "XYZQ"),
    ("XYZQ SWEPT TP1 AND THE SAME DAILY CANDLE — trailing explanation", "XYZQ"),
    ("THE XYZQ TOKEN MOVED AND THE DAILY CANDLE CLOSED — trailing explanation", "XYZQ"),
    ("CHECK (XYZQ) AND THE SAME DAILY CANDLE — trailing explanation", "XYZQ"),
    ("USE CODE XYZQ AND THE SAME DAILY CANDLE — trailing explanation", "XYZQ"),
    ("BTC AND THE ETH ETF OUTLOOK — trailing explanation", "ETF"),
    ("BTC AND THE ETH ETF OUTLOOK — trailing explanation", "ETH"),
])
def test_explicit_assets_acronyms_and_identifiers_survive_uppercase_sentences(original, identifier):
    assert identifier in news._translation_protected_upper_tokens(original)


def test_ordinary_unknown_tickers_and_short_abbreviations_keep_existing_protection():
    assert news._valid_title_translation("XYZQ rallies 3%", "XYZQ 3% 상승")
    assert not news._valid_title_translation("XYZQ rallies 3%", "BTC 3% 상승")
    assert "TA" in news._translation_protected_upper_tokens("FIL Price Filecoin TA FIL Technical Analysis")


def test_target_identifiers_do_not_reclassify_quarter_labels_as_tickers():
    original = "BTC HITS TP1 AND TP2 IN THE Q3 REPORT — quarterly results"
    assert "TP1" in news._translation_protected_upper_tokens(original)
    assert "TP2" in news._translation_protected_upper_tokens(original)
    assert "Q3" not in news._translation_protected_upper_tokens(original)


def test_company_single_letter_suffix_requires_the_full_company_context():
    assert news._translation_protected_upper_tokens(CAPITAL_B) == ("B", "BTC")
    assert "B" not in news._translation_protected_upper_tokens("A report mentions B today")
    assert "B" in news._translation_protected_upper_tokens("$B token rallies")
    assert "B" in news._translation_protected_upper_tokens("B token rallies")


@pytest.mark.parametrize("before, after", [
    ("B,", "C,"), ("B,", ","), ("2,530만", "2,531만"), ("유로", "달러"),
    ("376개", "377개"), ("3,521 BTC", "3,520 BTC"), ("BTC", "ETH"), ("10곳", "11곳"),
])
def test_company_suffix_handling_preserves_company_currency_and_every_quantity(before, after):
    assert not news._valid_title_translation(CAPITAL_B, CAPITAL_B_KO.replace(before, after))
