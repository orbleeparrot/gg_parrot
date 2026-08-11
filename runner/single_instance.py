"""Single-instance coordination for the Windows GGParrot runner.

The Windows shell starts a new process for every ``ggparrot://`` URL.  This
module lets that short-lived process hand a strictly validated command to the
already-running process and then exit, without putting a launch ticket in a
file or opening a second Tk window.

Only Python's standard library is used:

* a named Windows mutex elects one process as the primary;
* an authenticated ``multiprocessing.connection`` AF_PIPE carries commands;
* a per-session descriptor in ``LOCALAPPDATA`` contains only the random pipe
  address, a 32-byte authentication key, the primary PID, and an instance id.

The listener thread never calls Tk.  The primary process should poll
``get_command_nowait()`` from the Tk thread (usually through ``root.after``).
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import AuthenticationError, Client, Listener
from pathlib import Path
from typing import Literal

try:  # package import (tests) / direct script import (PyInstaller build)
    from .protocol import ProtocolLaunch, ProtocolLaunchError, parse_protocol_launch
except ImportError:  # pragma: no cover - exercised by the frozen direct script
    from protocol import ProtocolLaunch, ProtocolLaunchError, parse_protocol_launch


IPC_VERSION = 1
MAX_IPC_PAYLOAD_BYTES = 1024
MAX_DESCRIPTOR_BYTES = 4096
IPC_AUTHKEY_BYTES = 32
IPC_QUEUE_SIZE = 16
IPC_READ_TIMEOUT_SECONDS = 0.75
DEFAULT_FORWARD_TIMEOUT_SECONDS = 3.0

WINDOWS_MUTEX_NAME = r"Local\GGParrot.Runner.IPC.v1"
_PIPE_PREFIX = r"\\.\pipe\GGParrot-Runner-"
_DESCRIPTOR_NAME = "runner-ipc-session-{session_id}.json"
_ERROR_ALREADY_EXISTS = 183

_HEX_32_RE = re.compile(r"^[0-9a-f]{32}$")
_TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_COMMAND_KEYS = frozenset({"ipc_version", "kind"})
_LAUNCH_COMMAND_KEYS = frozenset({"ipc_version", "kind", "launch"})
_LAUNCH_KEYS = frozenset({"version", "environment", "ticket"})
_DESCRIPTOR_KEYS = frozenset(
    {"descriptor_version", "transport", "address", "authkey", "pid", "instance_id"}
)
_ACK_KEYS = frozenset({"ipc_version", "ok"})
_ERROR_ACK_KEYS = frozenset({"ipc_version", "ok", "error"})
_ACK_ERRORS = frozenset({"invalid_command", "queue_full", "shutting_down"})


class SingleInstanceError(RuntimeError):
    """Single-instance setup or handoff could not be completed safely."""


class InvalidIPCMessage(ValueError):
    """An IPC payload did not match the exact, supported JSON contract."""


@dataclass(frozen=True)
class InstanceCommand:
    """A safe command delivered to the primary runner process."""

    kind: Literal["activate", "launch"]
    launch: ProtocolLaunch | None = None

    @classmethod
    def activate(cls) -> "InstanceCommand":
        return cls(kind="activate")

    @classmethod
    def launch_protocol(cls, launch: ProtocolLaunch) -> "InstanceCommand":
        return cls(kind="launch", launch=launch)


@dataclass(frozen=True)
class InstanceAck:
    """Acknowledgement that the primary queued (or rejected) a command."""

    accepted: bool
    error: str = ""


@dataclass(frozen=True)
class InstanceDescriptor:
    """Non-ticket connection metadata stored in the user's LOCALAPPDATA."""

    address: str
    authkey: bytes
    pid: int
    instance_id: str


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidIPCMessage("duplicate JSON key")
        result[key] = value
    return result


def _load_json_object(payload: bytes, *, maximum: int) -> dict[str, object]:
    if not isinstance(payload, bytes) or not payload or len(payload) > maximum:
        raise InvalidIPCMessage("invalid payload size")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except InvalidIPCMessage:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidIPCMessage("invalid JSON") from exc
    if not isinstance(value, dict):
        raise InvalidIPCMessage("JSON root must be an object")
    return value


