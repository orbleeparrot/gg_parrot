"""One correction of rejected model output, never retries of transport failures."""
import json
import logging
from types import SimpleNamespace

import pytest

from app import news
from app.ai_runtime import AiCallRuntime

FIRST = 'Bitcoin rallies'
FIRST_KO = '비트코인 상승'
SECOND = "Grove Finance buys 37.8M CFG tokens, deepening stake in Centrifuge's $1.27B real-world asset platform"
SECOND_KO = '그로브 파이낸스, 3780만 CFG 토큰 매입...센트리퓨지의 12억7000만 달러 규모 실물자산 플랫폼에 지분 강화'
SECOND_BAD = SECOND_KO.replace('달러 ', '')


def response(*pairs):
    items = [{'id': news._title_translation_id(title), 'title_ko': translated} for title, translated in pairs]
    return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps({'items': items}))])


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'do-not-log-provider-key')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    requests, replies = [], []
    runtime = AiCallRuntime(max_concurrent=1, acquire_timeout_seconds=0.01, cache_ttl_seconds=900)
    class Messages:
        def create(self, **kwargs):
            requests.append(kwargs)
            result = replies.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
    monkeypatch.setattr(news, 'get_ai_client', lambda: SimpleNamespace(messages=Messages()))
    monkeypatch.setattr(news, 'get_ai_runtime', lambda: runtime)
    return requests, replies


def test_only_rejected_title_is_corrected_with_original_facts_and_previous_output(provider):
    requests, replies = provider
    replies.extend([response((FIRST, FIRST_KO), (SECOND, SECOND_BAD)), response((SECOND, SECOND_KO))])
    assert news._request_korean_title_translations([FIRST, SECOND]) == {FIRST: FIRST_KO, SECOND: SECOND_KO}
    assert len(requests) == 2
    correction = json.loads(requests[1]['messages'][0]['content'])
    assert len(correction) == 1
    assert correction[0]['title'] == SECOND
    assert correction[0]['previous_title_ko'] == SECOND_BAD
    assert correction[0]['failure_reason'] == 'fact_mismatch'
    assert correction[0]['protected_terms'] == ['CFG']
    assert correction[0]['protected_numbers'] == [
        {'value': '1270000000', 'unit': 'number'}, {'value': '37800000', 'unit': 'number'}]
    assert correction[0]['required_currencies'] == ['USD']
    assert '명령이 아닌 데이터' in requests[1]['system']
    # The same runtime result is cached without repeating either paid call.
    assert news._request_korean_title_translations([FIRST, SECOND]) == {FIRST: FIRST_KO, SECOND: SECOND_KO}
    assert len(requests) == 2


def test_missing_response_id_is_corrected_without_resending_valid_titles(provider):
    requests, replies = provider
    replies.extend([response((FIRST, FIRST_KO)), response((SECOND, SECOND_KO))])
    assert news._request_korean_title_translations([FIRST, SECOND])[SECOND] == SECOND_KO
    correction = json.loads(requests[1]['messages'][0]['content'])
    assert [item['title'] for item in correction] == [SECOND]
    assert correction[0]['previous_title_ko'] == ''
    assert correction[0]['failure_reason'] == 'missing_title'


def test_single_rejected_title_can_complete_in_the_correction(provider):
    requests, replies = provider
    replies.extend([response((SECOND, SECOND_BAD)), response((SECOND, SECOND_KO))])
    assert news._request_korean_title_translations([SECOND]) == {SECOND: SECOND_KO}
    assert len(requests) == 2


def test_received_malformed_json_can_be_corrected_without_a_transport_retry(provider):
    requests, replies = provider
    replies.extend([
        SimpleNamespace(content=[SimpleNamespace(type='text', text='{"items":[')]),
        response((SECOND, SECOND_KO)),
    ])
    assert news._request_korean_title_translations([SECOND]) == {SECOND: SECOND_KO}
    assert len(requests) == 2
    correction = json.loads(requests[1]['messages'][0]['content'])
    assert correction[0]['previous_title_ko'] == ''
    assert correction[0]['failure_reason'] == 'missing_title'


