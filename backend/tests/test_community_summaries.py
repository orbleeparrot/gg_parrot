from copy import deepcopy

import pytest

from app import community_summaries as summaries


def post(body="$CFG recovered support at 0.13 USDT. Volume increased 12%."):
    return {"content_type": "community", "source": "Binance Square", "community_post_id": "123",
            "url": "https://www.binance.com/en/square/post/123", "title": "CFG market update",
            "community_body": body, "community_body_status": "ready"}


class Cache:
    def __init__(self):
        self.values = {}
        self.claims = {}
        self.released = []

    def make_summary_key(self, **values):
        return "|".join(values.values())

    def get_summaries(self, requests):
        return {r["summary_key"]: self.values[r["summary_key"]] for r in requests if r["summary_key"] in self.values}

    def claim_summaries(self, requests, rejected_keys=None):
        keys = [r["summary_key"] for r in requests if r["summary_key"] not in self.values or r["summary_key"] in (rejected_keys or [])]
        self.claims.update({key: "owner" for key in keys})
        return {"claim_token": "owner", "claimed": keys, "waiting": [], "deferred": [], "cached": {}}

    def renew_claims(self, keys, *, claim_token):
        return all(self.claims.get(k) == claim_token for k in keys)

    def store_summaries(self, values, *, claim_token):
        self.values.update({k: v for k, v in values.items() if self.claims.get(k) == claim_token})
        return list(values)

    def release_claims(self, keys, **options):
        self.released.append((keys, options))


@pytest.fixture
def cache(monkeypatch):
    repository = Cache()
    monkeypatch.setattr(summaries, "_repository", lambda: repository)
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    summaries.clear_memory_cache()
    return repository


def test_body_is_summarized_once_and_shared_cache_survives_memory_reset(cache, monkeypatch):
    calls = []
    def request(jobs):
        calls.append(deepcopy(jobs))
        return {job["summary_key"]: "작성자는 CFG가 0.13 USDT 지지선을 회복했고 거래량이 12% 증가했다고 설명합니다." for job in jobs}
    monkeypatch.setattr(summaries, "_request_summaries", request)
    items, status = summaries.enrich_items([post(), post()], wait=True)
    assert len(calls) == 1 and len(calls[0]) == 1
    assert "Volume increased 12%" in calls[0][0]["body"]
    assert items[0]["community_summary_status"] == "ready"
    assert status["pending_count"] == 0
    summaries.clear_memory_cache()
    assert summaries.enrich_items([post()], wait=True)[0][0]["community_summary"]
    assert len(calls) == 1


def test_body_edit_requires_new_summary_without_changing_post_identity(cache, monkeypatch):
    calls = []
    def request(jobs):
        calls.extend(jobs)
        return {job["summary_key"]: "작성자는 CFG의 지지선 회복과 거래량 변화를 설명합니다." for job in jobs}
    monkeypatch.setattr(summaries, "_request_summaries", request)
    first = summaries.enrich_items([post()], wait=True)[0][0]
    second = summaries.enrich_items([post("$CFG lost support. Selling volume increased.")], wait=True)[0][0]
    assert len(calls) == 2 and calls[0]["summary_key"] != calls[1]["summary_key"]
    assert first["community_post_id"] == second["community_post_id"]


def test_public_read_keeps_title_visible_while_background_summary_is_pending(cache, monkeypatch):
    queued = []
    monkeypatch.setattr(summaries, "_schedule", lambda jobs: queued.extend(jobs))
    items, status = summaries.enrich_items([post()])
    assert items[0]["title"] == "CFG market update"
    assert items[0]["community_summary_status"] == "pending"
    assert status["pending_count"] == 1 and queued[0]["body"]


@pytest.mark.parametrize("candidate", [
    "The author says CFG recovered support. 한국어",
    "작성자는 CFG의 거래량이 99% 증가했다고 설명합니다.",
    "작성자는 CFG가 0.13 유로 지지선을 회복했다고 설명합니다.",
])
def test_invalid_summary_cannot_enter_shared_cache(cache, monkeypatch, candidate):
    monkeypatch.setattr(summaries, "_request_summaries", lambda jobs: {job["summary_key"]: candidate for job in jobs})
    items, status = summaries.enrich_items([post()], wait=True)
    assert items[0]["community_summary_status"] == "pending"
    assert status["pending_count"] == 1 and not cache.values and cache.released


