"""'오늘의 코인동향' — 무료 뉴스 요약 (Google News RSS 기반).

정보 제공용이며 투자자문이 아니다. 설계 가드레일(사용자 합의):
  1) 중립·사실 위주: 실제 헤드라인을 그대로 링크로 보여준다(팩트).
  2) 저작권: 기사 전문을 복제하지 않는다 — 제목·매체·시각 + '원문 링크'만.
  3) 환각 방지: AI 개요는 '주어진 헤드라인'에서만 요약하게 하고(모델 지식으로
     새 사실 생성 금지), 실패하면 개요 없이 헤드라인만 보여준다.
  4) 비용: KST 기준 하루 1회만 요약해 메모리에 캐시(방문마다 호출 X).

무료 소스로 Google News RSS를 쓴다 — API 키가 필요 없고, 한국어 검색 쿼리로
시장·규제 동향은 물론 코인별(경주마) 뉴스까지 뽑을 수 있다.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

_GOOGLE_NEWS = "https://news.google.com/rss/search"
_HTTP_TIMEOUT = 10.0
_MAX_ITEMS = 8
_COIN_CACHE_SECONDS = max(60, int(os.environ.get("COIN_NEWS_CACHE_SECONDS", "300")))

# 시장·규제 전반 쿼리. Google News 검색 연산자 when:2d 로 최근 이틀로 제한.
_MARKET_QUERY = "암호화폐 OR 가상자산 OR 비트코인 규제 OR 동향 when:2d"

# 코린이가 아는 흔한 티커의 한글명 — 한국어 뉴스 적중률을 높인다. 없으면 티커 그대로.
_COIN_KO = {
    "BTC": "비트코인", "ETH": "이더리움", "XRP": "리플", "SOL": "솔라나",
    "DOGE": "도지코인", "ADA": "에이다", "TRX": "트론", "AVAX": "아발란체",
    "LINK": "체인링크", "DOT": "폴카닷", "MATIC": "폴리곤", "SHIB": "시바이누",
    "BCH": "비트코인캐시", "LTC": "라이트코인", "ATOM": "코스모스", "ETC": "이더리움클래식",
    "APT": "앱토스", "ARB": "아비트럼", "OP": "옵티미즘", "SUI": "수이",
    "PEPE": "페페", "USDT": "테더", "BNB": "바이낸스코인",
    "UNI": "유니스왑", "AAVE": "에이브", "MKR": "메이커", "SAND": "샌드박스",
    "MANA": "디센트럴랜드", "AXS": "엑시인피니티", "GRT": "더그래프", "ALGO": "알고랜드",
    "FIL": "파일코인", "ICP": "인터넷컴퓨터", "NEAR": "니어프로토콜", "INJ": "인젝티브",
    "RUNE": "토르체인", "STX": "스택스", "IMX": "이뮤터블", "ONDO": "온도파이낸스",
    "ZEC": "지캐시", "XLM": "스텔라루멘", "HBAR": "헤데라", "VET": "비체인",
}

# 요약(개요)은 Anthropic 키가 있을 때만. 시장 페이지에 하루 1회.
_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")

_DISCLAIMER = (
    "정보 제공용이며 투자자문이 아닙니다. 요약은 AI가 생성했을 수 있으니 "
    "반드시 원문을 확인하세요."
)

# 일 1회 캐시: key -> (envelope, kst_date_str). 인스턴스 재시작 시 재생성(허용).
_cache: dict[str, tuple[dict, str]] = {}
# 종목 뉴스는 에이전트 화면에서 자동 확인하므로 시장 일일 브리핑과 분리한다.
# 전역 5분 TTL로 Google RSS 호출을 억제하면서도 장중 새 헤드라인을 반영한다.
_coin_cache: dict[str, tuple[dict, float]] = {}


def _kst_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=9)


def _kst_date() -> str:
    return _kst_now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# RSS 파싱 (순수 함수 — 테스트 가능, I/O 없음)
# ---------------------------------------------------------------------------
def _clean_title(title: str, source: Optional[str]) -> str:
    """Google News 제목은 보통 '기사제목 - 매체명' 형태 — 매체명 꼬리를 떼어낸다."""
    t = (title or "").strip()
    if source and t.endswith(" - " + source):
        t = t[: -(len(source) + 3)].strip()
    elif " - " in t:
        # 매체 원소가 없을 때의 폴백: 마지막 ' - 매체' 조각 제거
        head, _, _tail = t.rpartition(" - ")
        if head:
            t = head.strip()
    return t


def _fmt_published(dt: Optional[datetime]) -> str:
    """KST 기준 상대 시각(예: '3시간 전', '어제')."""
    if dt is None:
        return ""
    now = datetime.now(timezone.utc)
    delta = now - dt
    secs = delta.total_seconds()
    if secs < 0:
        secs = 0
    if secs < 3600:
        return f"{int(secs // 60)}분 전"
    if secs < 86400:
        return f"{int(secs // 3600)}시간 전"
    days = int(secs // 86400)
    if days == 1:
        return "어제"
    return f"{days}일 전"


def _parse_rss(xml_text: str, *, limit: int = _MAX_ITEMS) -> list[dict]:
    """RSS XML -> [{title, source, url, published, published_display}]. 중복 제거."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[dict] = []
    seen: set[str] = set()
    for item in root.iterfind(".//item"):
        link = (item.findtext("link") or "").strip()
        raw_title = (item.findtext("title") or "").strip()
        if not link or not raw_title:
            continue
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        title = _clean_title(raw_title, source)
        key = title.lower()
        if not title or key in seen:
            continue
        seen.add(key)
        pub_raw = (item.findtext("pubDate") or "").strip()
        published_dt = None
        if pub_raw:
            try:
                published_dt = parsedate_to_datetime(pub_raw)
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published_dt = None
        items.append(
            {
                "title": title,
                "source": source,
                "url": link,
                "published": published_dt.astimezone(timezone.utc).isoformat() if published_dt else None,
                "published_display": _fmt_published(published_dt),
            }
        )
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# 네트워크 + 요약
# ---------------------------------------------------------------------------
def _fetch_news(query: str, *, limit: int = _MAX_ITEMS) -> list[dict]:
    params = {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(_GOOGLE_NEWS, params=params)
            resp.raise_for_status()
            return _parse_rss(resp.text, limit=limit)
    except Exception:
        return []


def _summarize(items: list[dict], *, label: str) -> Optional[str]:
    """헤드라인만 근거로 한 중립 개요(3~4줄). 실패하면 None(개요 생략)."""
    if not items or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        headlines = "\n".join(f"- {it['title']} ({it['source']})" for it in items)
        system = (
            "너는 코인 초보(코린이)에게 '오늘의 코인 동향'을 짚어주는 도우미야. "
            "규칙: (1) 반드시 한국어, 쉬운 말. (2) 아래에 '주어진 헤드라인'에서 드러난 "
            "사실만 요약하고, 목록에 없는 내용이나 새 수치·가격 예측을 절대 지어내지 마. "
            "(3) '사라/팔아라/오른다/추천·수익보장' 같은 투자 조언·전망은 하지 마. "
            "(4) 3~4줄, 오늘 무슨 흐름·이슈가 있었는지 중립적으로. "
            "(5) 어려운 용어가 있으면 한 번만 괄호로 짧게 풀어줘."
        )
        user = (
            f"오늘의 {label} 관련 헤드라인이야. 이걸 근거로 오늘 흐름을 3~4줄로 "
            f"중립 요약해줘(제목 재나열 말고 종합):\n{headlines}"
        )
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = (block.text or "").strip()
                return text or None
    except Exception:
        return None
    return None


def _envelope(items: list[dict], *, overview: Optional[str], label: str, query: str) -> dict:
    return {
        "as_of": _kst_date(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": label,
        "overview": overview,
        "ai": overview is not None,
        "items": items,
        "query": query,
        "disclaimer": _DISCLAIMER,
    }


def get_market_news() -> dict:
    """시장·규제 전반 동향 — 헤드라인 + AI 중립 개요. KST 하루 1회 캐시."""
    day = _kst_date()
    hit = _cache.get("market")
    if hit and hit[1] == day:
        return hit[0]
    items = _fetch_news(_MARKET_QUERY)
    overview = _summarize(items, label="코인 시장·규제") if items else None
    env = _envelope(items, overview=overview, label="코인 시장·규제 동향", query=_MARKET_QUERY)
    if items:  # 빈 결과는 캐시하지 않음(일시적 실패일 수 있음)
        _cache["market"] = (env, day)
    return env


def get_coin_news(symbol: str) -> dict:
    """코인별(경주마) 최신 뉴스 헤드라인. 개요 없이 목록만 → 무료·환각 없음. 캐시."""
    base = (symbol or "").upper().removesuffix("USDT").removesuffix("BUSD").removesuffix("USDC")
    if not base:
        return _envelope([], overview=None, label="코인 뉴스", query="")
    name = _COIN_KO.get(base, base)
    ckey = f"coin:{base}"
    hit = _coin_cache.get(ckey)
    if hit and hit[1] > time.time():
        return hit[0]
    query = f"{name} 코인 when:7d"
    items = _fetch_news(query, limit=6)
    env = _envelope(items, overview=None, label=f"{name} 뉴스", query=query)
    env["symbol"] = base
    env["coin_name"] = name
    env["refresh_seconds"] = _COIN_CACHE_SECONDS
    if items:
        _coin_cache[ckey] = (env, time.time() + _COIN_CACHE_SECONDS)
    return env
