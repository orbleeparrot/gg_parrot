"""이 PC에 키 기억하기 — DPAPI 로 감싼 파일 하나. 깨진 파일·다른 PC 파일은 조용히 빈 값."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runner import credentials


def _xor(data: bytes) -> bytes:  # 테스트용 가짜 보호 — 되돌릴 수 있고 평문과 다르다
    return bytes(b ^ 0x5A for b in data)


def _values(api_key: str, api_secret: str, member_key: str) -> dict:  # v2 모양, 바이낸스 한 쌍
    return {"member_key": member_key, "exchanges": {"binance": {"api_key": api_key, "api_secret": api_secret}}}


class CredentialFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "GGParrot" / "credentials.dat"

    def test_default_path_lives_under_local_app_data(self) -> None:
        self.assertEqual(credentials.default_path("C:/Users/x/AppData/Local"),
                         Path("C:/Users/x/AppData/Local") / "GGParrot" / "credentials.dat")

    def test_save_then_load_round_trips_and_file_is_not_plaintext(self) -> None:
        credentials.save(self.path, _values("AK", "SECRET-1", "MK"), protect=_xor)
        raw = self.path.read_bytes()
        self.assertNotIn(b"SECRET-1", raw)
        self.assertEqual(credentials.load(self.path, unprotect=_xor),
                         {"version": 2, "member_key": "MK",
                          "exchanges": {"binance": {"api_key": "AK", "api_secret": "SECRET-1"}}})

    def test_load_missing_or_corrupt_file_returns_none(self) -> None:
        self.assertIsNone(credentials.load(self.path, unprotect=_xor))
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"garbage")
        self.assertIsNone(credentials.load(self.path, unprotect=_xor))

    def test_load_returns_none_when_unprotect_fails(self) -> None:
        credentials.save(self.path, _values("AK", "S", "M"), protect=_xor)

        def boom(data: bytes) -> bytes:
            raise OSError("wrong user")

        self.assertIsNone(credentials.load(self.path, unprotect=boom))

    def test_clear_removes_file_and_is_idempotent(self) -> None:
        credentials.save(self.path, _values("AK", "S", "M"), protect=_xor)
        credentials.clear(self.path)
        self.assertFalse(self.path.exists())
        credentials.clear(self.path)  # 없어도 조용히

    def test_only_known_fields_are_kept(self) -> None:
        values = _values("AK", "S", "M")
        values["junk"] = "x"
        values["exchanges"]["binance"]["junk"] = "y"
        credentials.save(self.path, values, protect=_xor)
        self.assertEqual(credentials.load(self.path, unprotect=_xor),
                         {"version": 2, "member_key": "M",
                          "exchanges": {"binance": {"api_key": "AK", "api_secret": "S"}}})
        stored = json.loads(_xor(self.path.read_bytes()).decode("utf-8"))
        self.assertNotIn("junk", stored)
        self.assertNotIn("junk", stored["exchanges"]["binance"])


class RememberChoiceTests(unittest.TestCase):
    def test_remember_saves_and_unchecked_clears(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.dat"
            credentials.apply_choice(path, True, _values("A", "S", "M"), protect=_xor)
            self.assertEqual(credentials.load(path, unprotect=_xor)["exchanges"]["binance"]["api_key"], "A")
            credentials.apply_choice(path, False, _values("A", "S", "M"), protect=_xor)
            self.assertFalse(path.exists())


class CredentialsV2Tests(unittest.TestCase):
    def roundtrip(self, values):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.dat"
            credentials.save(path, values, protect=lambda b: b)
            return credentials.load(path, unprotect=lambda b: b)

    def load_raw(self, payload):
        """파일 내용을 보호 없이 그대로 두고 읽는다 — 옛 파일·손으로 깨진 파일 흉내."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.dat"
            path.write_bytes(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
            return credentials.load(path, unprotect=lambda b: b)

    def test_each_exchange_keeps_its_own_pair(self):
        loaded = self.roundtrip({
            "member_key": "mk",
            "exchanges": {"binance": {"api_key": "bk", "api_secret": "bs"},
                          "upbit": {"api_key": "uk", "api_secret": "us"}},
        })
        self.assertEqual(loaded["exchanges"]["binance"]["api_key"], "bk")
        self.assertEqual(loaded["exchanges"]["upbit"]["api_secret"], "us")
        self.assertEqual(loaded["member_key"], "mk")

    def test_v1_file_moves_into_binance_and_keeps_member_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.dat"
            path.write_bytes(json.dumps(
                {"api_key": "old", "api_secret": "olds", "member_key": "mk"}).encode())
            loaded = credentials.load(path, unprotect=lambda b: b)
        self.assertEqual(loaded["exchanges"]["binance"]["api_key"], "old")
        self.assertEqual(loaded["exchanges"]["binance"]["api_secret"], "olds")
        self.assertEqual(loaded["member_key"], "mk")
        self.assertEqual(loaded["version"], 2)

    def test_unknown_exchange_names_are_dropped(self):
        loaded = self.roundtrip({
            "member_key": "mk",
            "exchanges": {"binance": {"api_key": "bk", "api_secret": "bs"},
                          "kraken": {"api_key": "x", "api_secret": "y"}},
        })
        self.assertNotIn("kraken", loaded["exchanges"])

    def test_entropy_is_unchanged_so_old_files_still_open(self):
        self.assertEqual(credentials._ENTROPY, b"ggparrot-runner-credentials-v1")

    def test_exchange_names_are_the_three_supported(self):
        self.assertEqual(credentials.EXCHANGES, ("binance", "upbit", "bithumb"))

    def test_saved_file_is_written_as_version_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.dat"
            credentials.save(path, _values("AK", "S", "M"), protect=_xor)
            stored = json.loads(_xor(path.read_bytes()).decode("utf-8"))
        self.assertEqual(stored, {"version": 2, "member_key": "M",
                                  "exchanges": {"binance": {"api_key": "AK", "api_secret": "S"}}})

    def test_v1_file_without_exchange_keys_keeps_member_key_and_adds_no_binance_entry(self):
        for payload in ({"api_key": "", "api_secret": "", "member_key": "mk"}, {"member_key": "mk"}):
            with self.subTest(payload=payload):
                loaded = self.load_raw(payload)
                self.assertEqual(loaded, {"version": 2, "member_key": "mk", "exchanges": {}})

    def test_v1_half_pair_is_migrated_as_typed_not_dropped(self):
        loaded = self.load_raw({"api_key": "old", "api_secret": "", "member_key": "mk"})
        self.assertEqual(loaded["exchanges"], {"binance": {"api_key": "old", "api_secret": ""}})

    def test_v1_non_string_values_become_empty(self):
        loaded = self.load_raw({"api_key": 12345, "api_secret": ["x"], "member_key": {"a": 1}})
        self.assertEqual(loaded, {"version": 2, "member_key": "", "exchanges": {}})

    def test_migrated_v1_file_is_rewritten_as_v2_on_next_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.dat"
            path.write_bytes(json.dumps({"api_key": "old", "api_secret": "olds", "member_key": "mk"}).encode())
            credentials.save(path, credentials.load(path, unprotect=lambda b: b), protect=lambda b: b)
            stored = json.loads(path.read_bytes().decode("utf-8"))
        self.assertEqual(stored["version"], 2)
        self.assertNotIn("api_key", stored)
        self.assertEqual(stored["exchanges"]["binance"]["api_secret"], "olds")

    def test_not_a_json_object_returns_none(self):
        for raw in (b"", b"not json", b"[1, 2]", b"null", b'"text"', b"\xff\xfe\x00", b'{"version": 2, "exch'):
            with self.subTest(raw=raw):
                self.assertIsNone(self.load_raw(raw))

    def test_malformed_exchanges_never_crash_and_become_empty(self):
        for bad in (None, "binance", ["binance"], 7, [], True):
            with self.subTest(exchanges=bad):
                loaded = self.load_raw({"version": 2, "member_key": "mk", "exchanges": bad})
                self.assertEqual(loaded, {"version": 2, "member_key": "mk", "exchanges": {}})

    def test_missing_exchanges_in_v2_file_is_empty(self):
        loaded = self.load_raw({"version": 2, "member_key": "mk"})
        self.assertEqual(loaded["exchanges"], {})

    def test_bad_pair_is_skipped_but_good_ones_survive(self):
        loaded = self.load_raw({"version": 2, "member_key": "mk", "exchanges": {
            "binance": "oops", "upbit": ["k", "s"], "bithumb": {"api_key": "bk", "api_secret": "bs"}}})
        self.assertEqual(loaded["exchanges"], {"bithumb": {"api_key": "bk", "api_secret": "bs"}})

    def test_non_string_values_in_a_pair_become_empty_strings(self):
        loaded = self.load_raw({"version": 2, "member_key": 5, "exchanges": {
            "upbit": {"api_key": "uk", "api_secret": 99}, "binance": {"api_key": None, "api_secret": {"x": 1}}}})
        self.assertEqual(loaded["exchanges"], {"upbit": {"api_key": "uk", "api_secret": ""}})
        self.assertEqual(loaded["member_key"], "")

    def test_save_tolerates_malformed_input_and_stays_loadable(self):
        loaded = self.roundtrip({"member_key": None, "exchanges": "nope"})
        self.assertEqual(loaded, {"version": 2, "member_key": "", "exchanges": {}})
        loaded = self.roundtrip({"member_key": "mk"})
        self.assertEqual(loaded["exchanges"], {})

    def test_empty_pairs_are_not_stored(self):
        loaded = self.roundtrip({"member_key": "mk", "exchanges": {
            "binance": {"api_key": "", "api_secret": ""}, "upbit": {"api_key": "uk", "api_secret": "us"}}})
        self.assertEqual(list(loaded["exchanges"]), ["upbit"])

    def test_future_version_is_read_as_v2_shape_not_migrated(self):
        # 다음 형식(version 3)을 만난 옛 실행기: 바깥에 남은 api_key 를 바이낸스로 오해해 옮기지 않는다.
        loaded = self.load_raw({"version": 3, "member_key": "mk", "api_key": "stray",
                                "exchanges": {"upbit": {"api_key": "uk", "api_secret": "us"}}})
        self.assertEqual(list(loaded["exchanges"]), ["upbit"])