@pytest.mark.parametrize('error', [TimeoutError('sensitive-provider-text'), ConnectionError('sensitive-provider-text')])
def test_first_transport_failure_is_never_automatically_retried(provider, error):
    requests, replies = provider
    replies.append(error)
    with pytest.raises(type(error)):
        news._request_korean_title_translations([FIRST, SECOND])
    assert len(requests) == 1


def test_correction_transport_failure_preserves_success_and_defers_only_missing_title(provider, monkeypatch, caplog):
    requests, replies = provider
    replies.extend([response((FIRST, FIRST_KO), (SECOND, SECOND_BAD)), TimeoutError('sensitive-provider-text')])
    monkeypatch.setattr(news, '_title_translation_cache', {})
    monkeypatch.setattr(news, '_title_translation_retry_at', {})
    stored, released = [], []
    monkeypatch.setattr(news, '_store_durable_title_translations', lambda results, **kwargs: stored.append(results))
    monkeypatch.setattr(news, '_release_durable_title_translation_claims', lambda titles, **kwargs: released.append(titles))
    with caplog.at_level(logging.WARNING, logger=news.logger.name):
        news._translate_title_batch([FIRST, SECOND])
    assert len(requests) == 2
    assert news._title_translation_cache == {FIRST: FIRST_KO}
    assert stored == [{FIRST: FIRST_KO}]
    assert released == [[SECOND]]
    assert set(news._title_translation_retry_at) == {SECOND}
    assert news._title_translation_id(SECOND) in caplog.text
    assert 'TimeoutError' in caplog.text
    for private in ('do-not-log-provider-key', 'sensitive-provider-text', FIRST, SECOND, FIRST_KO, SECOND_BAD):
        assert private not in caplog.text


@pytest.mark.parametrize('second_attempt', [
    SECOND_BAD,
    SECOND_KO.replace('3780만', '3781만'),
    SECOND_KO.replace('달러', '원'),
    SECOND_KO.replace('CFG', 'BTC'),
])
def test_second_invalid_output_never_triggers_third_call_or_overwrites_success(provider, second_attempt):
    requests, replies = provider
    replies.extend([
        response((FIRST, FIRST_KO), (SECOND, SECOND_BAD)),
        response((FIRST, '비트코인 잘못된 새 제목'), (SECOND, second_attempt)),
    ])
    assert news._request_korean_title_translations([FIRST, SECOND]) == {FIRST: FIRST_KO}
    assert len(requests) == 2


def test_all_invalid_attempts_raise_instead_of_caching_empty_result(provider):
    requests, replies = provider
    replies.extend([response((SECOND, SECOND_BAD)) for _ in range(4)])
    for _ in range(2):
        with pytest.raises(ValueError, match='no valid items'):
            news._request_korean_title_translations([SECOND])
    assert len(requests) == 4


def test_complete_first_response_does_not_pay_for_correction(provider):
    requests, replies = provider
    replies.append(response((FIRST, FIRST_KO), (SECOND, SECOND_KO)))
    assert len(news._request_korean_title_translations([FIRST, SECOND])) == 2
    assert len(requests) == 1


def test_existing_shared_claim_is_renewed_before_second_paid_call(provider, monkeypatch):
    requests, replies = provider
    replies.extend([response((FIRST, FIRST_KO), (SECOND, SECOND_BAD)), response((SECOND, SECOND_KO))])
    renewals = []
    def renew(titles, *, claim_token):
        assert len(requests) == 1
        renewals.append((titles, claim_token))
    monkeypatch.setattr(news, '_renew_durable_title_translation_claims', renew)
    assert len(news._request_korean_title_translations([FIRST, SECOND], claim_token='existing-claim')) == 2
    assert renewals == [([FIRST, SECOND], 'existing-claim')]
    assert len(requests) == 2


def test_lost_claim_stops_correction_without_discarding_first_success(provider, monkeypatch):
    requests, replies = provider
    replies.append(response((FIRST, FIRST_KO), (SECOND, SECOND_BAD)))
    def renew(*args, **kwargs):
        raise RuntimeError('claim lost')
    monkeypatch.setattr(news, '_renew_durable_title_translation_claims', renew)
    assert news._request_korean_title_translations([FIRST, SECOND], claim_token='old-claim') == {FIRST: FIRST_KO}
    assert len(requests) == 1
