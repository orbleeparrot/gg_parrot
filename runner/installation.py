"""Pure helpers for installing the Windows runner protocol handler."""
from __future__ import annotations

import hashlib
from pathlib import Path

RUNNER_VERSION = "5"
RUNNER_RELEASE = f"runner-v{RUNNER_VERSION}"

_VERSIONED_EXE_PARTS = ("GGParrot", RUNNER_RELEASE, "ggparrot-runner.exe")


def protocol_install_target(local_app_data: str) -> Path:
    """Return this release's immutable per-user protocol-handler path."""

    return Path(local_app_data).joinpath(*_VERSIONED_EXE_PARTS)


def files_identical(left: Path, right: Path) -> bool:
    """Compare executables before touching a possibly running target."""

    if left.stat().st_size != right.stat().st_size:
        return False

    def digest(path: Path) -> bytes:
        result = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                result.update(chunk)
        return result.digest()

    return digest(left) == digest(right)
