"""Pure helpers for the Windows ``ggparrot://`` launch contract.

This module intentionally has no GUI or network imports so its parser can be
tested on non-Windows build hosts that do not ship Tk.
"""
from __future__ import annotations

import re

PROTOCOL_SCHEME = "ggparrot"
PROTOCOL_CLAIM_PATH = "/api/runner/launch-tickets/claim"
_PROTOCOL_URI_RE = re.compile(
    r"^ggparrot://launch\?v=1&ticket=([A-Za-z0-9_-]{43})$"
)


class ProtocolLaunchError(ValueError):
    """A browser launch argument did not match the supported URI shape."""


def parse_protocol_args(args: list[str]) -> str | None:
    """Return the opaque launch ticket from a strict ``--protocol`` invocation.

    The ticket is deliberately the only value accepted from the browser.  In
    particular, macro JSON, account keys, server addresses, and start/live
    commands must never enter the runner through its command line.
    """

    if not args:
        return None
    if args[0] != "--protocol":
        if "--protocol" in args:
            raise ProtocolLaunchError("프로토콜 실행 인자 순서가 올바르지 않습니다.")
        return None
    if len(args) != 2:
        raise ProtocolLaunchError("프로토콜 실행 인자 개수가 올바르지 않습니다.")
    match = _PROTOCOL_URI_RE.fullmatch(args[1])
    if match is None:
        raise ProtocolLaunchError("지원하지 않는 프로토콜 주소입니다.")
    return match.group(1)
