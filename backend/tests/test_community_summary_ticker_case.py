"""Explicit Square ticker tags are case-insensitive, unlike monetary suffixes."""
from app.community_summaries import _summary_unit_text, _valid_summary


BODY = (
    "RED DOESN’T MEAN THE RACE IS OVER. 🔥\n"
    "$DASH $MARSCOIN , $ARB , #chip ,#HEMI and #crv are bleeding today.\n\n"
    "But the real question is not who fell hardest, it’s who can rise first?\n\n"
    "Which one has the strongest comeback story? 👇"
)
SUMMARY = (
    "작성자는 DASH, MARSCOIN, ARB, CHIP, HEMI, CRV 등이 현재 하락세를 보이고 있지만, "
    "낙폭이 아닌 반등 가능성을 주목할 가치가 있다고 주장한다. 어느 코인이 가장 먼저 강한 "
    "반등을 보일지가 중요하다는 의견이다."
)


def test_captured_korean_summary_accepts_lowercase_explicit_unknown_asset_tag():
    assert _valid_summary(BODY, SUMMARY)


def test_mixed_case_explicit_tags_keep_alphanumeric_identifiers():
    assert _summary_unit_text("#CrV and $new123 are moving") == "#CRV and $NEW123 are moving"
    assert _valid_summary("#CrV and $new123 are moving.", "작성자는 CRV와 NEW123의 가격 움직임에 주목한다고 설명합니다.")


def test_money_and_untagged_words_are_not_capitalized_as_assets():
    source = "crv discussion; funding $15m; #1inch; prefix#crv_suffix"
    assert _summary_unit_text(source) == source


def test_lowercase_monetary_suffix_keeps_value_and_currency():
    body = "$crv raised $15m in funding."
    assert _valid_summary(body, "작성자는 CRV가 1,500만 달러를 조달했다고 설명합니다.")
    assert not _valid_summary(body, "작성자는 CRV가 15분 동안 자금을 조달했다고 설명합니다.")


def test_case_normalization_does_not_authorize_different_asset_or_english_prose():
    assert not _valid_summary("#crv is moving.", "작성자는 BTC의 가격 움직임에 주목한다고 설명합니다.")
    assert not _valid_summary("#crv is moving.", "작성자는 CRV의 가격 움직임에 주목한다고 설명하며 시장 상황에 따라 달라질 수 있다는 의견을 제시합니다. Buy now guaranteed profits.")
