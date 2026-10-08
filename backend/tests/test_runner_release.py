"""Download metadata must not regress when a Blueprint sync reapplies old env."""
from pathlib import Path
import importlib.util
import pytest

from app.runner_release import resolve_runner_release, OFFICIAL_RUNNER_VERSION, OFFICIAL_RUNNER_URL, MIN_SUPPORTED_RUNNER_VERSION


def test_default_is_published_v12():
    assert resolve_runner_release({}) == {
        'url': OFFICIAL_RUNNER_URL, 'version': '12',
        'supports_launch': True, 'min_runner_version': '6',
    }


@pytest.mark.parametrize('version', range(1, 12))
def test_stale_official_env_cannot_downgrade_download(version):
    result = resolve_runner_release({
        'RUNNER_DOWNLOAD_URL': OFFICIAL_RUNNER_URL.replace('runner-v12', f'runner-v{version}'),
        'RUNNER_EXE_VERSION': str(version), 'RUNNER_MIN_VERSION': str(version),
    })
    assert result['url'] == OFFICIAL_RUNNER_URL
    assert result['version'] == '12'
    assert result['min_runner_version'] == str(max(6, version))
    assert result['supports_launch']


def test_current_url_overrides_stale_display_metadata():
    result = resolve_runner_release({'RUNNER_DOWNLOAD_URL': OFFICIAL_RUNNER_URL,
        'RUNNER_EXE_VERSION': '6', 'RUNNER_MIN_VERSION': '6'})
    assert result['version'] == '12'
    assert result['min_runner_version'] == '6'


def test_future_release_is_not_downgraded():
    url = OFFICIAL_RUNNER_URL.replace('runner-v12', 'runner-v13')
    result = resolve_runner_release({'RUNNER_DOWNLOAD_URL': url, 'RUNNER_EXE_VERSION': '6', 'RUNNER_MIN_VERSION': '13'})
    assert result['url'] == url
    assert result['version'] == result['min_runner_version'] == '13'


def test_custom_download_is_preserved():
    url = 'https://downloads.example.com/runner-v6/ggparrot-runner.exe'
    result = resolve_runner_release({'RUNNER_DOWNLOAD_URL': url, 'RUNNER_EXE_VERSION': 'custom', 'RUNNER_SUPPORTS_LAUNCH': 'false'})
    assert result == {'url': url, 'version': 'custom', 'supports_launch': False, 'min_runner_version': ''}


def test_all_release_surfaces_match_runner_source():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('runner_installation_contract', root / 'runner/installation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert OFFICIAL_RUNNER_VERSION == module.RUNNER_VERSION
    blueprint = (root / 'render.yaml').read_text()
    assert f'- key: RUNNER_DOWNLOAD_URL\n        value: "{OFFICIAL_RUNNER_URL}"' in blueprint
    assert f'- key: RUNNER_EXE_VERSION\n        value: "{module.RUNNER_VERSION}"' in blueprint
    assert f'- key: RUNNER_MIN_VERSION\n        value: "{MIN_SUPPORTED_RUNNER_VERSION}"' in blueprint
    frontend = (root / 'frontend/src/lib/runnerDownload.js').read_text()
    assert f'OFFICIAL_RUNNER_VERSION = "{module.RUNNER_VERSION}"' in frontend
    assert OFFICIAL_RUNNER_URL in frontend
    assert 'runner-v6' not in (root / 'frontend/src/pages/RunnerDownload.jsx').read_text()
    workflow = (root / '.github/workflows/runner-release.yml').read_text()
    assert f'default: "{module.RUNNER_RELEASE}"' in workflow
