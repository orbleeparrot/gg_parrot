"""이 PC에 키 기억하기 — DPAPI 로 감싼 파일 하나. 깨진 파일·다른 PC 파일은 조용히 빈 값."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


class CredentialsLoadResultTests(unittest.TestCase):
    def test_missing_and_unreadable_files_have_distinct_safe_statuses(self):
        for error, status in ((FileNotFoundError("fixture"), "missing"),
                              (PermissionError("fixture-secret"), "read_error")):
            with self.subTest(status=status):
                path = Mock()
                path.read_bytes.side_effect = error
                result = credentials.load_result(path)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.values)
                self.assertNotIn("fixture-secret", repr(result))

    def test_crypto_failure_is_distinct_from_invalid_decrypted_data(self):
        path = Mock()
        path.read_bytes.return_value = b"fixture-ciphertext"
        result = credentials.load_result(path, unprotect=Mock(side_effect=OSError("fixture-secret")))
        self.assertEqual(result.status, "decrypt_error")
        for raw in (b"invalid", b"[]", b"\xff", None):
            with self.subTest(raw=raw):
                result = credentials.load_result(path, unprotect=lambda _raw: raw)
                self.assertEqual(result.status, "invalid_data")
                self.assertIsNone(result.values)

    def test_loaded_result_preserves_legacy_shape_and_hides_keys_in_repr(self):
        path = Mock()
        path.read_bytes.return_value = b"fixture-ciphertext"
        payload = {"api_key": "fixture-key", "api_secret": "fixture-secret", "member_key": "fixture-member"}
        result = credentials.load_result(path, unprotect=lambda _raw: json.dumps(payload).encode())
        self.assertEqual(result.status, "loaded")
        self.assertEqual(result.values["exchanges"]["binance"]["api_secret"], "fixture-secret")
        self.assertNotIn("fixture-secret", repr(result))
        self.assertNotIn("fixture-member", repr(result))
        self.assertEqual(credentials.load(path, unprotect=lambda _raw: json.dumps(payload).encode()), result.values)


class ExchangeRememberChoiceTests(unittest.TestCase):
    def setUp(self):
        self.path = Mock()
        self.previous = {
            "version": 2, "member_key": "saved-member",
            "exchanges": {"binance": {"api_key": "bk", "api_secret": "bs"},
                          "upbit": {"api_key": "uk", "api_secret": "us"}},
            "connection_history": {"binance": {"public_ipv4": "8.8.8.8", "observed_at": 100}},
        }
        loaded = self.previous
        def disk_result(_path):
            return credentials.CredentialsLoadResult(loaded, "loaded")
        self.load_patch = patch.object(credentials, "load_result", side_effect=disk_result)
        self.load_patch.start()
        self.addCleanup(self.load_patch.stop)

    def apply_with_disk(self, remember, values, previous):
        with patch.object(credentials, "load_result", return_value=credentials.CredentialsLoadResult(previous, "loaded")):
            return credentials.apply_exchange_choice(self.path, "upbit", remember, values, previous=previous)

    def test_selected_save_keeps_other_pairs_and_accepts_only_the_selected_input(self):
        original = json.loads(json.dumps(self.previous))
        values = {"member_key": "current-member", "exchanges": {
            "binance": {"api_key": "must-not-overwrite", "api_secret": "must-not-overwrite"},
            "upbit": {"api_key": "new-upbit", "api_secret": "new-secret"}},
            "connection_history": {"upbit": {"public_ipv4": "1.1.1.1", "observed_at": 101},
                                   "binance": {"public_ipv4": "9.9.9.9", "observed_at": 999}}}
        with patch.object(credentials, "save") as save, patch.object(credentials, "clear") as clear:
            result = credentials.apply_exchange_choice(self.path, "upbit", True, values, previous=self.previous)
        self.assertEqual(result["exchanges"]["upbit"]["api_key"], "new-upbit")
        self.assertEqual(result["exchanges"]["binance"], original["exchanges"]["binance"])
        self.assertEqual(result["connection_history"]["binance"], original["connection_history"]["binance"])
        self.assertEqual(result["connection_history"]["upbit"]["public_ipv4"], "1.1.1.1")
        self.assertEqual(result["member_key"], "current-member")
        self.assertEqual(self.previous, original)
        save.assert_called_once()
        clear.assert_not_called()

    def test_selected_delete_keeps_other_pairs_and_the_saved_member_key(self):
        values = {"member_key": "unrelated-current-member", "exchanges": {}}
        with patch.object(credentials, "save") as save, patch.object(credentials, "clear") as clear:
            result = credentials.apply_exchange_choice(self.path, "upbit", False, values, previous=self.previous)
        self.assertEqual(list(result["exchanges"]), ["binance"])
        self.assertEqual(result["member_key"], "saved-member")
        save.assert_called_once()
        clear.assert_not_called()

    def test_deleting_the_last_exchange_preserves_a_member_only_file(self):
        previous = {"version": 2, "member_key": "saved-member", "exchanges": {"upbit": {"api_key": "uk", "api_secret": "us"}},
                    "connection_history": {"upbit": {"public_ipv4": "8.8.8.8", "observed_at": 100}}}
        with patch.object(credentials, "save") as save, patch.object(credentials, "clear") as clear:
            result = self.apply_with_disk(False, {}, previous)
        self.assertEqual(result, {"version": 2, "member_key": "saved-member", "exchanges": {}})
        save.assert_called_once()
        clear.assert_not_called()

    def test_deleting_the_last_exchange_with_no_member_removes_the_file(self):
        previous = {"version": 2, "member_key": "", "exchanges": {"upbit": {"api_key": "uk", "api_secret": "us"}}}
        with patch.object(credentials, "save") as save, patch.object(credentials, "clear") as clear:
            result = self.apply_with_disk(False, {}, previous)
        self.assertIsNone(result)
        clear.assert_called_once_with(self.path)
        save.assert_not_called()

    def test_member_only_file_is_preserved_when_selected_key_does_not_exist(self):
        previous = {"version": 2, "member_key": "saved-member", "exchanges": {}}
        with patch.object(credentials, "save") as save, patch.object(credentials, "clear") as clear:
            result = self.apply_with_disk(False, {}, previous)
        self.assertEqual(result, previous)
        save.assert_called_once()
        clear.assert_not_called()

    def test_disk_is_authoritative_when_another_runner_changed_saved_keys(self):
        disk = {"version": 2, "member_key": "disk-member", "exchanges": {
            "binance": {"api_key": "latest-binance", "api_secret": "latest-secret"},
            "bithumb": {"api_key": "new-bithumb", "api_secret": "new-secret"}},
            "connection_history": {"bithumb": {"public_ipv4": "1.1.1.1", "observed_at": 100}}}
        original = json.loads(json.dumps(disk))
        with patch.object(credentials, "load_result", return_value=credentials.CredentialsLoadResult(disk, "loaded")), patch.object(credentials, "save") as save:
            result = credentials.apply_exchange_choice(self.path, "upbit", True,
                {"exchanges": {"upbit": {"api_key": "chosen", "api_secret": "chosen-secret"}}}, previous=self.previous)
        self.assertEqual(result["exchanges"]["binance"], disk["exchanges"]["binance"])
        self.assertEqual(result["exchanges"]["bithumb"], disk["exchanges"]["bithumb"])
        self.assertEqual(result["member_key"], "disk-member")
        self.assertEqual(result["connection_history"], disk["connection_history"])
        self.assertEqual(disk, original)
        save.assert_called_once()

    def test_same_values_still_must_finish_storage_before_returning_success(self):
        with patch.object(credentials, "save", side_effect=PermissionError("fixture-only")):
            with self.assertRaises(PermissionError):
                credentials.apply_exchange_choice(self.path, "upbit", True, self.previous, previous=self.previous)

    def test_missing_disk_does_not_resurrect_an_externally_deleted_cache(self):
        with patch.object(credentials, "load_result", return_value=credentials.CredentialsLoadResult(None, "missing")), patch.object(credentials, "save") as save:
            result = credentials.apply_exchange_choice(self.path, "upbit", True,
                {"exchanges": {"upbit": {"api_key": "chosen", "api_secret": "chosen-secret"}}}, previous=self.previous)
        self.assertEqual(result, {"version": 2, "member_key": "", "exchanges": {
            "upbit": {"api_key": "chosen", "api_secret": "chosen-secret"}}})
        save.assert_called_once()

    def test_incomplete_selected_keys_and_unknown_exchange_cannot_write(self):
        for exchange, values in (("upbit", {"exchanges": {"upbit": {"api_key": "uk", "api_secret": ""}}}),
                                 ("https://evil.test", {})):
            with self.subTest(exchange=exchange), patch.object(credentials, "save") as save, patch.object(credentials, "clear") as clear:
                with self.assertRaises(ValueError):
                    credentials.apply_exchange_choice(self.path, exchange, True, values, previous=self.previous)
                self.assertFalse(save.called or clear.called)

    def test_unknown_existing_file_is_never_overwritten_on_load_failure(self):
        for status in ("read_error", "decrypt_error", "invalid_data"):
            for previous in (None, self.previous):
                with self.subTest(status=status, previous=bool(previous)), patch.object(credentials, "load_result", return_value=credentials.CredentialsLoadResult(None, status)), patch.object(credentials, "save") as save, patch.object(credentials, "clear") as clear:
                    with self.assertRaises(ValueError):
                        credentials.apply_exchange_choice(self.path, "upbit", True, {"exchanges": {"upbit": {"api_key": "uk", "api_secret": "us"}}}, previous=previous)
                self.assertFalse(save.called or clear.called)

    def test_save_and_delete_failures_raise_without_mutating_previous_cache(self):
        for remember, previous, function in ((True, self.previous, "save"),
                                            (False, {"version": 2, "member_key": "", "exchanges": {"upbit": {"api_key": "uk", "api_secret": "us"}}}, "clear")):
            original = json.loads(json.dumps(previous))
            with self.subTest(function=function), patch.object(credentials, function, side_effect=PermissionError("fixture-only")):
                with self.assertRaises(PermissionError):
                    self.apply_with_disk(remember,
                        {"exchanges": {"upbit": {"api_key": "new", "api_secret": "new-secret"}}}, previous)
            self.assertEqual(previous, original)


class ConnectionHistoryStorageTests(unittest.TestCase):
    def test_only_public_ipv4_and_valid_observation_time_are_roundtripped(self):
        values = _values("fixture-key", "fixture-secret", "fixture-member")
        values["connection_history"] = {
            "upbit": {"public_ipv4": "8.8.8.8", "observed_at": 100, "registered": True, "api_secret": "drop-me"},
            "unknown": {"public_ipv4": "1.1.1.1", "observed_at": 100},
            "bithumb": {"public_ipv4": "192.168.0.1", "observed_at": 100},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.dat"
            credentials.save(path, values, protect=_xor)
            loaded = credentials.load(path, unprotect=_xor)
        self.assertEqual(loaded["connection_history"], {"upbit": {"public_ipv4": "8.8.8.8", "observed_at": 100}})

    def test_bad_address_time_and_future_observations_are_dropped(self):
        for address, observed_at in (("192.168.0.1", 100), ("224.0.0.1", 100), ("::1", 100),
                                     ("8.8.8.8", 0), ("8.8.8.8", True), ("8.8.8.8", "100"),
                                     ("8.8.8.8", float("nan")), ("8.8.8.8", float("inf")),
                                     ("8.8.8.8", time.time() + 600), ("8.8.8.8", 10**1000)):
            values = _values("fixture-key", "fixture-secret", "fixture-member")
            values["connection_history"] = {"upbit": {"public_ipv4": address, "observed_at": observed_at}}
            with self.subTest(address=address, observed_at=observed_at), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "fixture.dat"
                credentials.save(path, values, protect=_xor)
                self.assertNotIn("connection_history", credentials.load(path, unprotect=_xor))

    def test_failed_encryption_never_replaces_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.dat"
            credentials.save(path, _values("old", "old-secret", "old-member"), protect=_xor)
            original = path.read_bytes()
            with self.assertRaises(OSError):
                credentials.save(path, _values("new", "new-secret", "new-member"), protect=Mock(side_effect=OSError("fixture-only")))
            self.assertEqual(path.read_bytes(), original)

    def test_failed_replace_preserves_old_file_and_removes_only_the_owned_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.dat"
            credentials.save(path, _values("old", "old-secret", "old-member"), protect=_xor)
            unrelated = path.with_suffix(".tmp")
            unrelated.write_bytes(b"other-runner-owned-fixture")
            original = path.read_bytes()
            with patch.object(credentials.os, "replace", side_effect=PermissionError("fixture-only")):
                with self.assertRaises(PermissionError):
                    credentials.save(path, _values("new", "new-secret", "new-member"), protect=_xor)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(unrelated.read_bytes(), b"other-runner-owned-fixture")
            self.assertEqual(set(path.parent.iterdir()), {path, unrelated})


class UnreadableBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "credentials.dat"
        self.path.write_bytes(b"fixture-ciphertext")
        self.load_patch = patch.object(credentials, "load_result", return_value=credentials.CredentialsLoadResult(None, "decrypt_error"))
        self.load_patch.start()
        self.addCleanup(self.load_patch.stop)

    def test_unreadable_backup_stays_beside_original_and_keeps_exact_ciphertext(self):
        backup = credentials.backup_unreadable(self.path)
        self.assertEqual(backup.parent, self.path.parent)
        self.assertTrue(backup.name.startswith("credentials.dat.backup-"))
        self.assertEqual(backup.read_bytes(), b"fixture-ciphertext")
        self.assertFalse(self.path.exists())

    def test_symlink_or_directory_is_refused_without_changing_its_target(self):
        link = self.path.parent / "link.dat"
        link.symlink_to(self.path)
        for target in (link, self.path.parent):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    credentials.backup_unreadable(target)
                self.assertEqual(self.path.read_bytes(), b"fixture-ciphertext")
        parent_link = self.path.parent / "linked-directory"
        parent_link.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            credentials.backup_unreadable(parent_link / "credentials.dat")

    def test_healthy_or_missing_file_is_not_a_recovery_backup_target(self):
        for status in ("loaded", "missing"):
            with self.subTest(status=status), patch.object(credentials, "load_result", return_value=credentials.CredentialsLoadResult(None, status)), patch.object(credentials.os, "replace") as replace:
                with self.assertRaises(ValueError):
                    credentials.backup_unreadable(self.path)
                replace.assert_not_called()
                self.assertEqual(self.path.read_bytes(), b"fixture-ciphertext")

    def test_failed_backup_move_preserves_original_and_cleans_reserved_filename(self):
        with patch.object(credentials.os, "replace", side_effect=PermissionError("fixture-only")):
            with self.assertRaises(PermissionError):
                credentials.backup_unreadable(self.path)
        self.assertEqual(self.path.read_bytes(), b"fixture-ciphertext")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_source_changed_during_reservation_is_not_moved(self):
        original_mkstemp = tempfile.mkstemp
        def changed_source(*args, **kwargs):
            result = original_mkstemp(*args, **kwargs)
            self.path.write_bytes(b"newer-fixture-ciphertext")
            return result
        with patch.object(credentials.tempfile, "mkstemp", side_effect=changed_source), patch.object(credentials.os, "replace") as replace:
            with self.assertRaises(ValueError):
                credentials.backup_unreadable(self.path)
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"newer-fixture-ciphertext")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_collision_never_overwrites_existing_backup_or_original(self):
        existing = self.path.parent / "existing-backup.dat"
        existing.write_bytes(b"existing")
        with patch.object(credentials.tempfile, "mkstemp", side_effect=FileExistsError("fixture-only")), patch.object(credentials.os, "replace") as replace:
            with self.assertRaises(FileExistsError):
                credentials.backup_unreadable(self.path)
        replace.assert_not_called()
        self.assertEqual(existing.read_bytes(), b"existing")
        self.assertEqual(self.path.read_bytes(), b"fixture-ciphertext")
