"""Local discovery tests."""

import socket
import struct
import threading

import pytest

from hub.discovery import (
    Discovered,
    broadcast_addresses,
    discover,
    parse_discovery_reply,
    subnet_targets,
)
from hub.miio_protocol import HANDSHAKE, MAGIC

DEVICE_ID = 0x0ABCDEF1
TOKEN = "00112233445566778899aabbccddeeff"


def reply(checksum: bytes, device_id: int = DEVICE_ID, stamp: int = 4242) -> bytes:
    return struct.pack(">HHIII", MAGIC, 32, 0xFFFFFFFF, device_id, stamp) + checksum


# -- parsing -----------------------------------------------------------------


def test_parses_a_handshake_reply():
    device = parse_discovery_reply(reply(b"\xff" * 16), "192.168.1.50")
    assert device == Discovered(
        ip="192.168.1.50", device_id=DEVICE_ID, stamp=4242, token=None
    )


def test_filler_checksums_are_not_mistaken_for_a_token():
    for filler in (b"\xff" * 16, b"\x00" * 16):
        assert parse_discovery_reply(reply(filler), "10.0.0.1").token is None


def test_exposed_token_is_returned_as_hex():
    device = parse_discovery_reply(reply(bytes.fromhex(TOKEN)), "10.0.0.1")
    assert device.token == TOKEN


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"tooshort",
        struct.pack(">HHIII", 0x1234, 32, 0, 1, 1) + b"\xff" * 16,  # wrong magic
        struct.pack(">HHIII", MAGIC, 4, 0, 1, 1) + b"\xff" * 16,  # absurd length
    ],
)
def test_non_miio_replies_ignored(data):
    assert parse_discovery_reply(data, "10.0.0.1") is None


def test_describe_is_readable_both_ways():
    assert "not exposed" in parse_discovery_reply(
        reply(b"\xff" * 16), "10.0.0.1"
    ).describe()
    assert TOKEN in parse_discovery_reply(
        reply(bytes.fromhex(TOKEN)), "10.0.0.1"
    ).describe()


# -- addressing --------------------------------------------------------------


def test_broadcast_addresses_include_the_local_subnet():
    assert broadcast_addresses("192.168.1.42") == ["255.255.255.255", "192.168.1.255"]


def test_broadcast_addresses_tolerate_a_missing_hint():
    assert broadcast_addresses(None) == ["255.255.255.255"]
    assert broadcast_addresses("not-an-ip") == ["255.255.255.255"]


def test_subnet_targets_lists_usable_hosts():
    targets = subnet_targets("192.168.1.0/24")
    assert len(targets) == 254
    assert targets[0] == "192.168.1.1" and targets[-1] == "192.168.1.254"


def test_oversized_subnet_rejected_rather_than_flooding_the_network():
    with pytest.raises(ValueError, match="narrow it"):
        subnet_targets("10.0.0.0/16")


# -- the wire ----------------------------------------------------------------


class FakeResponder:
    """Answers the handshake on localhost, like a robot would."""

    def __init__(self, checksum=b"\xff" * 16):
        self.checksum = checksum
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
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if data == HANDSHAKE:
                self.sock.sendto(reply(self.checksum), addr)

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        self.sock.close()


@pytest.fixture
def responder():
    fake = FakeResponder()
    yield fake
    fake.close()


def test_unicast_discovery_finds_the_device(responder):
    found = discover(
        timeout=1.5,
        targets=["127.0.0.1"],
        port=responder.port,
        use_broadcast=False,
    )
    assert [d.device_id for d in found] == [DEVICE_ID]
    assert found[0].ip == "127.0.0.1"


def test_discovery_returns_empty_when_nothing_answers():
    assert (
        discover(timeout=0.5, targets=["127.0.0.1"], port=59998, use_broadcast=False)
        == []
    )


def test_exposed_token_survives_the_round_trip():
    fake = FakeResponder(checksum=bytes.fromhex(TOKEN))
    try:
        found = discover(
            timeout=1.5, targets=["127.0.0.1"], port=fake.port, use_broadcast=False
        )
        assert found[0].token == TOKEN
    finally:
        fake.close()
