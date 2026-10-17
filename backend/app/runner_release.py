"""Published runner download policy, independent of application startup.

The runner is the desktop app (orbleeparrot/ggparrot-desktop): a Windows NSIS
installer and a macOS dmg per release. Its version is ``0.<level>.<patch>``;
``level`` is the server link level it sends as ``runner_version`` (the old
Python exe's ``runner-vN`` numbering continues there), so every minimum-version
check keeps comparing plain integers.

Keep OFFICIAL_APP_VERSION in sync with render.yaml (RUNNER_APP_VERSION) and
frontend/src/lib/runnerDownload.js. A stale Blueprint value never downgrades the
download, and the old exe URL (still set on Render as RUNNER_DOWNLOAD_URL) is
upgraded to the desktop app. A custom, non-official URL is kept as is.
"""
import re
from collections.abc import Mapping

OFFICIAL_APP_VERSION = "0.20.7"
# Download version is not the compatibility floor. Existing rule-A/B runners
# remain supported; indicator macros separately require the v8 signal protocol.
MIN_SUPPORTED_RUNNER_VERSION = "6"
DESKTOP_RELEASE_BASE = "https://github.com/orbleeparrot/ggparrot-desktop/releases/download"
_APP_VERSION = re.compile(r"0\.([0-9]{1,4})\.([0-9]{1,4})")
_LEGACY_EXE_URL = re.compile(r"https://github\.com/orbleeparrot/gg_parrot/releases/download/runner-v([0-9]{1,6})/ggparrot-runner\.exe")


def _parts(app_version: str) -> tuple[int, int] | None:
    match = _APP_VERSION.fullmatch(str(app_version or "").strip())
    return (int(match[1]), int(match[2])) if match else None


OFFICIAL_RUNNER_VERSION = str(_parts(OFFICIAL_APP_VERSION)[0])


def desktop_assets(app_version: str) -> dict:
    """그 버전 릴리스의 Windows 설치 파일 · macOS dmg 주소와 파일 이름."""
    win = f"ggparrot-desktop_{app_version}_windows_x64-setup.exe"
    mac = f"ggparrot-desktop_{app_version}_darwin_universal.dmg"
    base = f"{DESKTOP_RELEASE_BASE}/v{app_version}"
    return {"url": f"{base}/{win}", "filename": win, "mac_url": f"{base}/{mac}", "mac_filename": mac}


OFFICIAL_RUNNER_URL = desktop_assets(OFFICIAL_APP_VERSION)["url"]
OFFICIAL_RUNNER_MAC_URL = desktop_assets(OFFICIAL_APP_VERSION)["mac_url"]


def _minimum(env: Mapping[str, str]) -> str:
    minimum = str(env.get("RUNNER_MIN_VERSION", "")).strip() or MIN_SUPPORTED_RUNNER_VERSION
    if not minimum.isascii() or not minimum.isdigit() or len(minimum) > 6 or int(minimum) < int(MIN_SUPPORTED_RUNNER_VERSION):
        return MIN_SUPPORTED_RUNNER_VERSION
    return minimum


def resolve_runner_release(env: Mapping[str, str]) -> dict:
    custom = str(env.get("RUNNER_DOWNLOAD_URL", "")).strip()
    if custom and not _LEGACY_EXE_URL.fullmatch(custom):
        supports = str(env.get("RUNNER_SUPPORTS_LAUNCH", "false")).strip().lower() in {"1", "true", "yes"}
        return {"url": custom, "filename": custom.rsplit("/", 1)[-1], "mac_url": "", "mac_filename": "",
                "version": str(env.get("RUNNER_EXE_VERSION", "")).strip(), "app_version": "",
                "supports_launch": supports, "min_runner_version": _minimum(env) if supports else ""}
    app_version = str(env.get("RUNNER_APP_VERSION", "")).strip()
    if _parts(app_version) is None or _parts(app_version) < _parts(OFFICIAL_APP_VERSION):
        app_version = OFFICIAL_APP_VERSION
    supports = str(env.get("RUNNER_SUPPORTS_LAUNCH", "true")).strip().lower() in {"1", "true", "yes"}
    return {**desktop_assets(app_version), "version": str(_parts(app_version)[0]), "app_version": app_version,
            "supports_launch": supports, "min_runner_version": _minimum(env) if supports else ""}
