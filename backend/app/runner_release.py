"""Published runner download policy, independent of application startup.

Keep the release floor in sync with runner/installation.py and render.yaml.
Only this repository's official asset URLs are upgraded; custom and newer
release URLs must never be silently replaced.
"""
import re
from collections.abc import Mapping

OFFICIAL_RUNNER_VERSION = "10"
# Download version is not the compatibility floor. Existing rule-A/B runners
# remain supported; indicator macros separately require the v8 signal protocol.
MIN_SUPPORTED_RUNNER_VERSION = "6"
OFFICIAL_RUNNER_URL = "https://github.com/orbleeparrot/gg_parrot/releases/download/runner-v10/ggparrot-runner.exe"
_OFFICIAL_URL = re.compile(r"https://github\.com/orbleeparrot/gg_parrot/releases/download/runner-v([0-9]{1,6})/ggparrot-runner\.exe")


def resolve_runner_release(env: Mapping[str, str]) -> dict:
    url = str(env.get("RUNNER_DOWNLOAD_URL", "")).strip() or OFFICIAL_RUNNER_URL
    official = _OFFICIAL_URL.fullmatch(url)
    if official and int(official[1]) < int(OFFICIAL_RUNNER_VERSION):
        url = OFFICIAL_RUNNER_URL
        official = _OFFICIAL_URL.fullmatch(url)
    supports = str(env.get("RUNNER_SUPPORTS_LAUNCH", "true" if official else "false")).strip().lower() in {"1", "true", "yes"}
    version = str(int(official[1])) if official else str(env.get("RUNNER_EXE_VERSION", "")).strip()
    minimum = str(env.get("RUNNER_MIN_VERSION", "")).strip() or MIN_SUPPORTED_RUNNER_VERSION
    if official and (not minimum.isascii() or not minimum.isdigit() or len(minimum) > 6 or int(minimum) < int(MIN_SUPPORTED_RUNNER_VERSION)):
        minimum = MIN_SUPPORTED_RUNNER_VERSION
    return {"url": url, "version": version, "supports_launch": supports,
            "min_runner_version": minimum if supports else ""}