def _canonical_launch(launch: ProtocolLaunch) -> ProtocolLaunch:
    """Re-parse launch fields so a manually constructed dataclass is not trusted."""

    if type(launch.version) is not int or launch.version not in (1, 2):
        raise InvalidIPCMessage("invalid launch version")
    if not isinstance(launch.ticket, str) or not _TICKET_RE.fullmatch(launch.ticket):
        raise InvalidIPCMessage("invalid launch ticket")
    if launch.version == 1:
        if launch.environment != "production":
            raise InvalidIPCMessage("v1 must target production")
        uri = f"ggparrot://launch/?v=1&ticket={launch.ticket}"
    else:
        if launch.environment not in ("local", "production"):
            raise InvalidIPCMessage("invalid launch environment")
        uri = (
            "ggparrot://launch/?v=2"
            f"&env={launch.environment}&ticket={launch.ticket}"
        )
    try:
        parsed = parse_protocol_launch(["--protocol", uri])
    except ProtocolLaunchError as exc:  # defense in depth if the URI contract changes
        raise InvalidIPCMessage("unsupported launch") from exc
    if parsed is None:  # pragma: no cover - parser contract guarantees a value
        raise InvalidIPCMessage("missing launch")
    return parsed


def encode_command(command: InstanceCommand) -> bytes:
    """Serialize one command using the minimal exact JSON contract."""

    if command.kind == "activate":
        if command.launch is not None:
            raise InvalidIPCMessage("activate cannot include a launch")
        value: dict[str, object] = {
            "ipc_version": IPC_VERSION,
            "kind": "activate",
        }
    elif command.kind == "launch":
        if not isinstance(command.launch, ProtocolLaunch):
            raise InvalidIPCMessage("launch command requires protocol fields")
        launch = _canonical_launch(command.launch)
        value = {
            "ipc_version": IPC_VERSION,
            "kind": "launch",
            "launch": {
                "version": launch.version,
                "environment": launch.environment,
                "ticket": launch.ticket,
            },
        }
    else:
        raise InvalidIPCMessage("unsupported command")
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    if len(payload) > MAX_IPC_PAYLOAD_BYTES:  # should remain far below the cap
        raise InvalidIPCMessage("command is too large")
    return payload


def decode_command(payload: bytes) -> InstanceCommand:
    """Decode a command while rejecting missing, extra, or duplicate fields."""

    value = _load_json_object(payload, maximum=MAX_IPC_PAYLOAD_BYTES)
    if value.get("ipc_version") != IPC_VERSION or type(value.get("ipc_version")) is not int:
        raise InvalidIPCMessage("unsupported IPC version")
    kind = value.get("kind")
    if kind == "activate":
        if frozenset(value) != _COMMAND_KEYS:
            raise InvalidIPCMessage("activate has unexpected fields")
        return InstanceCommand.activate()
    if kind != "launch" or frozenset(value) != _LAUNCH_COMMAND_KEYS:
        raise InvalidIPCMessage("unsupported command shape")
    raw_launch = value.get("launch")
    if not isinstance(raw_launch, dict) or frozenset(raw_launch) != _LAUNCH_KEYS:
        raise InvalidIPCMessage("invalid launch shape")
    version = raw_launch.get("version")
    environment = raw_launch.get("environment")
    ticket = raw_launch.get("ticket")
    if type(version) is not int or version not in (1, 2):
        raise InvalidIPCMessage("invalid launch version")
    if not isinstance(environment, str) or not isinstance(ticket, str):
        raise InvalidIPCMessage("invalid launch field types")
    launch = _canonical_launch(
        ProtocolLaunch(ticket=ticket, environment=environment, version=version)
    )
    return InstanceCommand.launch_protocol(launch)


