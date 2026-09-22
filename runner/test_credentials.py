"""이 PC에 키 기억하기 — DPAPI 로 감싼 파일 하나. 깨진 파일·다른 PC 파일은 조용히 빈 값."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runner import credentials


def _xor(data: bytes) -> bytes:  # 테스트용 가짜 보호 — 되돌릴 수 있고 평문과 다르다
    return bytes(b ^ 0x5A for b in data)


class CredentialFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "GGParrot" / "credentials.dat"

    def test_default_path_lives_under_local_app_data(self) -> None:
        self.assertEqual(credentials.default_path("C:/Users/x/AppData/Local"),
                         Path("C:/Users/x/AppData/Local") / "GGParrot" / "credentials.dat")

    def test_save_then_load_round_trips_and_file_is_not_plaintext(self) -> None:
        credentials.save(self.path, {"api_key": "AK", "api_secret": "SECRET-1", "member_key": "MK"}, protect=_xor)
        raw = self.path.read_bytes()
        self.assertNotIn(b"SECRET-1", raw)
        self.assertEqual(credentials.load(self.path, unprotect=_xor),
                         {"api_key": "AK", "api_secret": "SECRET-1", "member_key": "MK"})

    def test_load_missing_or_corrupt_file_returns_none(self) -> None:
        self.assertIsNone(credentials.load(self.path, unprotect=_xor))
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"garbage")
        self.assertIsNone(credentials.load(self.path, unprotect=_xor))

    def test_load_returns_none_when_unprotect_fails(self) -> None:
        credentials.save(self.path, {"api_key": "AK", "api_secret": "S", "member_key": "M"}, protect=_xor)

        def boom(data: bytes) -> bytes:
            raise OSError("wrong user")

        self.assertIsNone(credentials.load(self.path, unprotect=boom))

    def test_clear_removes_file_and_is_idempotent(self) -> None:
        credentials.save(self.path, {"api_key": "AK", "api_secret": "S", "member_key": "M"}, protect=_xor)
        credentials.clear(self.path)
        self.assertFalse(self.path.exists())
        credentials.clear(self.path)  # 없어도 조용히

    def test_only_known_fields_are_kept(self) -> None:
        credentials.save(self.path, {"api_key": "AK", "api_secret": "S", "member_key": "M", "junk": "x"}, protect=_xor)
        self.assertEqual(credentials.load(self.path, unprotect=_xor), {"api_key": "AK", "api_secret": "S", "member_key": "M"})
        self.assertEqual(json.loads(_xor(self.path.read_bytes()).decode("utf-8")).get("junk"), None)


class RememberChoiceTests(unittest.TestCase):
    def test_remember_saves_and_unchecked_clears(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.dat"
            credentials.apply_choice(path, True, {"api_key": "A", "api_secret": "S", "member_key": "M"}, protect=_xor)
            self.assertEqual(credentials.load(path, unprotect=_xor)["api_key"], "A")
            credentials.apply_choice(path, False, {"api_key": "A", "api_secret": "S", "member_key": "M"}, protect=_xor)
            self.assertFalse(path.exists())
