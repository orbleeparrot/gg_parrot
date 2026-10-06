"""GG-006: 429 must pause unrelated work without burning source-item budgets."""
from types import SimpleNamespace

import httpx
import openai
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import ai_provider_health, ai_runtime, news_ai_budget


@pytest.fixture(autouse=True)
def isolated_health(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'provider-health.db'}", connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setenv('OPENAI_API_KEY', 'cooldown-test-fixture-only')
    for module in (ai_provider_health, news_ai_budget):
        monkeypatch.setattr(module, 'get_session', lambda: Session(engine))
    yield
    engine.dispose()


def test_unrelated_news_work_is_not_called_after_provider_429(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'cooldown-test-fixture-only')
    monkeypatch.setattr(ai_runtime, 'record_openai_usage', lambda **_: None)
    calls = []
    def create(**_):
        calls.append('provider')
        raise openai.RateLimitError('fixture rate limit',
            response=httpx.Response(429, request=httpx.Request('POST', 'https://example.invalid')),
            body={'code': 'rate_limit_exceeded'})
    messages = ai_runtime._Messages(SimpleNamespace(responses=SimpleNamespace(create=create)))
    runtime = ai_runtime.AiCallRuntime(retries=0)
    def translate():
        news_ai_budget.reserve('title', ['cooldown-first-title'])
        return messages.create(model='gpt-6-luna', max_tokens=256, messages=[])
    with pytest.raises(ai_runtime.AiRateLimitError):
        runtime.call('first-title', translate, retries=0)
    def summarize():
        news_ai_budget.reserve('summary', ['cooldown-new-body'])
        calls.append('summary-loader')
        return 'not expected'
    with pytest.raises(ai_runtime.AiBusyError):
        # Separate runtime simulates another process reading shared storage.
        ai_runtime.AiCallRuntime(retries=0).call('different-body', summarize, retries=0)
    assert calls == ['provider']
    assert news_ai_budget.reserve('summary', ['cooldown-new-body']) == {'cooldown-new-body'}
    assert news_ai_budget.reserve('summary', ['cooldown-new-body']) == {'cooldown-new-body'}


def test_pause_expiry_and_extensions_are_monotonic():
    error = ai_runtime.AiRateLimitError('safe', status_code=429)
    ai_provider_health.pause(error, now_ms=1000)
    ai_provider_health.pause(error, now_ms=2000)
    ai_provider_health.pause(error, now_ms=500)
    with pytest.raises(ai_runtime.AiBusyError):
        ai_provider_health.ensure_available(now_ms=61_500)
    ai_provider_health.ensure_available(now_ms=62_000)


def test_key_rotation_does_not_keep_old_key_blocked(monkeypatch):
    ai_provider_health.pause(ai_runtime.AiRateLimitError('safe', status_code=429))
    monkeypatch.setenv('OPENAI_API_KEY', 'another-fixture-key-only')
    ai_provider_health.ensure_available()


def test_cached_success_remains_available_during_pause():
    runtime = ai_runtime.AiCallRuntime(retries=0)
    assert runtime.call('ready', lambda: '한국어 준비 결과')[0] == '한국어 준비 결과'
    ai_provider_health.pause(ai_runtime.AiRateLimitError('safe', status_code=429))
    assert runtime.call('ready', lambda: pytest.fail('cache called AI')) == ('한국어 준비 결과', 'cached')
    with pytest.raises(ai_runtime.AiBusyError):
        runtime.call('different', lambda: pytest.fail('paused loader ran'))


@pytest.mark.parametrize('code', ['insufficient_quota', 'billing_hard_limit_reached'])
def test_quota_is_not_immediately_retried_or_logged_as_generic_429(code):
    error = openai.RateLimitError('raw provider secret must stay private',
        response=httpx.Response(429, request=httpx.Request('POST', 'https://example.invalid')),
        body={'code': code})
    mapped = ai_runtime._translate_error(error)
    assert isinstance(mapped, ai_runtime.AiQuotaError)
    assert not ai_runtime._provider_transient(mapped)
    assert 'secret' not in str(mapped)
    ai_provider_health.pause(mapped, now_ms=1000)
    with pytest.raises(ai_runtime.AiBusyError):
        ai_provider_health.ensure_available(now_ms=900_999)
    ai_provider_health.ensure_available(now_ms=901_000)


def test_health_storage_failure_prevents_paid_loader(monkeypatch):
    def broken_session():
        raise RuntimeError('isolated storage unavailable')
    monkeypatch.setattr(ai_provider_health, 'get_session', broken_session)
    with pytest.raises(RuntimeError, match='storage unavailable'):
        ai_runtime.AiCallRuntime().call('new', lambda: pytest.fail('paid loader ran'))


def test_validation_failure_does_not_block_unrelated_work():
    ai_provider_health.pause(ValueError('not a provider-wide error'))
    ai_provider_health.ensure_available()
    assert ai_runtime.AiCallRuntime().call('valid', lambda: 'ready')[0] == 'ready'
