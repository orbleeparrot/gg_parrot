"""Real Square translations rejected by numeric/identifier validation in v7."""
import json
from pathlib import Path

import pytest

from app import news


CAPTURED = json.loads((Path(__file__).parent / "fixtures/news_square_translation_responses.json").read_text())


@pytest.mark.parametrize("pair", CAPTURED)
def test_captured_korean_response_preserves_the_original_multilingual_facts(pair):
    original, translated = pair["original"], pair["translated"]
    response = json.dumps({"items": [{"id": news._title_translation_id(original), "title_ko": translated}]})
    normalized = news._normalize_title_translation(original, translated)
    assert news._parse_korean_title_translations(response, [original]) == {original: normalized}


@pytest.mark.parametrize("changed", ["0.1306U", "0U", "0.1305", "0.1305USD", "0.1305달러"])
def test_quote_shorthand_never_loses_fraction_or_unit(changed):
    original, translated = CAPTURED[0]["original"], CAPTURED[0]["translated"]
    assert ("0.1305", "literal:u") in news._translation_fact_tokens(original)[0]
    assert ("0", "number") not in news._translation_fact_tokens(original)[0]
    assert not news._valid_title_translation(original, translated.replace("0.1305U", changed))


@pytest.mark.parametrize("original", ["0.1305unrecognized", "0.1305identifier"])
def test_unrecognized_numeric_suffix_does_not_backtrack_to_the_integer_part(original):
    assert ("0", "number") not in news._translation_fact_tokens(original)[0]


@pytest.mark.parametrize("original, correct, changed", [
    ("BTC rises 1,4%", "BTC 1.4% 상승", "BTC 14% 상승"),
    ("BTC rises 1,400%", "BTC 1400% 상승", "BTC 1.4% 상승"),
    ("BTC holds $1,400", "BTC 1400달러 유지", "BTC 1.4달러 유지"),
])
def test_unambiguous_percentage_decimal_and_thousands_groups_stay_distinct(original, correct, changed):
    assert news._valid_title_translation(original, correct)
    assert not news._valid_title_translation(original, changed)


def test_french_gdp_and_quarter_equivalents_do_not_permit_changed_quarters_or_tickers():
    pair = CAPTURED[3]
    assert news._valid_title_translation(pair["original"], pair["translated"])
    assert not news._valid_title_translation(pair["original"], pair["translated"].replace("2분기", "3분기"))
    assert not news._valid_title_translation(pair["original"], pair["translated"].replace("1.4%", "14%"))
    normalized = news._normalize_title_translation(pair["original"], pair["translated"].replace("GDP", "PIB"))
    assert news._valid_title_translation(pair["original"], normalized)
    assert "PIB" in news._translation_protected_upper_tokens("Le $PIB token rallies")
    assert "T2" in news._translation_protected_upper_tokens("Le PIB japonais compares $T2 token")
    assert not news._valid_title_translation("Le $PIB token rallies", "GDP 토큰 상승")


def test_software_sector_prose_does_not_erase_explicit_software_asset_identifiers():
    assert news._translation_protected_upper_tokens(CAPTURED[4]["original"]) == ("BTC",)
    for original in ("$SOFTWARE stock rallies", "SOFTWARE token rallies"):
        assert "SOFTWARE" in news._translation_protected_upper_tokens(original)
        assert not news._valid_title_translation(original, "소프트웨어 상승")


def test_apostrophe_contractions_never_create_a_t_ticker_but_real_t_is_required():
    assert news._translation_protected_upper_tokens(CAPTURED[5]["original"]) == ("AERO",)
    original = "$AERO AND $T: DON'T MISS THE RIDE"
    assert news._translation_protected_upper_tokens(original) == ("AERO", "T")
    assert not news._valid_title_translation(original, "$AERO: 기회를 놓치지 마세요")


@pytest.mark.parametrize("original, ticker", [
    ("Le prix de l'ETH monte", "ETH"),
    ("Le prix d’BTC monte", "BTC"),
    ("Le prix de l'T monte", "T"),
])
def test_french_apostrophe_articles_preserve_the_following_asset(original, ticker):
    assert ticker in news._translation_protected_upper_tokens(original)
    assert news._valid_title_translation(original, f"{ticker} 가격 상승")
    assert not news._valid_title_translation(original, "가격 상승")


@pytest.mark.parametrize("contraction", ["DON’T", "CAN'T", "SHOULDN'T"])
def test_actual_english_negative_contractions_do_not_create_assets(contraction):
    original = f"$AERO: {contraction} MISS THE RIDE"
    assert news._translation_protected_upper_tokens(original) == ("AERO",)


@pytest.mark.parametrize("index, ticker", [(0, "CFG"), (1, "CFG"), (6, "SOPH"), (7, "SOPH"), (8, "WLD")])
def test_cjk_and_lowercase_asset_recognition_still_rejects_missing_or_changed_ticker(index, ticker):
    pair = CAPTURED[index]
    assert ticker in news._translation_protected_upper_tokens(pair["original"])
    assert not news._valid_title_translation(pair["original"], pair["translated"].replace(ticker, ""))
    assert not news._valid_title_translation(pair["original"], pair["translated"].replace(ticker, "BTC"))


def test_only_explicit_chinese_ordinal_supports_added_rank():
    pair = CAPTURED[7]
    assert news._valid_title_translation(pair["original"], pair["translated"])
    assert not news._valid_title_translation(pair["original"], pair["translated"].replace("1위", "2위"))
    assert ("1", "number") not in news._implied_number_facts("SOPH 一般走势")


def test_oi_financial_expansion_is_equivalent_but_omission_is_not():
    pair = CAPTURED[2]
    translated = pair["translated"].replace("OI", "미결제약정")
    normalized = news._normalize_title_translation(pair["original"], translated)
    assert "미결제약정(OI)" in normalized
    assert news._valid_title_translation(pair["original"], normalized)
    assert not news._valid_title_translation(pair["original"], pair["translated"].replace("OI", ""))
