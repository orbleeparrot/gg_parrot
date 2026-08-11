from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runner.installation import (
    RUNNER_RELEASE,
    RUNNER_VERSION,
    files_identical,
    protocol_install_target,
)


class ProtocolInstallPathTests(unittest.TestCase):
    def test_v5_uses_release_specific_path_instead_of_locked_legacy_path(self) -> None:
        local_app_data = Path("C:/Users/test/AppData/Local")
        target = protocol_install_target(str(local_app_data))

        self.assertEqual(RUNNER_VERSION, "5")
        self.assertEqual(RUNNER_RELEASE, "runner-v5")
        self.assertEqual(
            target,
            local_app_data / "GGParrot" / "runner-v5" / "ggparrot-runner.exe",
        )
        self.assertNotEqual(
            target,
            local_app_data / "GGParrot" / "ggparrot-runner.exe",
        )

    def test_existing_release_binary_is_reused_only_when_content_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "downloaded.exe"
            installed = Path(tmp) / "installed.exe"
            source.write_bytes(b"official runner v5")
            installed.write_bytes(b"official runner v5")
            self.assertTrue(files_identical(source, installed))

            installed.write_bytes(b"different runner")
            self.assertFalse(files_identical(source, installed))


if __name__ == "__main__":
    unittest.main()
