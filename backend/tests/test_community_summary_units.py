"""Summary-only unit regressions from seven captured Square posts."""
import pytest

from app.community_summaries import _valid_summary


@pytest.mark.parametrize("body, summary", [
    ("$AERO volume is spiking on the 4‑hour chart.",
     "작성자는 AERO의 4시간 차트에서 거래량이 증가했다고 설명합니다."),
    ("$WLD reached a fresh 24‑hour high of 0.4808.",
     "작성자는 WLD가 24시간 고점인 0.4808을 기록했다고 설명합니다."),
    ("$AERO is holding its bullish structure on the 15M chart.",
     "작성자는 AERO가 15분 차트에서 상승 추세를 유지한다고 설명합니다."),
    ("$INJ 1小时+15.7%，4小时+34.6%，WLD联动跟涨。",
     "작성자는 INJ가 1시간 동안 15.7%, 4시간 동안 34.6% 올랐고 WLD도 동반 상승했다고 설명합니다."),
    ("$INJ entry: $6.41 – $6.67. Manage size carefully.",
     "작성자는 INJ의 매수 구간을 6.41~6.67달러로 제시합니다."),
    ("$NEAR, $LINK and $INJ are aiming for $5, $30 and $20.",
     "작성자는 NEAR, LINK, INJ의 목표를 각각 5, 30, 20달러로 제시합니다."),
])
def test_actual_time_and_shared_currency_notation_is_equivalent(body, summary):
    assert _valid_summary(body, summary)


@pytest.mark.parametrize("body, summary", [
    ("$AERO gained attention on the 15M chart.", "작성자는 AERO가 1,500만 규모의 거래를 기록했다고 설명합니다."),
    ("$AERO raised $15M in funding.", "작성자는 AERO가 15분 동안 자금을 모았다고 설명합니다."),
    ("$AERO volume 15M appears on the chart.", "작성자는 AERO의 15분 차트를 분석했다고 설명합니다."),
    ("$AERO volume is spiking on the 4‑hour chart.", "작성자는 AERO의 4분 차트에서 거래량이 증가했다고 설명합니다."),
    ("$INJ 1小时+15.7%，4小时+34.6%。", "작성자는 INJ가 1분 동안 15.7% 올랐다고 설명합니다."),
    ("$INJ entry: $6.41 – $6.67.", "작성자는 INJ의 매수 구간을 6.41~6.67로 제시합니다."),
    ("$INJ entry: $6.41 – $6.67. Fees cost €1.", "작성자는 INJ의 매수 구간을 6.41~6.67유로로 제시합니다."),
    ("$INJ entry: $6.41 – $6.67.", "작성자는 BTC의 매수 구간을 6.41~6.67달러로 제시합니다."),
    ("$INJ entry: $6.41 – $6.67.", "작성자는 INJ의 매수 구간을 6.41~6.67달러로 제시하며 시장 상황에 따라 변할 수 있다고 설명합니다. Buy now guaranteed profits."),
    ("The fund spent $47 million and fees were $2.", "작성자는 펀드 지출이 4,700만 규모이며 수수료가 2달러라고 설명합니다."),
])
def test_unit_currency_asset_corruption_and_prose_remain_rejected(body, summary):
    assert not _valid_summary(body, summary)


@pytest.mark.parametrize("body, summary", [
    ("$AERO raised $15M in funding.", "작성자는 AERO가 1,500만 달러를 조달했다고 설명합니다."),
    ("$INJ 15分钟涨幅12%，1小時漲幅19%。", "작성자는 INJ가 15분 동안 12%, 1시간 동안 19% 상승했다고 설명합니다."),
    ("$INJ price range 6.41–6.67 USDT.", "작성자는 INJ의 가격 구간을 6.41~6.67 USDT로 제시합니다."),
    ("$AERO offers 10x leverage with entry at 0.6503 and stop at 0.575.", "작성자는 AERO의 10배 레버리지 매수에 0.6503 진입과 0.575 손절을 제시합니다."),
])
def test_unchanged_money_crypto_units_and_leverage_still_work(body, summary):
    assert _valid_summary(body, summary)
