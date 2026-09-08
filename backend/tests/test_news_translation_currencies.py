"""Korean currency and long-headline validation without a translation provider."""
import pytest

from app import news


@pytest.mark.parametrize("original,translated", [
    ("Fund raises €5 million", "펀드, 500만 유로 조달"),
    ("Fund grows to EUR 5M", "펀드 규모 500만 유로로 증가"),
    ("Fund raises £320M", "펀드, 3억 2000만 파운드 조달"),
    ("Fund grows to GBP 320M", "펀드 규모 3억2000만파운드로 증가"),
    ("Bitcoin trading volume reaches ₩100B", "비트코인 거래량 1000억원 달해"),
    ("Fund grows to KRW 100B", "펀드 규모 1000억원대로 증가"),
    ("Fund distributes ₩5000", "펀드, 5000원을 지급"),
    ("Euro and pound payments expand", "유로와 파운드 결제 확대"),
    ("Fund raises $320M", "펀드, 3억 2000만달러를 조달"),
    ("$WLD targets $0.50", "$WLD, 0.50달러 목표"),
])
def test_korean_currency_labels_preserve_the_original_amount(original, translated):
    assert news._valid_title_translation(original, translated)


@pytest.mark.parametrize("original,translated", [
    ("Fund raises €5 million", "펀드, 500만 달러 조달"),
    ("Fund raises £5 million", "펀드, 500만 유로 조달"),
    ("Fund raises ₩5 million", "펀드, 500만 파운드 조달"),
    ("Fund raises $5 million", "펀드, 500만원 조달"),
    ("Fund raises €5 million", "펀드, 500만 조달"),
    ("Fund raises GBP 320M", "펀드, 3억2000만원 조달"),
    ("Fund raises ₩100B", "펀드, 100억원 조달"),
    ("Fund raises €5 million", "펀드, 500만 유로 조달하며 5% 성장"),
    ("BTC falls -5%", "BTC, 5% 상승"),
    ("BTC falls 5%", "ETH, 5% 하락"),
    ("$WLD targets $0.50", "$WLD, 0.50원 목표"),
])
def test_currency_number_and_ticker_changes_remain_invalid(original, translated):
    assert not news._valid_title_translation(original, translated)


@pytest.mark.parametrize("text", ["원 토큰 개발", "정책 100원칙 발표", "100명의 위원", "유로파 프로젝트 발표"])
def test_currency_detection_does_not_match_unrelated_korean_words(text):
    assert news._translation_fact_tokens(text)[2] == ()


def test_long_publisher_headline_can_keep_all_translated_content():
    original = "Bitcoin network update includes " + "security and developer improvements, " * 20 + "$25M funding"
    translated = "비트코인 네트워크 업데이트에 " + "보안 개선과 개발자 환경 개선, " * 20 + "2500만 달러 자금 조달 포함"
    assert len(original) > 300 and len(translated) > 300
    assert news._valid_title_translation(original, translated)
    assert not news._valid_title_translation(original, translated.replace("2500만", "3500만"))


def test_long_headline_limit_still_rejects_unbounded_model_output():
    assert not news._valid_title_translation("Bitcoin network improves", "비트코인 " + "개선 " * 200)
    original = "Bitcoin network improves " * 100
    assert not news._valid_title_translation(original, "비트코인 " + "개선 " * 600)
