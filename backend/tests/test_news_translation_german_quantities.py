"""Captured production headlines preserve local number formats and currencies."""
import json
from types import SimpleNamespace

import pytest

from app import news


WLD = (
    'Eightco Holdings (NASDAQ: ORBS) gibt einen Gesamtbestand von rund 380 Millionen '
    'US-Dollar bekannt, darunter Anteile an OpenAI und Beast Industries sowie mehr '
    'als 16.000 ETH und fast 302 Millionen WLD-Token'
)
WLD_RESPONSE = (
    'Eightco Holdings (NASDAQ: ORBS)이 OpenAI와 Beast Industries 지분을 포함한 약 '
    '380백만 US$ 규모 자산 보유 발표, 16,000 ETH 이상 및 약 302백만 WLD 토큰 보유'
)
ACE = 'Fusionist (ACE): Revolutionizing Web3 Gaming with AAA Visuals, Blockchain Integration, and AI-Powered Strategy'
ACE_RESPONSE = 'Fusionist (ACE): AAA급 비주얼, 블록체인 통합, AI 기반 전략으로 Web3 게이밍 혁신'
DOT = 'Cryptocurrency prices show mixed movements with Bitcoin near $79K and notable gains in DOT and AVAX.'
DOT_RESPONSE = '비트코인 79K 근처에서 암호화폐 가격이 혼조세를 보이며 DOT와 AVAX에서 주목할 만한 상승'


@pytest.mark.parametrize('original,response,expected_name', [
    (WLD, WLD_RESPONSE, '나스닥'),
    (ACE, ACE_RESPONSE, '퓨저니스트'),
])
def test_captured_responses_preserve_facts_after_narrow_normalization(original, response, expected_name):
    raw = '```json\n' + json.dumps({'items': [{'id': news._title_translation_id(original), 'title_ko': response}]}) + '\n```'
    result = news._parse_korean_title_translations(raw, [original])
    assert original in result
    assert expected_name in result[original]
    assert news._translation_preserves_facts(original, result[original])


@pytest.mark.parametrize('response', [
    WLD_RESPONSE.replace('380백만', '381백만'),
    WLD_RESPONSE.replace('16,000 ETH', '16 ETH'),
    WLD_RESPONSE.replace('US$', '원'),
    WLD_RESPONSE.replace('WLD 토큰', 'BTC 토큰'),
])
def test_german_quantity_support_still_rejects_changed_facts(response):
    assert not news._valid_title_translation(WLD, news._normalize_title_translation(WLD, response))


@pytest.mark.parametrize('original,translated', [
    ('Bestand von 3,8 Millionen WLD und 16.000 ETH', '380만 WLD 및 1만6000 ETH 보유'),
    ('Bestand von 2 Milliarden WLD', '20억 WLD 보유'),
    ('Bestand von 2 Billionen WLD', '2조 WLD 보유'),
    ('Bestand von 30 Tausend ETH', '3만 ETH 보유'),
])
def test_explicit_german_scale_context_supports_its_number_format(original, translated):
    assert news._translation_preserves_facts(original, translated)


def test_english_decimal_and_scale_meaning_is_unchanged():
    assert news._translation_preserves_facts('Holdings of 1.000 ETH', '1 ETH 보유')
    assert not news._translation_preserves_facts('Holdings of 1.000 ETH', '1000 ETH 보유')
    assert news._translation_preserves_facts('Holdings of 2 billion WLD', '20억 WLD 보유')
    assert not news._translation_preserves_facts('Holdings of 2 billion WLD', '2조 WLD 보유')


def test_captured_currency_omission_is_rejected_until_model_preserves_usd():
    assert not news._valid_title_translation(DOT, DOT_RESPONSE)
    assert news._valid_title_translation(DOT, DOT_RESPONSE.replace('79K', '$79K'))


def test_captured_german_dollar_currency_is_not_a_us_ticker():
    response = (
        '이트코 홀딩스(NASDAQ: ORBS), 약 3억8000만 USD의 총 자산 공시 - '
        '오픈AI와 비스트 인더스트리스 지분, 16,000개 이상의 ETH 및 '
        '약 3억200만 개의 WLD 토큰 보유'
    )
    normalized = news._normalize_title_translation(WLD, response)
    assert '3억8000만 달러의' in normalized
    assert 'US' not in news._translation_protected_upper_tokens(WLD)
    assert news._valid_title_translation(WLD, normalized)
    assert not news._valid_title_translation(WLD, normalized.replace('달러의', '원의'))
    assert not news._valid_title_translation(WLD, normalized.replace('ORBS', 'ORCA'))
    assert news._normalize_title_translation(WLD, '3억8000만 USD.AI 토큰') == '3억8000만 USD.AI 토큰'
    assert 'US' in news._translation_protected_upper_tokens('US token rises')


def test_request_supplies_required_amounts_and_currency_without_relaxing_validation(monkeypatch):
    requests = []
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    class Messages:
        def create(self, **kwargs):
            requests.append(kwargs)
            payload = {'items': [{'id': news._title_translation_id(DOT), 'title_ko': DOT_RESPONSE.replace('79K', '$79K')}]}
            return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(payload))])
    class Runtime:
        def call(self, key, loader, **kwargs):
            return loader(), 'loaded'
    monkeypatch.setattr(news, 'get_ai_client', lambda: SimpleNamespace(messages=Messages()))
    monkeypatch.setattr(news, 'get_ai_runtime', Runtime)
    assert news._request_korean_title_translations([DOT])
    article = json.loads(requests[0]['messages'][0]['content'])[0]
    assert article['required_currencies'] == ['USD']
    assert article['protected_numbers'] == [{'value': '79000', 'unit': 'number'}]
    assert set(article['protected_terms']) == {'DOT', 'AVAX'}
    assert '달러가 누락' in requests[0]['system']