def encode_ack(ack: InstanceAck) -> bytes:
    if type(ack.accepted) is not bool:
        raise InvalidIPCMessage("invalid acknowledgement")
    value: dict[str, object] = {"ipc_version": IPC_VERSION, "ok": ack.accepted}
    if not ack.accepted:
        if ack.error not in _ACK_ERRORS:
            raise InvalidIPCMessage("invalid acknowledgement error")
        value["error"] = ack.error
    elif ack.error:
        raise InvalidIPCMessage("successful acknowledgement cannot have an error")
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("ascii")


def decode_ack(payload: bytes) -> InstanceAck:
    value = _load_json_object(payload, maximum=MAX_IPC_PAYLOAD_BYTES)
    if value.get("ipc_version") != IPC_VERSION or type(value.get("ipc_version")) is not int:
        raise InvalidIPCMessage("unsupported acknowledgement version")
    accepted = value.get("ok")
    if type(accepted) is not bool:
        raise InvalidIPCMessage("invalid acknowledgement result")
    expected = _ACK_KEYS if accepted else _ERROR_ACK_KEYS
    if frozenset(value) != expected:
        raise InvalidIPCMessage("invalid acknowledgement shape")
    error = value.get("error", "")
    if not accepted and error not in _ACK_ERRORS:
        raise InvalidIPCMessage("invalid acknowledgement error")
    return InstanceAck(accepted=accepted, error=str(error))


def send_command_over_connection(connection, command: InstanceCommand, *, timeout: float) -> InstanceAck:
    """Send bytes (never pickle) and wait for the primary's bounded acknowledgement."""

    connection.send_bytes(encode_command(command))
    if not connection.poll(max(0.0, timeout)):
        raise SingleInstanceError("기존 실행기의 응답 시간이 초과됐어요.")
    try:
        response = connection.recv_bytes(maxlength=MAX_IPC_PAYLOAD_BYTES)
    except (EOFError, OSError) as exc:
        raise SingleInstanceError("기존 실행기의 응답을 읽지 못했어요.") from exc
    return decode_ack(response)


def receive_command_over_connection(connection) -> InstanceCommand:
    """Receive one size-limited command from an authenticated connection."""

    try:
        payload = connection.recv_bytes(maxlength=MAX_IPC_PAYLOAD_BYTES)
    except (EOFError, OSError) as exc:
        raise InvalidIPCMessage("could not receive command") from exc
    return decode_command(payload)


def _encode_descriptor(descriptor: InstanceDescriptor) -> bytes:
    if not _valid_pipe_address(descriptor.address):
        raise InvalidIPCMessage("invalid pipe address")
    if not isinstance(descriptor.authkey, bytes) or len(descriptor.authkey) != IPC_AUTHKEY_BYTES:
        raise InvalidIPCMessage("invalid authentication key")
    if type(descriptor.pid) is not int or descriptor.pid <= 0:
        raise InvalidIPCMessage("invalid primary pid")
    if not isinstance(descriptor.instance_id, str) or not _HEX_32_RE.fullmatch(descriptor.instance_id):
        raise InvalidIPCMessage("invalid instance id")
    authkey = base64.urlsafe_b64encode(descriptor.authkey).decode("ascii")
    value = {
        "descriptor_version": IPC_VERSION,
        "transport": "AF_PIPE",
        "address": descriptor.address,
        "authkey": authkey,
        "pid": descriptor.pid,
        "instance_id": descriptor.instance_id,
    }
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("ascii")
    if len(payload) > MAX_DESCRIPTOR_BYTES:
        raise InvalidIPCMessage("descriptor is too large")
    return payload


