"""Hourly news windows must remain numeric facts after Korean translation."""
import json

import pytest

from app import news


ORIGINAL = 'FET Drops 10% in 24h: Technical Correction in Weak AI Sector'
RESPONSE = 'FET 24시간 내 10% 하락: 약세 AI 부문의 기술적 조정'


def test_captured_provider_hour_translation_preserves_original_number():
    payload = json.dumps({'items': [{'id': news._title_translation_id(ORIGINAL), 'title_ko': RESPONSE}]})
    assert news._parse_korean_title_translations(payload, [ORIGINAL]) == {ORIGINAL: RESPONSE}
    assert ('24', 'hour') in news._translation_fact_tokens(ORIGINAL)[0]


@pytest.mark.parametrize('response', [
    RESPONSE.replace('24시간', '25시간'),
    RESPONSE.replace('24시간 내 ', ''),
    RESPONSE.replace('10%', '11%'),
    RESPONSE.replace('FET', 'BTC'),
    RESPONSE.replace('AI ', ''),
    RESPONSE.replace('24시간', '24'),
    RESPONSE.replace('24시간', '24분'),
])
def test_hour_support_still_rejects_changed_or_missing_facts(response):
    assert not news._valid_title_translation(ORIGINAL, response)


@pytest.mark.parametrize('hours', ['1h', '24h', '24H'])
def test_hour_abbreviation_supports_korean_and_unchanged_hour_notation(hours):
    count = hours[:-1]
    original = f'FET gains 10% over {hours}'
    assert news._valid_title_translation(original, f'FET, {count}시간 동안 10% 상승')
    assert news._valid_title_translation(original, f'FET, {hours} 동안 10% 상승')
