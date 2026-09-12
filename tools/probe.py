#!/usr/bin/env python3
"""Phase 0 probe -- answers the questions the design depends on.

Read-only by default. Nothing here writes a setting or moves the robot unless
you pass --remote-test, which drives it briefly in open floor.

    python3 tools/probe.py
    python3 tools/probe.py --remote-test      # needs clear space around it

Output is written to fixtures/probe-<timestamp>.json. That file identifies your
device (mac, did, owner id, serial) -- fixtures/ is gitignored, keep it so.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import spec  # noqa: E402
from hub.config import ConfigError, load  # noqa: E402
from hub.device import Vacuum  # noqa: E402
from hub.telemetry import PositionParseError, parse_position  # noqa: E402
from hub.transport import Transport  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

KNOWN_PROPS = [
    v for v in vars(spec).values() if isinstance(v, spec.Prop)
]


def heading(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


def probe_known(t: Transport, report: dict) -> None:
    heading("1. Known properties")
    values: dict[str, object] = {}
    for prop in sorted(KNOWN_PROPS, key=lambda p: (p.siid, p.piid)):
        ok, value = t.try_get(prop)
        key = f"{prop.siid}/{prop.piid}"
        if ok:
            values[key] = {"name": prop.name, "value": value}
            shown = repr(value)
            if len(shown) > 90:
                shown = shown[:87] + "..."
            print(f"  {key:>7}  {prop.name:<26} {shown}")
        else:
            print(f"  {key:>7}  {prop.name:<26} -- unavailable")
    report["known_properties"] = values


def _wifi_sn_candidates(values: dict[str, object]) -> list[tuple[str, str]]:
    """Fields shaped like a Xiaomi wifi_sn.

    The S20 family's looks like `54785/DUAA8F4WB06957` -- around 20 characters,
    alphanumeric apart from a slash. Length and `isalnum()` checks that do not
    allow for the slash are the classic reason map decryption fails.
    """
    candidates: list[tuple[str, str]] = []
    for key, value in values.items():
        text = str(value)
        for field in text.replace('"', "").replace("{", "").replace("}", "").split(","):
            field = field.strip()
            cleaned = field.replace("/", "")
            if (
                10 <= len(field) <= 25
                and cleaned.isalnum()
                and not field.isdigit()
                and not cleaned.isalpha()
            ):
                candidates.append((key, field))
    return candidates


def hunt_wifi_sn(t: Transport, vac: Vacuum, report: dict) -> None:
    """The map decryption key needs wifi_sn, and its home on this model is unknown.

    The reference ijai implementation reads 7/p45, which is not in the published
    b108gl spec. So sweep the undocumented range and look at get-system-info.
    """
    heading("2. Hunting for wifi_sn (needed to decrypt maps)")
    found: dict[str, object] = {}

    print("  Sweeping undocumented property ranges...")
    for siid, piid_range in ((7, range(10, 60)), (6, range(14, 40))):
        for piid in piid_range:
            ok, value = t.try_get(spec.Prop(siid, piid, f"probe-{siid}-{piid}"))
            if ok and value not in (None, "", 0):
                key = f"{siid}/{piid}"
                found[key] = value
                shown = repr(value)
                print(f"    {key:>7}  {shown[:110]}")

    print("\n  get-system-info (6/A16):")
    try:
        info = vac.system_info()
        print(f"    {json.dumps(info, ensure_ascii=False)[:600]}")
        found["action:6/16"] = info
    except Exception as exc:  # noqa: BLE001
        print(f"    failed: {exc}")

    report["undocumented"] = found

    # Scan the known properties too -- on this model the serial number at 1/5
    # carries the value, and looking only at undocumented addresses misses it.
    searchable = dict(found)
    for key, entry in (report.get("known_properties") or {}).items():
        if isinstance(entry, dict) and entry.get("value") not in (None, "", 0):
            searchable[f"{key} ({entry['name']})"] = entry["value"]

    candidates = _wifi_sn_candidates(searchable)
    if candidates:
        print("\n  Plausible wifi_sn values (the S20 format contains a '/'):")
        for key, value in candidates[:15]:
            marker = "  <-- most likely" if "/" in value else ""
            print(f"    {key:>22}  {value}{marker}")
    else:
        print("\n  No obvious candidate found -- send the full output over.")
    report["wifi_sn_candidates"] = candidates


def probe_position(t: Transport, report: dict, seconds: float = 30.0) -> None:
    heading("3. Position telemetry -- rate, format and jitter")
    samples: list[tuple[float, object]] = []
    started = time.monotonic()
    print(f"  Polling {spec.VACUUM_POSITION} as fast as allowed for {seconds:.0f}s...")
    while time.monotonic() - started < seconds:
        ok, value = t.try_get(spec.VACUUM_POSITION)
        samples.append((time.monotonic(), value if ok else None))

    good = [(at, v) for at, v in samples if v is not None]
    print(f"  {len(samples)} reads, {len(good)} successful")
    if not good:
        print("  No position data at all -- closed-loop control is not possible.")
        report["position"] = {"reads": len(samples), "ok": 0}
        return

    gaps = [b[0] - a[0] for a, b in zip(good, good[1:])]
    distinct = []
    for _, value in good:
        if not distinct or value != distinct[-1]:
            distinct.append(value)

    print(f"  Raw sample:      {good[0][1]!r}")
    print(f"  Mean interval:   {statistics.mean(gaps) * 1000:.0f} ms")
    print(f"  Median interval: {statistics.median(gaps) * 1000:.0f} ms")
    print(f"  Slowest:         {max(gaps) * 1000:.0f} ms")
    print(f"  Distinct values: {len(distinct)} of {len(good)} reads")
    if len(distinct) < 3:
        print("  NOTE: the value barely changed. Either the robot is parked,")
        print("        or this property only updates while it is moving.")

    try:
        pose = parse_position(good[0][1])
        print(f"  Parsed as:       x={pose.x:.3f} y={pose.y:.3f} heading={pose.heading:.1f}deg")
        print("  Sanity-check those units against where the robot physically is.")
    except PositionParseError as exc:
        print(f"  COULD NOT PARSE: {exc}")

    report["position"] = {
        "reads": len(samples),
        "ok": len(good),
        "raw_samples": [v for _, v in good[:40]],
        "mean_interval_ms": statistics.mean(gaps) * 1000,
        "median_interval_ms": statistics.median(gaps) * 1000,
        "max_interval_ms": max(gaps) * 1000,
        "distinct_values": len(distinct),
    }


def probe_remote(vac: Vacuum, report: dict, hold: float = 2.0) -> None:
    """Characterise remote control: continuous drive, or a single nudge?

    This is the question that decides how much momentum we can build, and so
    how well the crossing maneuver can work.
    """
    heading("4. Remote control semantics")
    print("  The robot will move. Make sure it has ~1.5 m of clear floor ahead.")
    if input("  Type 'go' to continue: ").strip().lower() != "go":
        print("  Skipped.")
        return

    result: dict[str, object] = {}
    try:
        started = time.monotonic()
        vac.enter_remote()
        time.sleep(0.5)
        status = vac.status_name()
        result["status_after_enter"] = status
        print(f"  Status after enter: {status} (expected 'remote')")

        print(f"  Single forward command, then watching for {hold:.0f}s...")
        positions = []
        vac.remote_forward()
        command_at = time.monotonic()
        result["command_latency_ms"] = (command_at - started) * 1000
        while time.monotonic() - command_at < hold:
            positions.append((time.monotonic() - command_at, vac.position_raw()))
            time.sleep(0.25)

        vac.remote_halt()
        moved = len({p for _, p in positions}) > 1
        still_moving = len({p for _, p in positions[-3:]}) > 1
        print(f"  Moved at all:            {moved}")
        print(f"  Still moving after {hold:.0f}s: {still_moving}")
        print(
            "  => CONTINUOUS drive (momentum is available)"
            if still_moving
            else "  => single NUDGE per command (momentum will be limited)"
        )
        result.update(
            moved=moved, continuous=still_moving, samples=[p for _, p in positions]
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {exc}")
        result["error"] = str(exc)
    finally:
        try:
            vac.remote_halt()
            vac.exit_remote()
            print("  Remote mode released.")
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not exit remote mode: {exc}")

    report["remote"] = result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip")
    parser.add_argument("--token")
    parser.add_argument(
        "--remote-test",
        action="store_true",
        help="also drive the robot briefly to characterise remote control",
    )
    parser.add_argument("--position-seconds", type=float, default=30.0)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load(args.ip, args.token)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    print(f"Connecting to {cfg.ip} (token {cfg.redacted_token()})")
    transport = Transport(
        cfg.ip, cfg.token, timeout=cfg.timeout, min_interval=cfg.min_interval
    )
    vac = Vacuum(transport)

    try:
        model = transport.get(spec.DEVICE_MODEL)
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not reach the robot: {exc}", file=sys.stderr)
        print(
            "\nChecks:\n"
            "  - is the robot on the same subnet as this machine?\n"
            "  - is the token current? re-pairing the robot changes it\n"
            "  - is UDP 54321 reachable (no NAT between you and it)?",
            file=sys.stderr,
        )
        return 1

    print(f"Connected. Model reports: {model}")
    if model != spec.MODEL:
        print(f"NOTE: expected {spec.MODEL}; addresses in hub/spec.py may differ.")

    report: dict[str, object] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ip": cfg.ip,
        "model": model,
    }

    probe_known(transport, report)
    hunt_wifi_sn(transport, vac, report)  # depends on probe_known having run
    probe_position(transport, report, args.position_seconds)
    if args.remote_test:
        probe_remote(vac, report)

    FIXTURES.mkdir(exist_ok=True)
    out = FIXTURES / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    heading(f"Saved to {out.relative_to(ROOT)}")
    print("That file contains device identifiers -- fixtures/ is gitignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
