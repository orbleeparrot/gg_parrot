"""추천 묶음 — **구성이 아니라 기준**을 들고 있다가 거래소마다 그때 푼다.

설계: docs/superpowers/specs/2026-10-30-portfolio-presets-design.md

왜 구성을 적어 두지 않는가: "시가총액 TOP10" 을 종목으로 박아 두면 순위가 바뀌는 순간
틀린 이름이 되고, 거래소마다 상장 종목이 달라 그대로 쓸 수도 없다(BNB 는 업비트에 없다).
기준만 들고 있으면 거래소가 달라도 규칙이 알아서 메우고, 이름도 계속 참말이다.

이 모듈은 HTTP 도 DB 도 직접 모른다 — 종목 목록은 ``data.symbols``, 시가총액 순위는
``data.coingecko`` 에서 받아 **골라내기만** 한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from engine.exchanges import is_domestic, normalize_exchange
from engine.schema import Macro

from . import hotcoins
from .data import coingecko, symbols as symbols_mod

log = logging.getLogger(__name__)

# 한 묶음에 들어갈 수 있는 종목 수 — 엔진이 정한다. 여기서 따로 정하면 두 벌이 되어,
# 화면에는 보이는데 매크로를 만들 때 거절당하는 묶음이 생긴다.
MAX_LEGS = Macro.MAX_SYMBOLS

# 거래유의로 찍힌 종목은 추천에서 뺀다. 거래소가 손을 든 종목을 우리가 권할 이유가 없다.
# (국내 거래소만 이 표시를 준다 — ``data/krw.py`` 의 ``warning``.)
SKIP_WARNED = True

# 스테이블 · 레버리지 토큰 판정은 ``hotcoins`` 가 이미 들고 있다. 여기 또 만들면 한쪽만
# 갱신되어 어긋난다. USDT 는 바이낸스의 거래 통화라 그 목록에 없어 따로 더한다.
_EXCLUDED_BASES = hotcoins._STABLE_BASES | {"USDT", "KRW"}


@dataclass(frozen=True)
class Leg:
    symbol: str
    weight: float


@dataclass(frozen=True)
class Portfolio:
    key: str
    name: str
    lead: str
    tags: tuple[str, ...]
    legs: tuple[Leg, ...]

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "lead": self.lead,
                "tags": list(self.tags), "count": len(self.legs),
                "legs": [{"symbol": leg.symbol, "weight": leg.weight} for leg in self.legs]}


@dataclass(frozen=True)
class _Pool:
    """그 거래소에서 살 수 있는 종목을, 시가총액 큰 것부터 줄 세운 것.

    티커마다 **주인 id** 를 함께 들고 있다. 범주 판정을 티커로 하면 같은 티커를 쓰는 다른
    코인에 걸려 엉뚱한 종목이 들어온다(코인게코의 티커는 고유하지 않다).
    """

    exchange: str
    ranked: tuple[str, ...]                 # 티커(base)만. 시가총액 내림차순.
    owner_id: dict[str, str]                # 티커 → 그 티커의 주인 코인 id
    tradable: frozenset[str]
    category_ids: dict[str, frozenset[str]] # 범주 slug → 그 범주 코인들의 id

    def top(self, count: int, *, skip: tuple[str, ...] = ()) -> list[str]:
        out: list[str] = []
        for base in self.ranked:
            if base in skip or base in out:
                continue
            out.append(base)
            if len(out) == count:
                break
        return out

    def in_category(self, slug: str, count: int, *, skip: tuple[str, ...] = ()) -> list[str]:
        ids = self.category_ids.get(slug) or frozenset()
        out: list[str] = []
        for base in self.ranked:          # 순위 순서를 그대로 쓴다 — 범주 안에서도 시총 순
            if base in skip or base in out or self.owner_id.get(base) not in ids:
                continue
            out.append(base)
            if len(out) == count:
                break
        return out

    def keep(self, bases: tuple[str, ...]) -> list[str]:
        """골라 둔 종목 중 그 거래소에 있는 것만."""
        return [base for base in bases if base in self.tradable]


# --- 묶음의 기준 --------------------------------------------------------------
#
# ``size`` 는 **이 이름이 약속하는 종목 수**다. 다 못 채우면 그 거래소에서는 아예 내지
# 않는다 — "TOP5" 라고 해 놓고 셋만 주면 이름이 거짓말이 된다.

@dataclass(frozen=True)
class _Rule:
    key: str
    name: str
    lead: str
    tags: tuple[str, ...]
    size: int
    pick: Callable[[_Pool], list[str]]


def _sector_mix(pool: _Pool) -> list[str]:
    """레이어1 1 · 디파이 2 · 밈 1 — 분야를 섞어 담는다.

    디파이에서 둘을 뽑는 것은 레이어1 과 밈이 각각 한 종목으로 분야를 대표할 수 있기
    때문이다(BTC · DOGE). 디파이는 대표가 하나로 좁혀지지 않는다.
    """
    layer1 = pool.in_category(coingecko.CATEGORY_LAYER1, 1)
    defi = pool.in_category(coingecko.CATEGORY_DEFI, 2, skip=tuple(layer1))
    meme = pool.in_category(coingecko.CATEGORY_MEME, 1, skip=tuple(layer1 + defi))
    return layer1 + defi + meme


RULES: tuple[_Rule, ...] = (
    _Rule(
        key="cap_top5", name="시가총액 TOP5", size=5,
        lead="그 거래소에서 살 수 있는 시가총액 상위 5종목으로 나눠 담아요.",
        tags=("메이저", "분산투자"),
        pick=lambda pool: pool.top(5),
    ),
    _Rule(
        key="btc_eth_duo", name="비트코인 이더리움 듀오", size=2,
        lead="가장 오래되고 가장 큰 둘로만 반반 담아요.",
        tags=("메이저",),
        pick=lambda pool: pool.keep(("BTC", "ETH")),
    ),
    _Rule(
        key="sector_top4", name="분야별로 골라담은 TOP4", size=4,
        lead="레이어1 · 디파이 · 밈에서 각 분야의 시가총액 상위를 골라 섞어요.",
        tags=("섹터대표", "분산투자"),
        pick=_sector_mix,
    ),
    _Rule(
        key="defi_top3", name="디파이 대표 TOP3", size=3,
        lead="디파이 분야에서 시가총액 상위 3종목만 담아요.",
        tags=("섹터대표",),
        pick=lambda pool: pool.in_category(coingecko.CATEGORY_DEFI, 3),
    ),
)


def _tradable_bases(exchange: str, market: str) -> set[str]:
    """그 거래소에서 **매매 후보로 쓸 수 있는** 티커.

    스테이블 · 레버리지 토큰 · (국내) 거래유의 종목은 여기서 빠진다.
    """
    listing = symbols_mod.list_symbols(exchange=exchange)
    out: set[str] = set()
    for row in listing.get("items") or []:
        base = str(row.get("base") or "").upper()
        if not base or base in _EXCLUDED_BASES or hotcoins._is_leverage_token(base):
            continue
        if SKIP_WARNED and row.get("warning"):
            continue
        if market == "futures" and not row.get("futures"):
            continue
        if market != "futures" and not row.get("spot"):
            continue
        out.add(base)
    return out


def _pool(exchange: str, market: str) -> Optional[_Pool]:
    """순위를 못 받으면 ``None`` — 그때는 추천 묶음을 아예 내지 않는다."""
    rows = coingecko.ranked()
    if not rows:
        return None
    tradable = _tradable_bases(exchange, market)
    if not tradable:
        return None
    # 포장 자산 · 스테이킹 파생은 같은 자산을 한 번 더 담는 것이라 분산이 아니다.
    # **id 로 견준다** — 티커로 거르면 티커가 그냥 "BTC" 인 포장 토큰 때문에 진짜
    # 비트코인이 지워진다(실제로 그랬다).
    derivatives = coingecko.derivative_ids()
    # 티커의 주인은 **처음 나온 것**(= 시가총액이 가장 큰 것)이다. 그냥 dict 로 모으면
    # 뒤에 나온 사칭 티커가 주인을 덮어써서, 분야 판정이 엉뚱한 코인에 걸린다.
    owner_id: dict[str, str] = {}
    ordered: list[str] = []
    for row in rows:
        base, coin_id = row["symbol"], row["id"]
        if base in owner_id:
            continue
        owner_id[base] = coin_id
        if base in tradable and coin_id not in derivatives:
            ordered.append(base)
    categories: dict[str, frozenset[str]] = {}
    for slug in (coingecko.CATEGORY_LAYER1, coingecko.CATEGORY_DEFI, coingecko.CATEGORY_MEME):
        categories[slug] = frozenset(coingecko.category_ids(slug) or ())
    return _Pool(exchange=exchange, ranked=tuple(ordered), owner_id=owner_id,
                 tradable=frozenset(tradable), category_ids=categories)


def _equal_weights(count: int) -> list[float]:
    """균등 비중. **합이 정확히 100 이어야 한다** — 스키마가 0.01 까지만 봐준다.

    100/3 처럼 나누어떨어지지 않으면 마지막 몫이 나머지를 받는다.
    """
    each = round(100.0 / count, 4)
    weights = [each] * (count - 1)
    weights.append(round(100.0 - sum(weights), 4))
    return weights


def _symbol(exchange: str, base: str) -> str:
    return f"KRW-{base}" if is_domestic(exchange) else f"{base}USDT"


def build(exchange: str = "binance", market: str = "spot") -> list[Portfolio]:
    """그 거래소·시장에서 지금 쓸 수 있는 추천 묶음. 못 만들면 빈 목록.

    이름이 약속한 종목 수를 못 채우거나 엔진의 묶음 상한을 넘는 기준은 **조용히
    줄이지 않고 통째로 뺀다** — "TOP5" 가 세 종목이면 그 이름이 거짓말이 된다.
    """
    exchange = normalize_exchange(exchange)
    pool = _pool(exchange, market)
    if pool is None:
        return []
    out: list[Portfolio] = []
    for rule in RULES:
        if rule.size > MAX_LEGS:
            continue  # 엔진이 받아 주지 않는 크기 — 상한이 오르면 저절로 살아난다
        try:
            bases = rule.pick(pool)
        except Exception:
            log.warning("portfolios: %s 를 풀지 못했다", rule.key, exc_info=True)
            continue
        if len(bases) != rule.size:
            continue
        weights = _equal_weights(len(bases))
        out.append(Portfolio(
            key=rule.key, name=rule.name, lead=rule.lead, tags=rule.tags,
            legs=tuple(Leg(symbol=_symbol(exchange, base), weight=weight)
                       for base, weight in zip(bases, weights)),
        ))
    return out


def view(exchange: str = "binance", market: str = "spot") -> dict:
    """화면이 받는 모양. 묶음이 없으면 왜 없는지 한 줄 담는다."""
    items = build(exchange, market)
    return {
        "exchange": exchange, "market": market,
        "items": [item.as_dict() for item in items],
        "max_symbols": MAX_LEGS,
        "note": "" if items else "추천 묶음을 지금 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.",
    }
