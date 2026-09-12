"""Find Xiaomi devices on the local network, without the cloud.

Every miIO device answers the fixed handshake packet with its device id and
clock. That makes discovery a one-packet affair and, usefully, independent of
the Xiaomi cloud entirely -- so a robot can be located and confirmed reachable
before any account or token is in hand.

Some devices also leave their **token** in the handshake reply's checksum
field. Firmware since roughly 2017 fills it with 0xFF instead, so treat this as
a bonus rather than the expected path.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import time
from dataclasses import dataclass

from .miio_protocol import HANDSHAKE, HEADER_SIZE, MAGIC

_LOG = logging.getLogger(__name__)

#: Checksum fields that carry no token, just filler.
_FILLER = (b"\xff" * 16, b"\x00" * 16)


@dataclass(frozen=True)
class Discovered:
    ip: str
    device_id: int
    stamp: int
    token: str | None = None

    def describe(self) -> str:
        token = self.token or "not exposed (normal on current firmware)"
        return f"{self.ip:<16} device id {self.device_id:<12} token: {token}"


def parse_discovery_reply(data: bytes, ip: str) -> Discovered | None:
    """Turn a handshake reply into a Discovered, or None if it is not one."""
    if len(data) < HEADER_SIZE:
        return None
    magic, length, _unknown, device_id, stamp = struct.unpack(">HHIII", data[:16])
    if magic != MAGIC or length < HEADER_SIZE:
        return None

    checksum = data[16:HEADER_SIZE]
    token = None
    if checksum not in _FILLER:
        token = checksum.hex()

    return Discovered(ip=ip, device_id=device_id, stamp=stamp, token=token)


def broadcast_addresses(hint: str | None = None) -> list[str]:
    """Addresses worth sending the handshake to.

    The global broadcast address is blocked by plenty of consumer access points,
    so the local subnet's own broadcast address is tried as well.
    """
    addresses = ["255.255.255.255"]
    if hint:
        try:
            network = ipaddress.ip_network(f"{hint}/24", strict=False)
            addresses.append(str(network.broadcast_address))
        except ValueError:
            pass
    return list(dict.fromkeys(addresses))


def local_ip_hint(destination: str = "8.8.8.8") -> str | None:
    """The address this host would use to reach the wider network."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((destination, 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def discover(
    *,
    timeout: float = 5.0,
    targets: list[str] | None = None,
    port: int = 54321,
    use_broadcast: bool = True,
) -> list[Discovered]:
    """Send the handshake and collect whatever answers.

    `targets` unicasts to specific addresses as well -- useful where the access
    point filters broadcast traffic, which is common on guest and IoT networks.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.3)

    found: dict[str, Discovered] = {}
    try:
        destinations: list[str] = []
        if use_broadcast:
            destinations += broadcast_addresses(local_ip_hint())
        destinations += targets or []

        for destination in destinations:
            try:
                sock.sendto(HANDSHAKE, (destination, port))
            except OSError as exc:
                _LOG.debug("could not send to %s: %s", destination, exc)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            device = parse_discovery_reply(data, addr[0])
            if device is not None and addr[0] not in found:
                found[addr[0]] = device
                _LOG.debug("found %s", device)
    finally:
        sock.close()

    return sorted(found.values(), key=lambda d: tuple(map(int, d.ip.split("."))))


def subnet_targets(cidr: str, *, limit: int = 512) -> list[str]:
    """Every usable host address in a subnet, for a unicast sweep."""
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(h) for h in network.hosts()]
    if len(hosts) > limit:
        raise ValueError(
            f"{cidr} has {len(hosts)} hosts; narrow it to at most {limit} "
            "(a /24 or smaller)"
        )
    return hosts
