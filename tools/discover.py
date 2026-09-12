#!/usr/bin/env python3
"""Find the robot on the local network, no Xiaomi account needed.

    python3 tools/discover.py                     # broadcast
    python3 tools/discover.py --scan 192.168.1.0/24   # if broadcast is filtered

Every miIO device answers a fixed handshake packet with its device id, so this
locates the robot and proves it is reachable over UDP 54321 before you have a
token. A few devices also leak their token in the reply; most current firmware
does not, so expect to still need the token from your Mi account.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.discovery import discover, local_ip_hint, subnet_targets  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan",
        metavar="CIDR",
        help="also unicast every host in this subnet, e.g. 192.168.1.0/24",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--no-broadcast", action="store_true")
    args = parser.parse_args()

    local = local_ip_hint()
    print(f"This host is {local or 'unknown'}")

    targets = None
    if args.scan:
        try:
            targets = subnet_targets(args.scan)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"Sweeping {len(targets)} addresses in {args.scan}...")
    else:
        print("Broadcasting the miIO handshake...")

    devices = discover(
        timeout=args.timeout, targets=targets, use_broadcast=not args.no_broadcast
    )

    if not devices:
        print("\nNothing answered.")
        print(
            "\nIf the robot is definitely on this network, broadcast is probably\n"
            "being filtered -- common on guest and IoT VLANs. Find its IP in your\n"
            "router's DHCP leases and sweep that subnet directly:\n"
            f"    python3 tools/discover.py --scan {local.rsplit('.', 1)[0]}.0/24"
            if local
            else "    python3 tools/discover.py --scan 192.168.1.0/24"
        )
        return 1

    print(f"\nFound {len(devices)} device(s):\n")
    for device in devices:
        print(f"  {device.describe()}")

    leaked = [d for d in devices if d.token]
    print()
    if leaked:
        print("A token was exposed in the handshake -- put it straight into")
        print("config.toml along with the matching IP.")
    else:
        print("No tokens exposed, which is normal on current firmware.")
        print("Use the IP above with a token from your Mi account.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
