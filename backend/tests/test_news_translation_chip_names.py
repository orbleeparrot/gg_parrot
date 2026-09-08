"""Known company/project names cannot strand otherwise valid CHIP headlines."""
import json

import pytest

from app import news


@pytest.mark.parametrize("original,response,expected", [
    ("Bullish Expands Into AI Infrastructure Lending With USD.AI Deal",
     "Bullish, USD.AI 딜로 AI 인프라 대출 사업 확대",
     "불리시, USD.AI 딜로 AI 인프라 대출 사업 확대"),
    ("AI News: Tradoor Sinks, TAO Stabilizes, USD.AI Surges",
     "AI 뉴스: Tradoor 하락, TAO 안정세, USD.AI 급등",
     "AI 뉴스: 트래도어 하락, TAO 안정세, USD.AI 급등"),
    ("Bullish Backs USD.AI With $100M GPU Financing",
     "Bullish, USD.AI에 1억 달러 GPU 파이낸싱 지원",
     "불리시, USD.AI에 1억 달러 GPU 파이낸싱 지원"),
])
def test_captured_provider_output_normalizes_only_confirmed_names(original, response, expected):
    payload = json.dumps({"items": [{"id": news._title_translation_id(original), "title_ko": response}]})
    assert news._parse_korean_title_translations(payload, [original]) == {original: expected}
    assert news._valid_title_translation(original, expected)


@pytest.mark.parametrize("original,response", [
    ("Bitcoin Looks Bullish After ETF Approval", "비트코인, ETF 승인 후 Bullish 전망"),
    ("Bullish Traders Buy Bitcoin", "Bullish 트레이더, 비트코인 매수"),
    ("Examplecorp Backs USD.AI Funding", "Examplecorp, USD.AI 자금 지원"),
    ("AI News: Tradoor Sinks", "AI 뉴스: Tradoor Sinks"),
])
def test_company_normalization_does_not_allow_ordinary_prose_or_arbitrary_names(original, response):
    normalized = news._normalize_title_translation(original, response)
    assert "불리시" not in normalized
    assert not news._valid_title_translation(original, normalized)


@pytest.mark.parametrize("response", [
    "Bullish, USD.AI에 2억 달러 GPU 파이낸싱 지원",
    "Bullish, USD.AI에 1억 원 GPU 파이낸싱 지원",
    "Bullish, USD.AI에 1억 달러 파이낸싱 지원",
])
def test_name_normalization_still_rejects_changed_amount_currency_or_lost_identifier(response):
    original = "Bullish Backs USD.AI With $100M GPU Financing"
    payload = json.dumps({"items": [{"id": news._title_translation_id(original), "title_ko": response}]})
    assert news._parse_korean_title_translations(payload, [original]) == {}


def test_tradoor_normalization_does_not_change_or_remove_other_token_symbols():
    original = "AI News: Tradoor Sinks, TAO Stabilizes, USD.AI Surges"
    for changed in ("BTC", ""):
        response = f"AI 뉴스: Tradoor 하락, {changed} 안정세, USD.AI 급등"
        assert not news._valid_title_translation(original, news._normalize_title_translation(original, response))
