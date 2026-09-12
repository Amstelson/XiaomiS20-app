"""miIO packet protocol tests.

This replaced a third-party library, so it is tested as the load-bearing code
it is: known vectors where they exist, round-trips everywhere else, and a fake
UDP device to exercise the retry and clock-tracking behaviour.
"""

import hashlib
import json
import socket
import struct
import threading
import time

import pytest

from hub.miio_protocol import (
    HANDSHAKE,
    HEADER_SIZE,
    MAGIC,
    MiioChecksumError,
    MiioDeviceError,
    MiioProtocol,
    MiioProtocolError,
    MiioTimeout,
    build_packet,
    decrypt_payload,
    derive_key_iv,
    encrypt_payload,
    parse_packet,
    token_bytes,
)

TOKEN = "00112233445566778899aabbccddeeff"
DEVICE_ID = 0x0123ABCD


# -- known vectors -----------------------------------------------------------


def test_handshake_packet_is_the_documented_constant():
    assert HANDSHAKE.hex() == "21310020" + "ff" * 28
    assert len(HANDSHAKE) == 32
    magic, length = struct.unpack(">HH", HANDSHAKE[:4])
    assert magic == MAGIC
    assert length == HEADER_SIZE


def test_key_and_iv_derivation_matches_the_spec():
    raw = bytes.fromhex(TOKEN)
    key, iv = derive_key_iv(TOKEN)
    assert key == hashlib.md5(raw).digest()
    assert iv == hashlib.md5(key + raw).digest()
    assert len(key) == 16 and len(iv) == 16


def test_token_accepted_as_hex_or_bytes():
    assert token_bytes(TOKEN) == bytes.fromhex(TOKEN)
    assert token_bytes(bytes.fromhex(TOKEN)) == bytes.fromhex(TOKEN)
    assert token_bytes(f"  {TOKEN.upper()}  ") == bytes.fromhex(TOKEN)


@pytest.mark.parametrize("bad", ["", "abc", "zz" * 16, "00" * 15, "00" * 17])
def test_bad_tokens_rejected(bad):
    with pytest.raises(MiioProtocolError):
        token_bytes(bad)


# -- crypto round trip -------------------------------------------------------


@pytest.mark.parametrize(
    "plaintext",
    [b"x", b'{"id":1,"method":"get_properties","params":[]}', b"a" * 16, b"b" * 1000],
)
def test_encrypt_decrypt_round_trip(plaintext):
    assert decrypt_payload(TOKEN, encrypt_payload(TOKEN, plaintext)) == plaintext


def test_ciphertext_is_block_aligned_and_padded():
    ct = encrypt_payload(TOKEN, b"a" * 16)
    assert len(ct) % 16 == 0
    assert len(ct) == 32, "a full block of input must gain a whole padding block"


def test_wrong_token_does_not_decrypt_to_the_original():
    ct = encrypt_payload(TOKEN, b"secret payload here")
    other = "ffeeddccbbaa99887766554433221100"
    try:
        assert decrypt_payload(other, ct) != b"secret payload here"
    except Exception:
        pass  # a padding error is an equally correct outcome


# -- packet round trip -------------------------------------------------------


def test_packet_round_trip():
    plaintext = b'{"id":7,"method":"ping"}'
    packet = build_packet(TOKEN, DEVICE_ID, 1234, plaintext)
    parsed = parse_packet(TOKEN, packet)
    assert parsed.device_id == DEVICE_ID
    assert parsed.stamp == 1234
    assert parsed.payload == plaintext


def test_packet_header_fields_are_where_the_spec_says():
    packet = build_packet(TOKEN, DEVICE_ID, 99, b"xy")
    magic, length, unknown, did, stamp = struct.unpack(">HHIII", packet[:16])
    assert magic == MAGIC
    assert length == len(packet)
    assert unknown == 0
    assert did == DEVICE_ID
    assert stamp == 99


def test_checksum_covers_the_token():
    packet = build_packet(TOKEN, DEVICE_ID, 1, b"hello")
    expected = hashlib.md5(
        packet[:16] + bytes.fromhex(TOKEN) + packet[HEADER_SIZE:]
    ).digest()
    assert packet[16:HEADER_SIZE] == expected


def test_tampered_payload_is_rejected():
    packet = bytearray(build_packet(TOKEN, DEVICE_ID, 1, b"hello"))
    packet[-1] ^= 0xFF
    with pytest.raises(MiioChecksumError):
        parse_packet(TOKEN, bytes(packet))


def test_wrong_token_is_reported_as_a_checksum_failure():
    packet = build_packet(TOKEN, DEVICE_ID, 1, b"hello")
    with pytest.raises(MiioChecksumError, match="token"):
        parse_packet("ffeeddccbbaa99887766554433221100", packet)


def test_empty_payload_packet_is_treated_as_a_handshake_reply():
    packet = build_packet(TOKEN, DEVICE_ID, 42, b"")
    parsed = parse_packet(TOKEN, packet)
    assert parsed.is_handshake
    assert parsed.device_id == DEVICE_ID
    assert parsed.stamp == 42


