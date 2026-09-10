"""Local, write-time Korean profanity checks for community text."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unicodedata

from korcen import korcen

_ROOT = Path(__file__).resolve().parent
korcen.set_custom_filter_paths(
    include_path=str(_ROOT / "profanity_include.txt"),
    exclude_path=str(_ROOT / "profanity_exclude.txt"),
)
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_HANGUL_DIGITS = re.compile(r"(?<=[가-힣ㄱ-ㅎㅏ-ㅣ])[0-9]+(?=[가-힣ㄱ-ㅎㅏ-ㅣ])")
_BLOCKS = {"p", "br", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "pre", "hr"}


class _VisibleText(HTMLParser):
    """Read sanitized HTML; inline formatting must not split a word."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCKS:
            self.parts.append("\n")
        elif tag == "img":
            self.parts.append(" " + (dict(attrs).get("alt") or "") + " ")

    def handle_endtag(self, tag):
        if tag in _BLOCKS:
            self.parts.append("\n")


def require_clean_text(text: str, field: str, *, html: bool = False) -> None:
    """Reject without modifying stored text or retaining submitted content.

    HTML callers must pass the sanitized, final body. No member ID is passed
    to korcen: its optional ID cache can disable detection when unconfigured.
    """
    if html:
        parser = _VisibleText()
        parser.feed(text)
        parser.close()
        text = "".join(parser.parts)
    # Keep korcen's URL exemption; scan link labels and image fallback text.
    text = _URL.sub("", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    # NFC handles decomposed Hangul. Also check NFKC without losing the
    # compatibility-jamo spellings (e.g. ㅅㅂ) understood by the library.
    variants = {unicodedata.normalize("NFC", text), unicodedata.normalize("NFKC", text)}
    variants |= {
        "".join(char for char in value if not unicodedata.category(char).startswith("P"))
        for value in variants
    }
    # Remove digits only between Korean letters (시1발), keeping prices,
    # percentages and Latin ticker symbols in their original form.
    variants |= {_HANGUL_DIGITS.sub("", value) for value in variants}
    if any(korcen.check(value) for value in variants if value):
        raise ValueError(f"{field}에 비속어가 포함되어 있어요. 표현을 수정해 주세요.")
