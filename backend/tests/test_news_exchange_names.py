"""고정 사전에 없는 국내 코인의 뉴스 — 거래소 종목 목록의 한글 · 영문 이름으로 검색 · 표시하고, 거르기는 맥락을 요구한다."""
import pytest

from app import news, news_exchange_names as names
from app.data import krw

LISTING = {
    "upbit": {"items": [{"symbol": "KRW-BTC", "base": "BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
                        {"symbol": "KRW-STORY", "base": "STORY", "korean_name": "스토리", "english_name": "Story"}]},
    "bithumb": {"items": [{"symbol": "KRW-ACU", "base": "ACU", "korean_name": "아큐라스트", "english_name": "Acurast"},
                          {"symbol": "KRW-STORY", "base": "STORY", "korean_name": "스토리(빗썸)", "english_name": "Story"}]},
}


@pytest.fixture(autouse=True)
def _listing(monkeypatch):
    names.reset_for_tests()
    monkeypatch.setenv("NEWS_EXCHANGE_NAMES_ENABLED", "true")
    monkeypatch.setattr(krw, "list_symbols", lambda exchange: LISTING[exchange])
    yield
    names.reset_for_tests()


def test_names_come_from_both_exchanges_with_upbit_first():
    names.refresh_names()
    assert names.peek_names("ACU") == ("아큐라스트", "Acurast")
    assert names.peek_names("STORY") == ("스토리", "Story")
    assert names.peek_names("NOPE") == ("", "")


def test_curated_names_always_win_and_unknown_coins_use_the_exchange_name():
    names.refresh_names()
    assert news._coin_ko("BTC") == "비트코인"           # 고정 사전
    assert news._exchange_names("BTC") == ()            # 고정 사전에 있으면 거래소 이름을 끼우지 않는다
    assert news._coin_ko("ACU") == "아큐라스트"
    assert news._coin_ko("ZZZ") == "ZZZ"                # 어디에도 없으면 티커


def test_failed_listing_keeps_the_ticker(monkeypatch):
    def boom(exchange):
        raise RuntimeError("exchange down")
    monkeypatch.setattr(krw, "list_symbols", boom)
    names.refresh_names()
    assert news._coin_ko("ACU") == "ACU"


def test_exchange_names_need_crypto_context_to_count():
    names.refresh_names()
    plain = {"title": "스토리 작가가 쓴 새 소설 출간", "categories": []}
    crypto = {"title": "스토리 코인, 빗썸 원화 마켓 상장 첫날 급등", "categories": []}
    english = {"title": "Acurast token climbs after Bithumb listing", "categories": []}
    assert news._matches_asset(plain, "STORY", news._coin_ko("STORY")) is False
    assert news._matches_asset(crypto, "STORY", news._coin_ko("STORY")) is True
    assert news._matches_asset(english, "ACU", news._coin_ko("ACU")) is True


def test_disabled_switch_skips_the_listing(monkeypatch):
    monkeypatch.setenv("NEWS_EXCHANGE_NAMES_ENABLED", "false")
    called = []
    monkeypatch.setattr(krw, "list_symbols", lambda exchange: called.append(exchange) or LISTING[exchange])
    names.refresh_names()
    assert called == [] and names.peek_names("ACU") == ("", "")
