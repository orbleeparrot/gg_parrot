"""Korean compound magnitudes must preserve the publisher's numerical facts."""

import pytest

from app import news


@pytest.mark.parametrize("amount,expected", [
    ("18억 1천만", "1810000000"),
    ("18억1천만", "1810000000"),
    ("1천만", "10000000"),
    ("1천억", "100000000000"),
    ("2백만", "2000000"),
    ("2백억", "20000000000"),
    ("3십만", "300000"),
    ("1천2백만", "12000000"),
    ("1천백만", "11000000"),
    ("1억2천 3백만", "123000000"),
    ("1조 2천억 3백만 4천", "1200003004000"),
    ("1만2천억", "1200000000000"),
    ("3억 2000만", "320000000"),
    ("7만8600", "78600"),
    ("1천2백3십4", "1234"),
    ("1.5천만", "15000000"),
    ("0.25백만", "250000"),
    ("-2백만", "-2000000"),
    ("+1,200만", "12000000"),
    ("1.81 Billion", "1810000000"),
])
def test_compound_korean_amount_is_one_exact_numeric_fact(amount, expected):
    assert news._translation_fact_tokens(amount)[0] == ((expected, "number"),)


def test_actual_plasma_translation_keeps_unlock_amount_and_date():
    original = "Plasma XPL Unlock on September 25: 1.81 Billion Tokens Come Free"
    translated = "플라즈마 XPL 9월 25일 잠금 해제: 18억 1천만 개 토큰 무료 공개"
    assert news._valid_title_translation(original, translated)
    for changed in ("18억 2천만", "18억 1백만", "18억 1천", "18억"):
        assert not news._valid_title_translation(
            original, translated.replace("18억 1천만", changed)
        )


@pytest.mark.parametrize("original,translated", [
    ("Fund raises $10M", "펀드, 1천만달러 조달"),
    ("Fund raises €2M", "펀드, 2백만유로를 조달"),
    ("Fund raises £100B", "펀드, 1천억파운드 조달"),
    ("Fund raises KRW 2M", "펀드, 2백만원 조달"),
    ("Fund raises $12M", "펀드, 1천2백만달러로 조달"),
])
def test_compound_currency_amount_is_valid_and_changed_amount_is_rejected(original, translated):
    assert news._valid_title_translation(original, translated)
    changed = translated.replace("1", "3", 1) if "1" in translated else translated.replace("2", "3", 1)
    assert not news._valid_title_translation(original, changed)


def test_whitespace_does_not_merge_independent_amount_percentage_or_count():
    assert news._translation_fact_tokens("3억 5% 2명")[0] == (
        ("2", "number"), ("300000000", "number"), ("5", "%"),
    )
    assert news._valid_title_translation(
        "Fund reaches $300M, grows 5%", "펀드 3억달러 도달, 5% 증가",
    )
    assert not news._valid_title_translation(
        "Fund reaches $300M, grows 5%", "펀드 3억5천만달러 도달",
    )
