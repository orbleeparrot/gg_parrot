"""Korean headlines preserve identifiers independently of sentence repetition."""
import pytest

from app import news


@pytest.mark.parametrize("translated", [
    "파일코인(FIL) 가격 및 기술적 분석(TA)",
    "FIL 가격 파일코인 TA (TA) FIL 기술적 분석",
])
def test_identifier_repetition_does_not_reject_korean_translation(translated):
    original = "FIL Price Filecoin TA FIL Technical Analysis"
    assert news._valid_title_translation(original, translated)


@pytest.mark.parametrize("translated", [
    "파일코인(FIL) 가격 및 기술적 분석",  # TA is lost.
    "파일코인(ETH) 가격 및 기술적 분석(TA)",  # The asset changed.
    "파일코인(FIL) 및 ETH 가격과 기술적 분석(TA)",  # An asset was invented.
])
def test_missing_or_changed_identifiers_remain_invalid(translated):
    original = "FIL Price Filecoin TA FIL Technical Analysis"
    assert not news._valid_title_translation(original, translated)


def test_repeated_numbers_remain_facts_even_when_identifiers_repeat():
    original = "BTC rises 5%, ETH follows with 5%"
    assert news._valid_title_translation(original, "BTC 5% 상승, ETH도 5% 상승")
    assert not news._valid_title_translation(original, "BTC와 ETH 5% 상승")