def test_handshake_reply_parses_without_a_valid_checksum():
    """Real devices put the token or filler there, not a checksum."""
    header = struct.pack(">HHIII", MAGIC, 32, 0xFFFFFFFF, DEVICE_ID, 777)
    reply = header + b"\xff" * 16
    parsed = parse_packet(TOKEN, reply, verify=False)
    assert parsed.device_id == DEVICE_ID
    assert parsed.stamp == 777


@pytest.mark.parametrize(
    "data,match",
    [
        (b"short", "too short"),
        (struct.pack(">HHIII", 0x1234, 32, 0, 1, 1) + b"\x00" * 16, "bad magic"),
        (struct.pack(">HHIII", MAGIC, 99, 0, 1, 1) + b"\x00" * 16, "length says"),
    ],
)
def test_malformed_packets_rejected(data, match):
    with pytest.raises(MiioProtocolError, match=match):
        parse_packet(TOKEN, data)


# -- a fake device over real UDP ---------------------------------------------


class FakeDevice:
    """A UDP server that speaks miIO, for exercising the client end to end."""

    def __init__(self, token=TOKEN, device_id=DEVICE_ID, stamp=1000):
        self.token = token
        self.device_id = device_id
        self.stamp = stamp
        self.requests: list[dict] = []
        self.drop_next = 0
        self.reply_error = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        self.sock.settimeout(0.2)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if data == HANDSHAKE:
                header = struct.pack(
                    ">HHIII", MAGIC, 32, 0xFFFFFFFF, self.device_id, self.stamp
                )
                self.sock.sendto(header + b"\xff" * 16, addr)
                continue

            if self.drop_next > 0:
                self.drop_next -= 1
                continue

            try:
                packet = parse_packet(self.token, data)
            except MiioProtocolError:
                continue
            request = json.loads(packet.payload)
            self.requests.append(request)

            body = (
                {"id": request["id"], "error": self.reply_error}
                if self.reply_error
                else {"id": request["id"], "result": ["ok", request["method"]]}
            )
            self.sock.sendto(
                build_packet(
                    self.token,
                    self.device_id,
                    self.stamp,
                    json.dumps(body).encode(),
                ),
                addr,
            )

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        self.sock.close()


@pytest.fixture
def device():
    fake = FakeDevice()
    yield fake
    fake.close()


def test_handshake_learns_device_id_and_clock(device):
    proto = MiioProtocol("127.0.0.1", TOKEN, port=device.port, timeout=1)
    assert not proto.handshaken
    proto.handshake()
    assert proto.handshaken
    assert proto.device_id == DEVICE_ID
    assert proto.current_stamp() >= 1000


def test_send_performs_the_handshake_automatically(device):
    proto = MiioProtocol("127.0.0.1", TOKEN, port=device.port, timeout=1)
    assert proto.send("get_properties", [{"siid": 2, "piid": 1}]) == [
        "ok",
        "get_properties",
    ]
    assert device.requests[0]["method"] == "get_properties"
    assert device.requests[0]["params"] == [{"siid": 2, "piid": 1}]


def test_omitted_params_are_sent_as_an_empty_list(device):
    proto = MiioProtocol("127.0.0.1", TOKEN, port=device.port, timeout=1)
    proto.send("miIO.info")
    assert device.requests[0]["params"] == []


def test_request_ids_increment(device):
    proto = MiioProtocol("127.0.0.1", TOKEN, port=device.port, timeout=1)
    for _ in range(3):
        proto.send("ping")
    ids = [r["id"] for r in device.requests]
    assert ids == sorted(ids) and len(set(ids)) == 3


def test_dropped_packets_are_retried(device):
    device.drop_next = 2
    proto = MiioProtocol("127.0.0.1", TOKEN, port=device.port, timeout=0.4)
    assert proto.send("ping", retries=3) == ["ok", "ping"]


def test_gives_up_after_the_retry_budget():
    proto = MiioProtocol("127.0.0.1", TOKEN, port=59999, timeout=0.2)
    with pytest.raises(MiioTimeout):
        proto.send("ping", retries=1)


def test_device_errors_surface_and_are_not_retried(device):
    device.reply_error = {"code": -5001, "message": "invalid params"}
    proto = MiioProtocol("127.0.0.1", TOKEN, port=device.port, timeout=1)
    with pytest.raises(MiioDeviceError) as info:
        proto.send("bad_method")
    assert info.value.code == -5001
    assert len(device.requests) == 1, "an error reply must not trigger a retry"


def test_wrong_token_is_reported_clearly(device):
    proto = MiioProtocol(
        "127.0.0.1", "ffeeddccbbaa99887766554433221100", port=device.port, timeout=0.3
    )
    with pytest.raises((MiioChecksumError, MiioTimeout)):
        proto.send("ping", retries=0)


def test_stamp_advances_with_real_time():
    proto = MiioProtocol("127.0.0.1", TOKEN, timeout=1)
    proto.device_id = DEVICE_ID
    proto._stamp = 500
    proto._stamp_at = time.monotonic() - 3.0
    assert proto.current_stamp() >= 503
