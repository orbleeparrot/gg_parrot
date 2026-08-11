from __future__ import annotations

import json
import queue
import secrets
import sys
import tempfile
import threading
import unittest
from multiprocessing import Pipe
from pathlib import Path

from runner.protocol import ProtocolLaunch
from runner.single_instance import (
    IPC_AUTHKEY_BYTES,
    MAX_IPC_PAYLOAD_BYTES,
    InstanceAck,
    InstanceCommand,
    InstanceDescriptor,
    InvalidIPCMessage,
    RunnerSingleInstance,
    SingleInstanceError,
    decode_ack,
    decode_command,
    encode_ack,
    encode_command,
    read_instance_descriptor,
    receive_command_over_connection,
    send_command_over_connection,
    write_instance_descriptor,
)


class InstanceCommandCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ticket = "A" * 21 + "_" + "b" * 21

    def test_activate_round_trip_has_exact_shape(self) -> None:
        payload = encode_command(InstanceCommand.activate())
        self.assertEqual(
            json.loads(payload),
            {"ipc_version": 1, "kind": "activate"},
        )
        self.assertEqual(decode_command(payload), InstanceCommand.activate())

    def test_protocol_launch_round_trip_for_both_contract_versions(self) -> None:
        launches = (
            ProtocolLaunch(ticket=self.ticket, environment="production", version=1),
            ProtocolLaunch(ticket=self.ticket, environment="local", version=2),
            ProtocolLaunch(ticket=self.ticket, environment="production", version=2),
        )
        for launch in launches:
            with self.subTest(launch=launch):
                command = InstanceCommand.launch_protocol(launch)
                self.assertEqual(decode_command(encode_command(command)), command)

    def test_extra_missing_duplicate_and_wrongly_typed_fields_are_rejected(self) -> None:
        invalid = [
            b'{"ipc_version":1,"kind":"activate","ticket":"secret"}',
            b'{"ipc_version":1}',
            b'{"ipc_version":true,"kind":"activate"}',
            b'{"ipc_version":1,"ipc_version":1,"kind":"activate"}',
            (
                b'{"ipc_version":1,"kind":"launch","launch":'
                + json.dumps(
                    {
                        "version": 2,
                        "environment": "local",
                        "ticket": self.ticket,
                        "live": True,
                    },
                    separators=(",", ":"),
                ).encode("ascii")
                + b"}"
            ),
            json.dumps(
                {
                    "ipc_version": 1,
                    "kind": "launch",
                    "launch": {
                        "version": True,
                        "environment": "local",
                        "ticket": self.ticket,
                    },
                },
                separators=(",", ":"),
            ).encode("ascii"),
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(InvalidIPCMessage):
                decode_command(payload)

    def test_invalid_ticket_environment_and_version_pair_are_rejected(self) -> None:
        invalid_launches = (
            ProtocolLaunch(ticket="short", environment="local", version=2),
            ProtocolLaunch(ticket=self.ticket, environment="staging", version=2),
            ProtocolLaunch(ticket=self.ticket, environment="local", version=1),
        )
        for launch in invalid_launches:
            with self.subTest(launch=launch), self.assertRaises(InvalidIPCMessage):
                encode_command(InstanceCommand.launch_protocol(launch))

    def test_oversized_non_utf8_and_non_object_payloads_are_rejected(self) -> None:
        invalid = (
            b"x" * (MAX_IPC_PAYLOAD_BYTES + 1),
            b"\xff",
            b"[]",
        )
        for payload in invalid:
            with self.subTest(payload=payload[:20]), self.assertRaises(InvalidIPCMessage):
                decode_command(payload)

    def test_acknowledgement_contract_is_exact(self) -> None:
        for ack in (
            InstanceAck(True),
            InstanceAck(False, "invalid_command"),
            InstanceAck(False, "queue_full"),
            InstanceAck(False, "shutting_down"),
        ):
            with self.subTest(ack=ack):
                self.assertEqual(decode_ack(encode_ack(ack)), ack)
        invalid = (
            b'{"ipc_version":1,"ok":true,"error":"invalid_command"}',
            b'{"ipc_version":1,"ok":false}',
            b'{"ipc_version":1,"ok":false,"error":"secret"}',
        )
        for payload in invalid:
            with self.assertRaises(InvalidIPCMessage):
                decode_ack(payload)


class InstanceDescriptorTests(unittest.TestCase):
    def descriptor(self) -> InstanceDescriptor:
        return InstanceDescriptor(
            address=r"\\.\pipe\GGParrot-Runner-" + "a" * 32,
            authkey=bytes(range(IPC_AUTHKEY_BYTES)),
            pid=4321,
            instance_id="b" * 32,
        )

    def test_descriptor_round_trip_is_atomic_and_contains_no_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "GGParrot" / "runner-ipc-session-1.json"
            descriptor = self.descriptor()
            write_instance_descriptor(path, descriptor)
            self.assertEqual(read_instance_descriptor(path), descriptor)
            raw = path.read_text(encoding="ascii")
            self.assertNotIn("ticket", raw.lower())
            self.assertEqual(
                set(json.loads(raw)),
                {
                    "descriptor_version",
                    "transport",
                    "address",
                    "authkey",
                    "pid",
                    "instance_id",
                },
            )
            leftovers = [entry for entry in path.parent.iterdir() if entry != path]
            self.assertEqual(leftovers, [])

    def test_descriptor_rejects_extra_fields_and_non_32_byte_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runner.json"
            write_instance_descriptor(path, self.descriptor())
            value = json.loads(path.read_bytes())
            value["ticket"] = "must-never-be-stored"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(SingleInstanceError):
                read_instance_descriptor(path)

            bad = InstanceDescriptor(
                address=self.descriptor().address,
                authkey=secrets.token_bytes(31),
                pid=1,
                instance_id="c" * 32,
            )
            with self.assertRaises(InvalidIPCMessage):
                write_instance_descriptor(path, bad)


class DuplexConnectionTests(unittest.TestCase):
    """Exercise the size-limited byte protocol on Linux without a socket."""

    def test_size_limited_command_round_trip(self) -> None:
        server_connection, client_connection = Pipe(duplex=True)
        received: queue.Queue[InstanceCommand | BaseException] = queue.Queue()

        def server() -> None:
            try:
                command = receive_command_over_connection(server_connection)
                received.put(command)
                server_connection.send_bytes(encode_ack(InstanceAck(True)))
            except BaseException as exc:  # surfaced in the test thread
                received.put(exc)
            finally:
                server_connection.close()

        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        command = InstanceCommand.activate()
        try:
            ack = send_command_over_connection(client_connection, command, timeout=2.0)
        finally:
            client_connection.close()
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        delivered = received.get_nowait()
        if isinstance(delivered, BaseException):
            raise delivered
        self.assertEqual(delivered, command)
        self.assertEqual(ack, InstanceAck(True))


@unittest.skipUnless(sys.platform == "win32", "Windows named-pipe integration")
class WindowsSingleInstanceIntegrationTests(unittest.TestCase):
    def test_secondary_handoff_reaches_one_primary_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mutex_name = rf"Local\GGParrot.Runner.Test.{secrets.token_hex(8)}"
            primary = RunnerSingleInstance.acquire(
                local_app_data=temporary,
                mutex_name=mutex_name,
            )
            secondary = RunnerSingleInstance.acquire(
                local_app_data=temporary,
                mutex_name=mutex_name,
            )
            try:
                self.assertTrue(primary.is_primary)
                self.assertFalse(secondary.is_primary)
                command = InstanceCommand.activate()
                self.assertEqual(secondary.handoff(command), InstanceAck(True))
                self.assertEqual(primary.get_command_nowait(), command)
            finally:
                secondary.close()
                primary.close()


if __name__ == "__main__":
    unittest.main()
