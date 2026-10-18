"""Download metadata must not regress when a Blueprint sync reapplies old env, and must point at the desktop app."""
from pathlib import Path

import pytest

from app.runner_release import (MIN_SUPPORTED_RUNNER_VERSION, OFFICIAL_APP_VERSION, OFFICIAL_RUNNER_MAC_URL,
                                OFFICIAL_RUNNER_URL, OFFICIAL_RUNNER_VERSION, desktop_assets, resolve_runner_release)

LEGACY = "https://github.com/orbleeparrot/gg_parrot/releases/download/runner-v{0}/ggparrot-runner.exe"


def test_default_is_the_published_desktop_app():
    assert OFFICIAL_RUNNER_VERSION == "20"
    assert resolve_runner_release({}) == {
        "url": OFFICIAL_RUNNER_URL, "filename": f"ggparrot-desktop_{OFFICIAL_APP_VERSION}_windows_x64-setup.exe",
        "mac_url": OFFICIAL_RUNNER_MAC_URL, "mac_filename": f"ggparrot-desktop_{OFFICIAL_APP_VERSION}_darwin_universal.dmg",
        "version": "20", "app_version": OFFICIAL_APP_VERSION, "supports_launch": True, "min_runner_version": "6",
    }
    assert OFFICIAL_RUNNER_URL == ("https://github.com/orbleeparrot/ggparrot-desktop/releases/download/"
                                   f"v{OFFICIAL_APP_VERSION}/ggparrot-desktop_{OFFICIAL_APP_VERSION}_windows_x64-setup.exe")


@pytest.mark.parametrize("version", [6, 19, 20, 21])
def test_old_exe_url_left_on_render_is_upgraded_to_the_desktop_app(version):
    # render.yaml 에서 지운 키도 Render 에는 남는다 — 옛 exe 주소는 공식 데스크톱 앱으로 바꾼다.
    result = resolve_runner_release({"RUNNER_DOWNLOAD_URL": LEGACY.format(version), "RUNNER_EXE_VERSION": str(version)})
    assert result["url"] == OFFICIAL_RUNNER_URL and result["mac_url"] == OFFICIAL_RUNNER_MAC_URL
    assert result["version"] == "20" and result["supports_launch"]


@pytest.mark.parametrize("stale", ["0.19.6", "0.20.0", "0.20.1", "0.20.2", "0.20.3", "0.20.4", "0.20.5", "0.20.6", "0.20.7", "0.20.8", "0.20.9", "0.20.10", "", "1.0", "v0.20.1", "0.20.x"])
def test_stale_or_bad_app_version_cannot_downgrade(stale):
    assert resolve_runner_release({"RUNNER_APP_VERSION": stale})["app_version"] == OFFICIAL_APP_VERSION


def test_newer_app_version_is_followed():
    result = resolve_runner_release({"RUNNER_APP_VERSION": "0.21.3", "RUNNER_MIN_VERSION": "8"})
    assert result == {**desktop_assets("0.21.3"), "version": "21", "app_version": "0.21.3",
                      "supports_launch": True, "min_runner_version": "8"}


def test_min_version_never_drops_below_the_supported_floor():
    assert resolve_runner_release({"RUNNER_MIN_VERSION": "2"})["min_runner_version"] == MIN_SUPPORTED_RUNNER_VERSION


def test_custom_download_is_preserved():
    url = "https://downloads.example.com/runner-v6/ggparrot-runner.exe"
    result = resolve_runner_release({"RUNNER_DOWNLOAD_URL": url, "RUNNER_EXE_VERSION": "custom", "RUNNER_SUPPORTS_LAUNCH": "false"})
    assert result == {"url": url, "filename": "ggparrot-runner.exe", "mac_url": "", "mac_filename": "",
                      "version": "custom", "app_version": "", "supports_launch": False, "min_runner_version": ""}


def test_all_release_surfaces_match():
    root = Path(__file__).resolve().parents[2]
    blueprint = (root / "render.yaml").read_text()
    assert f'- key: RUNNER_APP_VERSION\n        value: "{OFFICIAL_APP_VERSION}"' in blueprint
    assert f'- key: RUNNER_MIN_VERSION\n        value: "{MIN_SUPPORTED_RUNNER_VERSION}"' in blueprint
    assert "key: RUNNER_DOWNLOAD_URL" not in blueprint
    frontend = (root / "frontend/src/lib/runnerDownload.js").read_text()
    assert f'OFFICIAL_APP_VERSION = "{OFFICIAL_APP_VERSION}"' in frontend