def test_db_claim_failure_never_falls_back_to_uncoordinated_paid_work(cache, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(cache, "claim_summaries", fail)
    monkeypatch.setattr(summaries, "_request_summaries", lambda jobs: pytest.fail("unclaimed model call"))
    assert summaries.enrich_items([post()], wait=True)[1]["pending_count"] == 1


def test_missing_body_does_not_summarize_the_headline(cache, monkeypatch):
    monkeypatch.setattr(summaries, "_request_summaries", lambda jobs: pytest.fail("headline-only summary"))
    items, status = summaries.enrich_items([{**post(""), "community_body_status": "missing"}], wait=True)
    assert items[0]["community_summary_status"] == "unavailable"
    assert not items[0]["community_summary"] and status["pending_count"] == 0


def test_partial_body_is_labeled_and_no_daily_translation_cap_applies(cache, monkeypatch):
    monkeypatch.setenv("NEWS_TITLE_TRANSLATION_DAILY_LIMIT", "0")
    monkeypatch.setattr(summaries, "_request_summaries", lambda jobs: {j["summary_key"]: "작성자는 CFG의 거래량 변화와 지지선을 설명합니다." for j in jobs})
    items, _ = summaries.enrich_items([{**post(), "community_body_truncated": True}], wait=True)
    assert items[0]["community_summary_status"] == "ready"
    assert items[0]["community_summary_partial"] is True


def test_partial_batch_saves_good_summaries_and_releases_only_failed_claims(cache, monkeypatch):
    second = {**post(), "community_post_id": "456"}
    def request(jobs):
        return {jobs[0]["summary_key"]: "작성자는 CFG의 거래량 변화와 지지선을 설명합니다.",
                jobs[1]["summary_key"]: "작성자는 99% 상승을 설명합니다."}
    monkeypatch.setattr(summaries, "_request_summaries", request)
    items, status = summaries.enrich_items([post(), second], wait=True)
    assert [i["community_summary_status"] for i in items] == ["ready", "pending"]
    assert status["pending_count"] == 1 and len(cache.values) == 1
    assert len(cache.released[-1][0]) == 1


def test_lost_claim_never_calls_provider_or_displays_unpersisted_result(cache, monkeypatch):
    monkeypatch.setattr(cache, "renew_claims", lambda *a, **kw: False)
    monkeypatch.setattr(summaries, "_request_summaries", lambda jobs: pytest.fail("lost claim"))
    assert summaries.enrich_items([post()], wait=True)[1]["pending_count"] == 1
    monkeypatch.setattr(cache, "renew_claims", lambda *a, **kw: True)
    monkeypatch.setattr(cache, "store_summaries", lambda *a, **kw: [])
    monkeypatch.setattr(summaries, "_request_summaries", lambda jobs: {j["summary_key"]: "작성자는 CFG의 거래량 변화와 지지선을 설명합니다." for j in jobs})
    assert summaries.enrich_items([post()], wait=True)[1]["pending_count"] == 1


def test_provider_receives_body_not_headline_and_runtime_does_not_retry(cache, monkeypatch):
    from types import SimpleNamespace
    calls = []
    class Runtime:
        def call(self, key, loader, *, retries):
            assert retries == 0
            return loader(), "loaded"
    class Messages:
        def create(self, **kwargs):
            import json
            assert kwargs["timeout"] == 45
            payload = json.loads(kwargs["messages"][0]["content"])
            calls.append(payload)
            assert payload[0]["body"] == post()["community_body"]
            assert "title" not in payload[0]
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"items": [
                {"id": payload[0]["id"], "summary_ko": "작성자는 CFG의 거래량 변화와 지지선을 설명합니다."}]}))])
    monkeypatch.setattr(summaries, "get_ai_runtime", lambda: Runtime())
    monkeypatch.delenv("COMMUNITY_SUMMARY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(summaries, "get_ai_client", lambda: SimpleNamespace(messages=Messages()))
    assert summaries.enrich_items([post()], wait=True)[0][0]["community_summary_status"] == "ready"
    assert len(calls) == 1


def test_model_input_bound_marks_partial_and_preserves_stored_body(cache, monkeypatch):
    body = "작성자가 비트코인 시장 흐름에 대해 설명합니다. " * 1000
    def request(jobs):
        assert len(jobs[0]["body"]) == summaries.MAX_BODY_CHARS
        return {jobs[0]["summary_key"]: "작성자는 비트코인 시장의 흐름에 대한 의견을 설명합니다."}
    monkeypatch.setattr(summaries, "_request_summaries", request)
    item = summaries.enrich_items([post(body)], wait=True)[0][0]
    assert item["community_summary_partial"] and item["community_body"] == body


def test_ready_projected_summary_survives_raw_body_removal(cache):
    item = {"content_type": "community", "community_summary": "작성자는 비트코인 시장의 흐름에 대한 의견을 설명합니다.",
            "community_summary_status": "ready", "community_summary_partial": True}
    items, status = summaries.enrich_items([item])
    assert items[0] == item and status["pending_count"] == 0


@pytest.mark.parametrize("summary", [
    "작성자는 BTC가 0.13 USDT 지지선을 회복했고 거래량이 12% 증가했다고 설명합니다.",
    "작성자는 CFG의 지지선 회복 가능성을 설명하며 시장 상황을 관찰할 필요가 있다는 의견을 제시합니다. Buy now guaranteed profits.",
    "작성자는 CFG가 0.13 지지선을 회복했고 거래량이 12% 증가했다고 설명합니다.",
])
def test_changed_tickers_omitted_quote_units_and_embedded_english_are_rejected(summary):
    assert not summaries._valid_summary(post()["community_body"], summary)


@pytest.mark.parametrize("summary, expected", [
    ("작성자는 펀드가 BTC를 4,700만 달러어치 매수했다고 설명합니다.", True),
    ("작성자는 펀드가 BTC를 4,700만 규모로 매수했다고 설명합니다.", False),
    ("작성자는 펀드가 BTC를 4,700만 유로어치 매수했다고 설명합니다.", False),
    ("작성자는 거래량이 12% 증가했다고 설명하며 추가 시장 반응이 필요하다는 의견을 제시합니다.", True),
])
def test_currency_is_bound_to_retained_amount_without_forcing_omitted_money(summary, expected):
    body = "The fund bought $47 million in BTC and sold €50 million in ETH. Volume increased 12%."
    assert summaries._valid_summary(body, summary) is expected


@pytest.mark.parametrize("body, summary", [
    ("1INCH trading volume increased 12%.", "작성자는 1INCH의 거래량이 12% 증가했다고 설명합니다."),
    ("USD 47 million was spent on BTC.", "작성자는 BTC 매수에 4,700만 달러가 쓰였다고 설명합니다."),
    ("The purchase cost 47 million dollars. Volume increased 12%.", "작성자는 매수 비용이 4,700만달러라고 설명합니다."),
    ("The author follows CFG and BTC; volume increased 12%.", "작성자는 CFG의 거래량이 12% 증가했다고 설명합니다."),
])
def test_valid_identifier_quantities_and_currency_notation_remain_supported(body, summary):
    assert summaries._valid_summary(body, summary)
