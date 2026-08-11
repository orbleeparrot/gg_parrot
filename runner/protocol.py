"""Pure helpers for the Windows ``ggparrot://`` launch contract.

This module intentionally has no GUI or network imports so its parser can be
tested on non-Windows build hosts that do not ship Tk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

PROTOCOL_SCHEME = "ggparrot"
PROTOCOL_CLAIM_PATH = "/api/runner/launch-tickets/claim"
_PROTOCOL_V1_URI_RE = re.compile(
    r"^ggparrot://launch/?\?v=1&ticket=([A-Za-z0-9_-]{43})$"
)
_PROTOCOL_V2_URI_RE = re.compile(
    # Windows CreateUri canonicalizes an authority-only custom URI by adding
    # exactly one root-path slash (``ggparrot://launch/?...``).  Accept that
    # browser/OS normalization, but no other path or query shape.
    r"^ggparrot://launch/?\?v=2&env=(local|production)&ticket=([A-Za-z0-9_-]{43})$"
)


@dataclass(frozen=True)
class ProtocolLaunch:
    """A validated browser launch request.

    Version 1 always targets production.  Version 2 may select only one of
    the two named environments; it deliberately carries no server address.
    """

    ticket: str
    environment: Literal["local", "production"]
    version: Literal[1, 2]


class ProtocolLaunchError(ValueError):
    """A browser launch argument did not match the supported URI shape."""


def parse_protocol_launch(args: list[str]) -> ProtocolLaunch | None:
    """Return a validated launch from a strict ``--protocol`` invocation.

    The URI may contain only a ticket plus the v2 named environment.  In
    particular, macro JSON, account keys, server addresses, ports, and
    start/live commands must never enter the runner through its command line.
    """

    if not args:
        return None
    if args[0] != "--protocol":
        if "--protocol" in args:
            raise ProtocolLaunchError("프로토콜 실행 인자 순서가 올바르지 않습니다.")
        return None
    if len(args) != 2:
        raise ProtocolLaunchError("프로토콜 실행 인자 개수가 올바르지 않습니다.")
    v1_match = _PROTOCOL_V1_URI_RE.fullmatch(args[1])
    if v1_match is not None:
        return ProtocolLaunch(
            ticket=v1_match.group(1),
            environment="production",
            version=1,
        )
    v2_match = _PROTOCOL_V2_URI_RE.fullmatch(args[1])
    if v2_match is not None:
        return ProtocolLaunch(
            ticket=v2_match.group(2),
            environment=v2_match.group(1),
            version=2,
        )
    raise ProtocolLaunchError("지원하지 않는 프로토콜 주소입니다.")


def parse_protocol_args(args: list[str]) -> str | None:
    """Return only the ticket for callers using the original v1 parser API."""

    launch = parse_protocol_launch(args)
    return launch.ticket if launch is not None else None
