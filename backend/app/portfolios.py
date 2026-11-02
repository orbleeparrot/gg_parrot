"""묶음 — **구성이 아니라 기준**을 들고 있다가 거래소마다 그때 푼다.

설계: docs/superpowers/specs/2026-10-30-portfolio-presets-design.md
      docs/superpowers/specs/2026-11-02-sector-bundles-design.md (분야 묶음)

묶음은 두 가지다. **분야 묶음**(``kind="sector"``)은 레이어1끼리, 디파이끼리처럼 한
분야로 묶은 것이고, **기준 묶음**(``kind="size"``)은 시가총액·거래대금처럼 분야가 아닌
기준으로 묶은 것이다. 물어볼까는 분야 쪽만 쓰고, 빌더는 둘을 두 줄로 나눠 그린다.

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
from .data import coingecko, krw, symbols as symbols_mod

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
class Stats:
    """묶음 전체를 한눈에 보는 숫자 둘. **받아 둔 티커로만 구한다** — 호출이 늘지 않는다.

    한 레그라도 숫자를 모르면 아예 안 만든다(``None``). 아는 것만으로 평균을 내면
    다섯 종목 묶음에 셋의 평균이 적히고, 화면은 그걸 다섯의 평균으로 읽는다.
    """

    rank_min: int           # 거래대금 순위가 가장 높은 레그
    rank_max: int           # 가장 낮은 레그
    range_avg_pct: float    # 레그마다 (고가-저가)/저가, 그 평균

    def as_dict(self) -> dict:
        return {"rank_min": self.rank_min, "rank_max": self.rank_max,
                "range_avg_pct": self.range_avg_pct}


@dataclass(frozen=True)
class Portfolio:
    key: str
    name: str
    lead: str
    tags: tuple[str, ...]
    legs: tuple[Leg, ...]
    # "sector" 는 한 분야로 묶은 것, "size" 는 크기·거래대금처럼 분야가 아닌 기준.
    # 빌더가 두 줄로 나눠 그리고, 물어볼까는 분야 쪽만 쓴다.
    kind: str = "size"
    stats: Optional[Stats] = None

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "lead": self.lead,
                "tags": list(self.tags), "count": len(self.legs), "kind": self.kind,
                "stats": self.stats.as_dict() if self.stats else None,
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
    by_volume: tuple[str, ...]              # 24시간 거래대금 내림차순 티커
    rank_of: dict[str, int]                 # 티커 → 거래대금 순위(1부터)
    range_of: dict[str, float]              # 티커 → 하루 변동폭(%)

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

    def busiest(self, count: int) -> list[str]:
        out: list[str] = []
        for base in self.by_volume:
            if base in out:
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
    pick: Callable[[_Pool], list[str]]
    # 이름이 종목 수를 약속하면 ``size``, 아니면 0. "TOP5" 는 다섯이 아니면 거짓말이지만
    # "디파이" 는 셋이든 다섯이든 참말이다. 약속이 없는 쪽은 ``least`` 만 지킨다.
    size: int = 0
    least: int = 0
    kind: str = "size"


def _sector_mix(pool: _Pool) -> list[str]:
    """레이어1 1 · 디파이 2 · 밈 1 — 분야를 섞어 담는다.

    디파이에서 둘을 뽑는 것은 레이어1 과 밈이 각각 한 종목으로 분야를 대표할 수 있기
    때문이다(BTC · DOGE). 디파이는 대표가 하나로 좁혀지지 않는다.
    """
    layer1 = pool.in_category(coingecko.CATEGORY_LAYER1, 1)
    defi = pool.in_category(coingecko.CATEGORY_DEFI, 2, skip=tuple(layer1))
    meme = pool.in_category(coingecko.CATEGORY_MEME, 1, skip=tuple(layer1 + defi))
    return layer1 + defi + meme


# 한 분야에서 몇 개까지 담고, 몇 개 아래면 안 내는가.
# 둘짜리는 분야가 아니라 그냥 두 종목이다 — 이름이 분야를 말하는데 내용은 아니게 된다.
SECTOR_TAKE = 5
SECTOR_LEAST = 3

# 분야 묶음. 설명은 **그 분야가 무엇인지**만 말한다 — 어떻게 움직일지는 말하지 않는다.
_SECTORS: tuple[tuple[str, str, str, str], ...] = (
    ("layer1", coingecko.CATEGORY_LAYER1, "레이어1", "자체 블록체인을 가진 코인들이에요."),
    ("layer2", coingecko.CATEGORY_LAYER2, "레이어2", "이더리움 위에서 수수료와 속도를 맡는 코인들이에요."),
    ("defi", coingecko.CATEGORY_DEFI, "디파이", "거래소·대출 같은 금융을 코드로 돌리는 코인들이에요."),
    ("meme", coingecko.CATEGORY_MEME, "밈", "쓰임새보다 유행에서 시작한 코인들이에요."),
    ("gaming", coingecko.CATEGORY_GAMING, "게임", "게임과 그 안의 아이템에 쓰이는 코인들이에요."),
    ("ai", coingecko.CATEGORY_AI, "AI", "인공지능 연산과 데이터에 쓰이는 코인들이에요."),
)


def _sector_rules() -> tuple[_Rule, ...]:
    # 기본값 인자로 slug 를 묶는다 — 람다가 반복 변수를 들여다보면 여섯이 전부 마지막
    # 분야를 가리킨다.
    return tuple(
        _Rule(key=f"sector_{key}", name=name, lead=lead, tags=("분야",),
              least=SECTOR_LEAST, kind="sector",
              pick=lambda pool, slug=slug: pool.in_category(slug, SECTOR_TAKE))
        for key, slug, name, lead in _SECTORS
    )


RULES: tuple[_Rule, ...] = _sector_rules() + (
    _Rule(
        key="cap_top10", name="시가총액 TOP10", size=10,
        lead="그 거래소에서 살 수 있는 시가총액 상위 10종목으로 넓게 나눠 담아요.",
        tags=("메이저", "분산투자"),
        pick=lambda pool: pool.top(10),
    ),
    _Rule(
        key="volume_top5", name="거래대금 TOP5", size=5,
        lead="어제 하루 돈이 가장 많이 오간 5종목이에요. 사고팔기 쉬운 쪽이에요.",
        tags=("거래활발",),
        pick=lambda pool: pool.busiest(5),
    ),
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
    # 분야 하나가 아니라 **여럿을 섞은** 것이다. 분야 줄이 옆에 생기고 나면 예전 이름
    # ("분야별로 골라담은 TOP4")은 분야 하나를 고른 것처럼 읽힌다.
    _Rule(
        key="mixed_top4", name="여러 분야 섞어담기", size=4,
        lead="레이어1 · 디파이 · 밈에서 각 분야의 시가총액 상위를 골라 섞어요.",
        tags=("섹터대표", "분산투자"),
        pick=_sector_mix,
    ),
    # "디파이 대표 TOP3" 는 2026-11-02 에 뺐다 — 분야 묶음의 디파이 칸과 같은 것이라
    # 두 줄에 같은 묶음이 앉았다.
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


def _ticker_facts(exchange: str, keep: set[str]) -> tuple[tuple[str, ...], dict[str, int], dict[str, float]]:
    """거래대금 내림차순 티커와, 티커마다의 순위·하루 변동폭.

    우리가 이미 받아 둔 캐시만 쓴다(추가 호출 없음). 바이낸스 선물 묶음도 **현물
    거래대금**으로 줄 세운다 — 선물 거래대금은 따로 받아야 하는데, 둘의 순서가 크게
    다르지 않고 어차피 선물에 상장된 것만 남긴다.

    변동폭은 고가·저가가 다 있는 종목만 담는다. 없는 자리를 0 으로 메우면 묶음 평균이
    조용히 내려간다.
    """
    if is_domestic(exchange):
        rows = krw.get_all_tickers(exchange)
    else:
        rows = hotcoins.get_cached_tickers() or []
    scored: list[tuple[float, str]] = []
    range_of: dict[str, float] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        base = symbol[4:] if symbol.startswith("KRW-") else symbol[:-4]
        if base not in keep:
            continue
        try:
            volume = float(row.get("quoteVolume"))
        except (TypeError, ValueError):
            continue
        if volume <= 0:
            continue
        scored.append((volume, base))
        try:
            low, high = float(row["lowPrice"]), float(row["highPrice"])
        except (KeyError, TypeError, ValueError):
            continue
        if low > 0 and high >= low:
            range_of[base] = round((high - low) / low * 100, 2)
    scored.sort(key=lambda pair: pair[0], reverse=True)
    by_volume: list[str] = []
    for _volume, base in scored:
        if base not in by_volume:       # 같은 티커가 두 줄로 오면 앞의 것이 참이다
            by_volume.append(base)
    return tuple(by_volume), {base: rank for rank, base in enumerate(by_volume, start=1)}, range_of


def _stats(pool: _Pool, bases: list[str]) -> Optional[Stats]:
    """그 묶음의 숫자 두 칸. 한 레그라도 모르면 ``None``."""
    ranks = [pool.rank_of[base] for base in bases if base in pool.rank_of]
    ranges = [pool.range_of[base] for base in bases if base in pool.range_of]
    if len(ranks) != len(bases) or len(ranges) != len(bases):
        return None
    return Stats(rank_min=min(ranks), rank_max=max(ranks),
                 range_avg_pct=round(sum(ranges) / len(ranges), 2))


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
    for _key, slug, _name, _lead in _SECTORS:
        categories[slug] = frozenset(coingecko.category_ids(slug) or ())
    usable = {base for base in ordered}
    by_volume, rank_of, range_of = _ticker_facts(exchange, usable)
    return _Pool(exchange=exchange, ranked=tuple(ordered), owner_id=owner_id,
                 tradable=frozenset(tradable), category_ids=categories,
                 by_volume=by_volume, rank_of=rank_of, range_of=range_of)


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
        # 엔진이 받아 주지 않는 크기 — 상한이 오르면 저절로 살아난다
        if (rule.size or rule.least) > MAX_LEGS:
            continue
        try:
            bases = rule.pick(pool)
        except Exception:
            log.warning("portfolios: %s 를 풀지 못했다", rule.key, exc_info=True)
            continue
        if rule.size:
            if len(bases) != rule.size:
                continue
        else:
            bases = bases[:MAX_LEGS]
            if len(bases) < rule.least:
                continue
        weights = _equal_weights(len(bases))
        out.append(Portfolio(
            key=rule.key, name=rule.name, lead=rule.lead, tags=rule.tags,
            kind=rule.kind, stats=_stats(pool, bases),
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
