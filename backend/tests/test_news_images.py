"""Article image lookup for market headlines — pure parsing plus a mocked network."""
import json

import httpx
import pytest

from app import news_images


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    news_images.reset_for_tests()
    monkeypatch.delenv("NEWS_IMAGES_DISABLED", raising=False)
    yield
    news_images.reset_for_tests()


def test_extract_og_image_prefers_og_and_resolves_relative_urls():
    page = """
    <html><head>
      <meta name="twitter:image" content="/t.jpg">
      <meta content="//cdn.example.com/a.png" property="og:image">
    </head></html>"""
    assert news_images.extract_og_image(page, "https://news.example.com/x/y") == "https://cdn.example.com/a.png"
    only_twitter = '<meta name="twitter:image" content="/photos/1.jpg">'
    assert news_images.extract_og_image(only_twitter, "https://m.example.com/page") == "https://m.example.com/photos/1.jpg"
    assert news_images.extract_og_image("<meta property='og:image' content='javascript:alert(1)'>", "https://a.b") == ""
    assert news_images.extract_og_image("<html></html>", "https://a.b") == ""


def test_google_article_id_and_passthrough():
    assert news_images.google_article_id("https://news.google.com/rss/articles/CBMiAbc?oc=5") == "CBMiAbc"
    assert news_images.google_article_id("https://news.google.com/articles/XyZ") == "XyZ"
    assert news_images.google_article_id("https://example.com/articles/1") == ""


def test_parse_decode_response_reads_publisher_url():
    inner = json.dumps(["garturlres", "https://publisher.example.com/story/1", 1])
    body = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]])
    assert news_images.parse_decode_response(body) == "https://publisher.example.com/story/1"
    assert news_images.parse_decode_response("garbage") == ""
    assert news_images.parse_decode_response(")]}'\n\n[]") == ""


def _mock_client(monkeypatch, routes):
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        for prefix, response in routes.items():
            if str(request.url).startswith(prefix):
                return response(request) if callable(response) else response
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    monkeypatch.setattr(news_images, "get_http_client", lambda: client)
    return calls


def test_resolve_now_decodes_google_link_and_attaches_image(monkeypatch):
    inner = json.dumps(["garturlres", "https://publisher.example.com/story/1", 1])
    batch = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner]])
    calls = _mock_client(monkeypatch, {
        "https://news.google.com/articles/ABC": httpx.Response(
            200, text='<c-wiz><div data-n-a-sg="SIG" data-n-a-ts="1700000000"></div></c-wiz>'),
        "https://news.google.com/_/DotsSplashUi/data/batchexecute": lambda request: httpx.Response(
            200, text=batch) if b"garturlreq" in request.content and b"ABC" in request.content else httpx.Response(400),
        "https://publisher.example.com/story/1": httpx.Response(
            200, text='<meta property="og:image" content="https://img.example.com/1.jpg">'),
    })
    items = [{"url": "https://news.google.com/rss/articles/ABC?oc=5", "title": "t"},
             {"url": "https://plain.example.com/no-image", "title": "u"}]
    assert news_images.attach(items) == "pending"
    assert news_images.resolve_now(items) == "ready"
    assert items[0]["image"] == "https://img.example.com/1.jpg"
    assert items[0]["article_url"] == "https://publisher.example.com/story/1"
    assert "image" not in items[1]
    assert [method for method, _url in calls] == ["GET", "POST", "GET", "GET"]
    # 두 번째 호출은 캐시에서 — 네트워크를 다시 쓰지 않는다.
    fresh = [{"url": items[0]["url"]}]
    assert news_images.resolve_now(fresh) == "ready" and fresh[0]["image"] == items[0]["image"]
    assert len(calls) == 4


def test_failures_are_cached_as_no_image_and_never_raise(monkeypatch):
    _mock_client(monkeypatch, {"https://news.google.com/articles/BAD": httpx.Response(500)})
    items = [{"url": "https://news.google.com/rss/articles/BAD?oc=5"}]
    assert news_images.resolve_now(items) == "ready"
    assert "image" not in items[0]


def test_disabled_switch_skips_everything(monkeypatch):
    monkeypatch.setenv("NEWS_IMAGES_DISABLED", "1")
    items = [{"url": "https://news.google.com/rss/articles/ABC?oc=5"}]
    assert news_images.attach(items) == "ready"
    news_images.ensure_resolving(items)
    assert news_images._worker is None


def test_google_block_starts_a_cooldown_and_is_not_cached_as_failure(monkeypatch):
    calls = _mock_client(monkeypatch, {
        "https://news.google.com/articles/ONE": httpx.Response(429, text="sorry"),
        "https://news.google.com/articles/TWO": httpx.Response(200, text="<div></div>"),
    })
    items = [{"url": "https://news.google.com/rss/articles/ONE?oc=5"},
             {"url": "https://news.google.com/rss/articles/TWO?oc=5"}]
    assert news_images.resolve_now(items) == "pending"   # 둘 다 미해결 — 쿨다운 동안 다시 시도한다
    assert len(calls) == 1                                # 차단 뒤 두 번째 기사는 두드리지 않았다
    assert news_images._google_retry_at > 0
    news_images._google_retry_at = 0.0
    assert news_images.resolve_now(items[1:]) == "ready"  # 쿨다운이 끝나면 정상 진행(서명 없음 → 이미지 없음)