def _decode_descriptor(payload: bytes) -> InstanceDescriptor:
    value = _load_json_object(payload, maximum=MAX_DESCRIPTOR_BYTES)
    if frozenset(value) != _DESCRIPTOR_KEYS:
        raise InvalidIPCMessage("invalid descriptor shape")
    if value.get("descriptor_version") != IPC_VERSION or type(value.get("descriptor_version")) is not int:
        raise InvalidIPCMessage("unsupported descriptor version")
    if value.get("transport") != "AF_PIPE":
        raise InvalidIPCMessage("unsupported descriptor transport")
    address = value.get("address")
    authkey_text = value.get("authkey")
    pid = value.get("pid")
    instance_id = value.get("instance_id")
    if not isinstance(address, str) or not _valid_pipe_address(address):
        raise InvalidIPCMessage("invalid pipe address")
    if not isinstance(authkey_text, str):
        raise InvalidIPCMessage("invalid authentication key")
    try:
        authkey = base64.b64decode(authkey_text, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise InvalidIPCMessage("invalid authentication key") from exc
    if len(authkey) != IPC_AUTHKEY_BYTES or base64.urlsafe_b64encode(authkey).decode("ascii") != authkey_text:
        raise InvalidIPCMessage("invalid authentication key")
    if type(pid) is not int or pid <= 0:
        raise InvalidIPCMessage("invalid primary pid")
    if not isinstance(instance_id, str) or not _HEX_32_RE.fullmatch(instance_id):
        raise InvalidIPCMessage("invalid instance id")
    return InstanceDescriptor(address=address, authkey=authkey, pid=pid, instance_id=instance_id)


def _valid_pipe_address(address: str) -> bool:
    if not address.startswith(_PIPE_PREFIX):
        return False
    suffix = address[len(_PIPE_PREFIX):]
    return bool(_HEX_32_RE.fullmatch(suffix))


def write_instance_descriptor(path: Path, descriptor: InstanceDescriptor) -> None:
    """Atomically publish connection metadata without ever storing a ticket."""

    payload = _encode_descriptor(descriptor)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.new")
    descriptor_fd: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor_fd = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor_fd, "wb") as stream:
            descriptor_fd = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            # Windows ultimately enforces the inherited per-user LOCALAPPDATA ACL.
            pass
        os.replace(temporary, path)
    finally:
        if descriptor_fd is not None:
            os.close(descriptor_fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_instance_descriptor(path: Path) -> InstanceDescriptor:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SingleInstanceError("기존 실행기의 연결 정보를 찾지 못했어요.") from exc
    try:
        return _decode_descriptor(payload)
    except InvalidIPCMessage as exc:
        raise SingleInstanceError("기존 실행기의 연결 정보가 올바르지 않아요.") from exc


def _windows_libraries():
    if sys.platform != "win32":
        raise SingleInstanceError("단일 실행기 연결은 Windows에서만 사용할 수 있어요.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.ProcessIdToSessionId.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.ProcessIdToSessionId.restype = ctypes.c_int
    kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    kernel32.WaitNamedPipeW.restype = ctypes.c_int
    user32.AllowSetForegroundWindow.argtypes = [ctypes.c_uint32]
    user32.AllowSetForegroundWindow.restype = ctypes.c_int
    return kernel32, user32


def _windows_session_id(kernel32) -> int:
    session_id = ctypes.c_uint32()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        raise SingleInstanceError("Windows 세션을 확인하지 못했어요.")
    return int(session_id.value)


class RunnerSingleInstance:
    """Own or contact the one runner instance in the current Windows session.

    Call :meth:`acquire` before constructing ``tk.Tk``.  A primary instance has
    a live authenticated listener and exposes commands through
    :meth:`get_command_nowait`.  A secondary should call :meth:`handoff` and
    return from ``main`` immediately.
    """

    def __init__(
        self,
        *,
        primary: bool,
        mutex_handle,
        owns_mutex: bool,
        descriptor_path: Path,
        kernel32,
        user32,
    ) -> None:
        self.is_primary = primary
        self._mutex_handle = mutex_handle
        self._owns_mutex = owns_mutex
        self._descriptor_path = descriptor_path
        self._kernel32 = kernel32
        self._user32 = user32
        self._descriptor: InstanceDescriptor | None = None
        self._listener = None
        self._listener_thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._commands: queue.Queue[InstanceCommand] = queue.Queue(maxsize=IPC_QUEUE_SIZE)

    @classmethod
    def acquire(
        cls,
        *,
        local_app_data: str | None = None,
        mutex_name: str = WINDOWS_MUTEX_NAME,
    ) -> "RunnerSingleInstance":
        """Elect the primary and, for it, publish an authenticated AF_PIPE."""

        try:
            kernel32, user32 = _windows_libraries()
            session_id = _windows_session_id(kernel32)
            app_data = (local_app_data or os.environ.get("LOCALAPPDATA", "")).strip()
            if not app_data:
                raise SingleInstanceError("LOCALAPPDATA에서 실행기 연결 경로를 찾지 못했어요.")
            descriptor_path = Path(app_data) / "GGParrot" / _DESCRIPTOR_NAME.format(session_id=session_id)

            ctypes.set_last_error(0)
            mutex_handle = kernel32.CreateMutexW(None, True, mutex_name)
            mutex_error = ctypes.get_last_error()
            if not mutex_handle:
                raise SingleInstanceError("실행기 단일 인스턴스 잠금을 만들지 못했어요.")
        except SingleInstanceError:
            raise
        except Exception as exc:
            raise SingleInstanceError("실행기 단일 인스턴스 준비에 실패했어요.") from exc
        primary = mutex_error != _ERROR_ALREADY_EXISTS
        instance = cls(
            primary=primary,
            mutex_handle=mutex_handle,
            owns_mutex=primary,
            descriptor_path=descriptor_path,
            kernel32=kernel32,
            user32=user32,
        )
        if not primary:
            return instance

        try:
            instance._start_primary_listener()
        except Exception as exc:
            instance.close()
            if isinstance(exc, SingleInstanceError):
                raise
            raise SingleInstanceError("실행기 내부 연결 통로를 준비하지 못했어요.") from exc
        return instance

    def _start_primary_listener(self) -> None:
        authkey = secrets.token_bytes(IPC_AUTHKEY_BYTES)
        instance_id = secrets.token_hex(16)
        address = _PIPE_PREFIX + secrets.token_hex(16)
        try:
            listener = Listener(address=address, family="AF_PIPE", authkey=authkey)
        except Exception as exc:
            raise SingleInstanceError("실행기 내부 연결 통로를 열지 못했어요.") from exc
        descriptor = InstanceDescriptor(
            address=address,
            authkey=authkey,
            pid=os.getpid(),
            instance_id=instance_id,
        )
        try:
            write_instance_descriptor(self._descriptor_path, descriptor)
        except Exception:
            listener.close()
            raise
        self._listener = listener
        self._descriptor = descriptor
        self._listener_thread = threading.Thread(
            target=self._listen,
            daemon=True,
            name="ggparrot-instance-ipc",
        )
        self._listener_thread.start()

    def _listen(self) -> None:
        # Keep one final accept available after ``close()`` sets the event.  The
        # authenticated self-connection in ``_wake_listener`` then releases a
        # blocked AF_PIPE accept without waiting for process teardown.
        while True:
            connection = None
            try:
                connection = self._listener.accept()
                if self._closed.is_set():
                    break
                if not connection.poll(IPC_READ_TIMEOUT_SECONDS):
                    continue
                try:
                    command = receive_command_over_connection(connection)
                except InvalidIPCMessage:
                    connection.send_bytes(
                        encode_ack(InstanceAck(False, "invalid_command"))
                    )
                    continue
                try:
                    self._commands.put_nowait(command)
                except queue.Full:
                    ack = InstanceAck(False, "queue_full")
                else:
                    ack = InstanceAck(True)
                connection.send_bytes(encode_ack(ack))
            except (AuthenticationError, EOFError, OSError):
                # Authentication and pipe failures contain no useful user-facing
                # detail and must not terminate the primary listener.
                if self._closed.is_set():
                    break
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass

    def get_command_nowait(self) -> InstanceCommand:
        """Return the next command; call only from the primary/Tk thread."""

        if not self.is_primary:
            raise SingleInstanceError("보조 프로세스에는 명령 대기열이 없어요.")
        return self._commands.get_nowait()

    def forward(self, command: InstanceCommand, *, timeout: float = DEFAULT_FORWARD_TIMEOUT_SECONDS) -> InstanceAck:
        """Forward one command from a secondary process to the primary."""

        if self.is_primary:
            raise SingleInstanceError("주 실행기는 자기 자신에게 명령을 전달할 수 없어요.")
        # Reject programmer errors before entering the transient connection
        # retry loop. In particular, never retry or transmit malformed tickets.
        encode_command(command)
        deadline = time.monotonic() + max(0.1, timeout)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                descriptor = read_instance_descriptor(self._descriptor_path)
                self._allow_primary_foreground(descriptor.pid)
                remaining_ms = max(1, min(250, int((deadline - time.monotonic()) * 1000)))
                if not self._kernel32.WaitNamedPipeW(descriptor.address, remaining_ms):
                    time.sleep(0.04)
                    continue
                connection = Client(
                    address=descriptor.address,
                    family="AF_PIPE",
                    authkey=descriptor.authkey,
                )
                try:
                    return send_command_over_connection(
                        connection,
                        command,
                        timeout=max(0.1, deadline - time.monotonic()),
                    )
                finally:
                    connection.close()
            except (AuthenticationError, InvalidIPCMessage, OSError, SingleInstanceError) as exc:
                last_error = exc
                time.sleep(0.04)
        raise SingleInstanceError("열려 있는 실행기에 연결하지 못했어요.") from last_error

    def handoff(self, command: InstanceCommand, *, timeout: float = DEFAULT_FORWARD_TIMEOUT_SECONDS) -> InstanceAck:
        """Forward as a secondary and release its mutex handle before returning."""

        if self.is_primary:
            raise SingleInstanceError("주 실행기는 handoff를 사용할 수 없어요.")
        try:
            return self.forward(command, timeout=timeout)
        finally:
            self.close()

    def _allow_primary_foreground(self, pid: int) -> None:
        try:
            self._user32.AllowSetForegroundWindow(pid)
        except Exception:
            # The primary still performs ShowWindow/lift as a fallback.
            pass

    def close(self) -> None:
        """Stop IPC and release descriptor/mutex ownership. Idempotent."""

        if self._closed.is_set():
            self._close_mutex_handle()
            return
        self._closed.set()
        if self.is_primary:
            self._remove_owned_descriptor()
            self._wake_listener()
            if self._listener_thread is not None:
                self._listener_thread.join(timeout=IPC_READ_TIMEOUT_SECONDS + 1.0)
            if self._listener is not None:
                try:
                    self._listener.close()
                except OSError:
                    pass
        self._close_mutex_handle()

    def _wake_listener(self) -> None:
        descriptor = self._descriptor
        if descriptor is None or self._listener_thread is None or not self._listener_thread.is_alive():
            return
        try:
            connection = Client(
                address=descriptor.address,
                family="AF_PIPE",
                authkey=descriptor.authkey,
            )
            connection.close()
        except Exception:
            pass

    def _remove_owned_descriptor(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        try:
            current = read_instance_descriptor(self._descriptor_path)
            if current.instance_id == descriptor.instance_id:
                self._descriptor_path.unlink()
        except (OSError, SingleInstanceError):
            pass

    def _close_mutex_handle(self) -> None:
        handle = self._mutex_handle
        if not handle:
            return
        self._mutex_handle = None
        if self._owns_mutex:
            try:
                self._kernel32.ReleaseMutex(handle)
            except Exception:
                pass
            self._owns_mutex = False
        try:
            self._kernel32.CloseHandle(handle)
        except Exception:
            pass

    def __enter__(self) -> "RunnerSingleInstance":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


__all__ = [
    "DEFAULT_FORWARD_TIMEOUT_SECONDS",
    "IPC_AUTHKEY_BYTES",
    "IPC_VERSION",
    "InstanceAck",
    "InstanceCommand",
    "InstanceDescriptor",
    "InvalidIPCMessage",
    "MAX_IPC_PAYLOAD_BYTES",
    "RunnerSingleInstance",
    "SingleInstanceError",
    "decode_ack",
    "decode_command",
    "encode_ack",
    "encode_command",
    "read_instance_descriptor",
    "receive_command_over_connection",
    "send_command_over_connection",
    "write_instance_descriptor",
]
