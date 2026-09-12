#!/usr/bin/env python3
"""Check whether this machine is a good place to drive the robot from.

Answers the practical question -- laptop, LXC, VM, Pi? -- by measuring rather
than guessing. Run it from any candidate host:

    python3 tools/doctor.py

It verifies the environment, proves the token, and measures round-trip latency,
which is what decides whether closed-loop control is viable from here.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_ICON = {PASS: "  ok  ", WARN: " warn ", FAIL: " FAIL "}

results: list[tuple[str, str, str]] = []


def check(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"[{_ICON[status]}] {name}")
    for line in detail.splitlines():
        if line.strip():
            print(f"          {line}")


def outbound_ip_for(destination: str) -> str | None:
    """Which local address the OS would use to reach the robot."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((destination, 54321))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip")
    parser.add_argument("--token")
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()

    print("Checking this host as a place to run the robot tooling.\n")

    # 1. Python -------------------------------------------------------------
    version = sys.version_info
    if version >= (3, 11):
        check("Python >= 3.11", PASS, f"running {sys.version.split()[0]}")
    else:
        check(
            "Python >= 3.11",
            FAIL,
            f"running {sys.version.split()[0]}; python-miio requires 3.11+",
        )
        return 1

    # 2. Dependencies -------------------------------------------------------
    try:
        import miio  # noqa: F401

        check("python-miio installed", PASS, f"version {miio.__version__}")
    except ImportError:
        check(
            "python-miio installed",
            FAIL,
            "pip install -r requirements.txt\n"
            "If a dependency fails to build, say so -- the miIO packet protocol\n"
            "is small enough to implement directly and drop the dependency.",
        )
        return 1

    # 3. Config -------------------------------------------------------------
    from hub.config import ConfigError, load

    try:
        cfg = load(args.ip, args.token)
        check("config", PASS, f"robot {cfg.ip}, token {cfg.redacted_token()}")
    except ConfigError as exc:
        check("config", FAIL, str(exc))
        return 2

    # 4. Networking ---------------------------------------------------------
    local = outbound_ip_for(cfg.ip)
    if local is None:
        check("route to the robot", FAIL, f"no route to {cfg.ip}")
        return 1

    try:
        same_24 = ipaddress.ip_network(
            f"{local}/24", strict=False
        ) == ipaddress.ip_network(f"{cfg.ip}/24", strict=False)
    except ValueError:
        same_24 = False

    if same_24:
        check(
            "same subnet as the robot",
            PASS,
            f"this host is {local}, robot is {cfg.ip}",
        )
    else:
        check(
            "same subnet as the robot",
            WARN,
            f"this host is {local}, robot is {cfg.ip} -- different /24.\n"
            "Unicast may still work with a pinned IP, but discovery will not,\n"
            "and NAT between the two will break the miIO handshake.\n"
            "On Proxmox, give the container a bridged NIC on the robot's VLAN.",
        )

    # 5. Handshake ----------------------------------------------------------
    from miio import MiotDevice

    device = MiotDevice(cfg.ip, cfg.token, lazy_discover=False, timeout=cfg.timeout)
    try:
        device.send_handshake()
        check("miIO handshake (UDP 54321)", PASS, "device answered and clock synced")
    except Exception as exc:  # noqa: BLE001
        check(
            "miIO handshake (UDP 54321)",
            FAIL,
            f"{exc}\n"
            "The robot is not answering on UDP 54321 from here.\n"
            "Check: correct IP, robot awake, no NAT or firewall in between.",
        )
        return 1

    # 6. Token --------------------------------------------------------------
    from hub import spec
    from hub.device import Vacuum
    from hub.transport import Transport

    transport = Transport(
        cfg.ip, cfg.token, timeout=cfg.timeout, min_interval=cfg.min_interval
    )
    vac = Vacuum(transport)
    try:
        model = transport.get(spec.DEVICE_MODEL)
        status = vac.status_name()
        battery = vac.battery()
        check(
            "token accepted",
            PASS,
            f"model {model}, status {status}, battery {battery}%",
        )
        if model != spec.MODEL:
            check(
                "model matches hub/spec.py",
                WARN,
                f"expected {spec.MODEL}, got {model} -- addresses may differ",
            )
    except Exception as exc:  # noqa: BLE001
        check(
            "token accepted",
            FAIL,
            f"{exc}\nHandshake worked, so the IP is right but the token is wrong.\n"
            "Re-extract it -- re-pairing the robot to Wi-Fi changes the token.",
        )
        return 1

    # 7. Latency ------------------------------------------------------------
    # This is the number that decides whether closed-loop control works here.
    timings = []
    for _ in range(args.samples):
        started = time.monotonic()
        try:
            transport.get(spec.STATUS)
        except Exception:  # noqa: BLE001
            continue
        timings.append((time.monotonic() - started) * 1000)

    if not timings:
        check("round-trip latency", FAIL, "no successful reads")
        return 1

    median = statistics.median(timings)
    worst = max(timings)
    detail = (
        f"median {median:.0f} ms, worst {worst:.0f} ms, "
        f"over {len(timings)} reads\n"
        f"(includes the {cfg.min_interval * 1000:.0f} ms client-side rate limit)"
    )
    if median < 150 and worst < 600:
        check("round-trip latency", PASS, detail + "\nPlenty fast for the control loop.")
    elif median < 400:
        check(
            "round-trip latency",
            WARN,
            detail + "\nWorkable, but raise poll_interval if maneuvers feel sluggish.",
        )
    else:
        check(
            "round-trip latency",
            FAIL,
            detail + "\nToo slow for closed-loop control. Try a host closer to the\n"
            "robot on the network -- wired, same subnet, no wireless bridge.",
        )

    # -- verdict ------------------------------------------------------------
    failed = [n for n, s, _ in results if s == FAIL]
    warned = [n for n, s, _ in results if s == WARN]
    print()
    if failed:
        print(f"NOT USABLE from here. Failed: {', '.join(failed)}")
        return 1
    if warned:
        print(f"Usable from here, with caveats: {', '.join(warned)}")
        return 0
    print("Good host. Everything checks out -- go ahead and run tools/jog.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
