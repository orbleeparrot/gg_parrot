from __future__ import annotations

import unittest

from runner.protocol import ProtocolLaunchError, parse_protocol_args


class ProtocolArgumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ticket = "A" * 21 + "_" + "b" * 21
        self.uri = f"ggparrot://launch?v=1&ticket={self.ticket}"

    def test_no_protocol_invocation_returns_none(self) -> None:
        self.assertIsNone(parse_protocol_args([]))
        self.assertIsNone(parse_protocol_args(["--ordinary-option"]))

    def test_exact_protocol_invocation_returns_ticket(self) -> None:
        self.assertEqual(parse_protocol_args(["--protocol", self.uri]), self.ticket)

    def test_flag_position_and_argument_count_are_strict(self) -> None:
        invalid = [
            ["--protocol"],
            [self.uri, "--protocol"],
            ["--protocol", self.uri, "extra"],
            ["extra", "--protocol", self.uri],
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ProtocolLaunchError):
                parse_protocol_args(args)

    def test_uri_shape_is_strict(self) -> None:
        invalid_uris = [
            f"GGPARROT://launch?v=1&ticket={self.ticket}",
            f"ggparrot://LAUNCH?v=1&ticket={self.ticket}",
            f"ggparrot://launch/?v=1&ticket={self.ticket}",
            f"ggparrot://launch?ticket={self.ticket}&v=1",
            f"ggparrot://launch?v=2&ticket={self.ticket}",
            f"ggparrot://launch?v=1&ticket={self.ticket}&action=start",
            f"ggparrot://launch?v=1&ticket={self.ticket}#fragment",
            f"ggparrot://launch?v=1&ticket={'A' * 42}",
            f"ggparrot://launch?v=1&ticket={'A' * 44}",
            f"ggparrot://launch?v=1&ticket={'A' * 42}=",
            f"ggparrot://launch?v=1&ticket={'A' * 40}%2D",
            f"ggparrot://other?v=1&ticket={self.ticket}",
        ]
        for uri in invalid_uris:
            with self.subTest(uri=uri), self.assertRaises(ProtocolLaunchError):
                parse_protocol_args(["--protocol", uri])


if __name__ == "__main__":
    unittest.main()
