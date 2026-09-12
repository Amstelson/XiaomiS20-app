"""The miIO packet protocol, implemented directly.

Why this exists
---------------
We originally used python-miio for this. Its released version declares
`netifaces`, `micloud` and `android_backup` as hard requirements: `netifaces`
publishes no wheels past CPython 3.9, and `micloud` is sdist-only with a
setup.py that fails on modern setuptools. So on any current Python the install
needs a C toolchain and still tends to fail.

We only ever used two things from it -- the handshake and an encrypted JSON
round-trip. Both are small and completely specified, so implementing them here
removes three unbuildable transitive dependencies and leaves one that ships
universal wheels (pycryptodome).

The protocol
------------
Every packet is a 32-byte header followed by an AES-encrypted JSON payload::

    offset  size  field
    0       2     magic, always 0x2131
    2       2     total packet length, big-endian
    4       4     unknown (0xFFFFFFFF in a handshake, else 0)
    8       4     device id
    12      4     stamp -- the device's uptime clock, in seconds
    16      16    MD5 checksum
    32      ..    AES-128-CBC payload, PKCS#7 padded

The checksum is the MD5 of the whole packet computed with the token sitting in
the checksum field. Key and IV both derive from the token::

    key = MD5(token)
    iv  = MD5(key + token)

The stamp must track the device's own clock, so we handshake to learn it and
then advance it by our own elapsed time. Getting this wrong is the usual cause
of a device ignoring otherwise valid packets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import struct
import time
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger(__name__)

MAGIC = 0x2131
HEADER_SIZE = 32
DEFAULT_PORT = 54321

#: The fixed handshake request: magic, length 0x20, then all bits set.
HANDSHAKE = bytes.fromhex("21310020" + "ff" * 28)


class MiioProtocolError(RuntimeError):
    pass


class MiioTimeout(MiioProtocolError):
    pass


class MiioChecksumError(MiioProtocolError):
    pass


class MiioDeviceError(MiioProtocolError):
    """The device replied, but with an error object."""

    def __init__(self, error: Any):
        super().__init__(f"device returned an error: {error}")
        self.error = error
        self.code = error.get("code") if isinstance(error, dict) else None


# -- crypto ------------------------------------------------------------------


def _aes_cbc(key: bytes, iv: bytes):
    """Return (encrypt, decrypt) callables, from whichever backend is present."""
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad, unpad

        def encrypt(data: bytes) -> bytes:
            return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(data, AES.block_size))

        def decrypt(data: bytes) -> bytes:
            return unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(data), AES.block_size)

        return encrypt, decrypt
    except ImportError:
        pass

    try:
        from cryptography.hazmat.primitives import padding as _padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        def encrypt(data: bytes) -> bytes:
            padder = _padding.PKCS7(128).padder()
            encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
            return encryptor.update(padder.update(data) + padder.finalize()) + (
                encryptor.finalize()
            )

        def decrypt(data: bytes) -> bytes:
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            unpadder = _padding.PKCS7(128).unpadder()
            plain = decryptor.update(data) + decryptor.finalize()
            return unpadder.update(plain) + unpadder.finalize()

        return encrypt, decrypt
    except ImportError as exc:
        raise MiioProtocolError(
            "No AES backend. Run: pip install -r requirements.txt"
        ) from exc


def token_bytes(token: str | bytes) -> bytes:
    if isinstance(token, bytes):
        raw = token
    else:
        text = token.strip()
        try:
            raw = bytes.fromhex(text)
        except ValueError as exc:
            raise MiioProtocolError(
                f"token must be 32 hex characters, got {text!r}"
            ) from exc
    if len(raw) != 16:
        raise MiioProtocolError(f"token must be 16 bytes, got {len(raw)}")
    return raw


def derive_key_iv(token: str | bytes) -> tuple[bytes, bytes]:
    raw = token_bytes(token)
    key = hashlib.md5(raw).digest()
    iv = hashlib.md5(key + raw).digest()
    return key, iv


def encrypt_payload(token: str | bytes, plaintext: bytes) -> bytes:
    key, iv = derive_key_iv(token)
    return _aes_cbc(key, iv)[0](plaintext)


def decrypt_payload(token: str | bytes, ciphertext: bytes) -> bytes:
    key, iv = derive_key_iv(token)
    return _aes_cbc(key, iv)[1](ciphertext)


# -- packets -----------------------------------------------------------------


@dataclass(frozen=True)
class Packet:
    device_id: int
    stamp: int
    payload: bytes

    @property
    def is_handshake(self) -> bool:
        return not self.payload


def build_packet(
    token: str | bytes, device_id: int, stamp: int, plaintext: bytes
) -> bytes:
    """Assemble an encrypted, checksummed packet."""
    raw_token = token_bytes(token)
    encrypted = encrypt_payload(raw_token, plaintext) if plaintext else b""
    header = struct.pack(
        ">HHIII", MAGIC, HEADER_SIZE + len(encrypted), 0, device_id, stamp
    )
    # The checksum covers the packet with the token standing in for the
    # checksum field itself.
    checksum = hashlib.md5(header + raw_token + encrypted).digest()
    return header + checksum + encrypted


def parse_packet(token: str | bytes, data: bytes, *, verify: bool = True) -> Packet:
    """Validate and decrypt a received packet."""
    if len(data) < HEADER_SIZE:
        raise MiioProtocolError(f"packet too short: {len(data)} bytes")

    magic, length, _unknown, device_id, stamp = struct.unpack(">HHIII", data[:16])
    if magic != MAGIC:
        raise MiioProtocolError(f"bad magic 0x{magic:04x}")
    if length != len(data):
        raise MiioProtocolError(f"length says {length}, got {len(data)} bytes")

    checksum = data[16:HEADER_SIZE]
    encrypted = data[HEADER_SIZE:]

    if not encrypted:
        # A handshake reply carries no payload; its checksum field holds either
        # the token or filler, so there is nothing to verify.
        return Packet(device_id=device_id, stamp=stamp, payload=b"")

    if verify:
        raw_token = token_bytes(token)
        expected = hashlib.md5(data[:16] + raw_token + encrypted).digest()
        if expected != checksum:
            raise MiioChecksumError(
                "checksum mismatch -- the token is probably wrong for this device"
            )

    return Packet(
        device_id=device_id, stamp=stamp, payload=decrypt_payload(token, encrypted)
    )


# -- transport ---------------------------------------------------------------


class MiioProtocol:
    """Talks to one device over UDP."""

    def __init__(
        self,
        ip: str,
        token: str,
        *,
        port: int = DEFAULT_PORT,
        timeout: float = 5.0,
    ) -> None:
        self.ip = ip
        self.token = token
        self.port = port
        self.timeout = timeout
        self.device_id: int | None = None
        self._stamp = 0
        self._stamp_at = 0.0
        self._request_id = 0

    # -- handshake -----------------------------------------------------------

    @property
    def handshaken(self) -> bool:
        return self.device_id is not None

    def current_stamp(self) -> int:
        """The device's clock as it should read now."""
        return self._stamp + int(time.monotonic() - self._stamp_at)

    def handshake(self) -> Packet:
        """Learn the device id and clock. Required before any command."""
        raw = self._exchange(HANDSHAKE)
        packet = parse_packet(self.token, raw, verify=False)
        self.device_id = packet.device_id
        self._stamp = packet.stamp
        self._stamp_at = time.monotonic()
        _LOG.debug(
            "handshake ok: device_id=%s stamp=%s", packet.device_id, packet.stamp
        )
        return packet

    # -- commands ------------------------------------------------------------

    def send(self, method: str, params: Any = None, *, retries: int = 2) -> Any:
        """Send a JSON-RPC command and return its `result`."""
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                if not self.handshaken:
                    self.handshake()
                return self._send_once(method, params)
            except (MiioTimeout, MiioChecksumError) as exc:
                last = exc
                # A stale clock looks exactly like a timeout, so re-handshake
                # before giving up.
                _LOG.debug("attempt %d for %s failed: %s", attempt + 1, method, exc)
                self.device_id = None
                if attempt < retries:
                    time.sleep(0.3 * (attempt + 1))
            except MiioDeviceError:
                raise
        raise last or MiioProtocolError(f"{method} failed")

    def _send_once(self, method: str, params: Any) -> Any:
        self._request_id = (self._request_id + 1) % 10000
        request = {"id": self._request_id, "method": method}
        request["params"] = params if params is not None else []
        plaintext = json.dumps(request, separators=(",", ":")).encode()

        packet = build_packet(
            self.token, self.device_id or 0, self.current_stamp(), plaintext
        )
        raw = self._exchange(packet)
        reply = parse_packet(self.token, raw)

        # Keep our clock aligned with the device's on every exchange.
        self._stamp = reply.stamp
        self._stamp_at = time.monotonic()

        if not reply.payload:
            return None

        text = reply.payload.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MiioProtocolError(f"undecodable reply: {text!r}") from exc

        if isinstance(decoded, dict):
            if "error" in decoded:
                raise MiioDeviceError(decoded["error"])
            if "result" in decoded:
                return decoded["result"]
        return decoded

    # -- socket --------------------------------------------------------------

    def _exchange(self, payload: bytes) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        try:
            sock.sendto(payload, (self.ip, self.port))
            data, _ = sock.recvfrom(4096)
            return data
        except socket.timeout as exc:
            raise MiioTimeout(
                f"no reply from {self.ip}:{self.port} within {self.timeout}s"
            ) from exc
        except OSError as exc:
            raise MiioProtocolError(f"network error talking to {self.ip}: {exc}") from exc
        finally:
            sock.close()
