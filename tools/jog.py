#!/usr/bin/env python3
"""Manual remote control -- drive the robot from the keyboard.

This is the first thing to run at the problem threshold. It answers, by hand
and in five minutes, what the autonomous maneuver would take days to discover:

  * does remote control cross the threshold where the cleaning planner won't?
  * how much run-up does it actually need?
  * which way round do x, y and heading run in this robot's frame?

    python3 tools/jog.py

Keys
    w / s     forward / reverse        a / d   turn left / right
    space     stop                     q       stop, exit remote mode, quit
    m         mark the current position (use at each end of the threshold)
    g         emit a gate definition from the last two marks
    r         reset the trip odometer
    t         toggle between the raw position string and the odometer

Marking both ends of the doorway and pressing `g` prints a ready-made gate
block for gates.toml, so you never have to read coordinates off a map by hand.

Every raw position sample is logged to fixtures/jog-<timestamp>.jsonl along
with the command in effect. While the wire format is still unconfirmed that
recording is the ground truth: drive a known distance, turn a known angle, and
the log says which field is which.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.config import ConfigError, load  # noqa: E402
from hub.device import Vacuum  # noqa: E402
from hub.geometry import Point  # noqa: E402
from hub.telemetry import PoseReader  # noqa: E402
from hub.transport import Transport  # noqa: E402

HELP = (
    "w/s drive  a/d turn  space stop  m mark  g gate  r reset  t raw/odo  q quit"
)


class RawKeyboard:
    """Read single keypresses without waiting for Enter."""

    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def key(self, timeout: float = 0.15) -> str | None:
        import select

        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.read(1) if ready else None


def emit_gate(marks: list[Point], here: Point) -> None:
    if len(marks) < 2:
        print("\r  Need two marks: drive to each end of the threshold and press m.")
        return
    a, b = marks[-2], marks[-1]
    print("\r\n  Paste into gates.toml:\r")
    print("\r  [[gate]]\r")
    print('  id = "hall"\r')
    print('  name = "Living room -> hall"\r')
    print(f"  a = [{a.x:.3f}, {a.y:.3f}]\r")
    print(f"  b = [{b.x:.3f}, {b.y:.3f}]\r")
    print(f"  # a point on the side you cross FROM (robot is here now):\r")
    print(f"  from_side = [{here.x:.3f}, {here.y:.3f}]\r")
    print("  approach_distance = 0.45\r")
    print("  clearance = 0.20\r\n")
    print(f"  (threshold width {a.distance_to(b):.2f} m)\r\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip")
    parser.add_argument("--token")
    parser.add_argument(
        "--hold",
        type=float,
        default=0.4,
        help="seconds between re-issuing a held direction command",
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="do not log raw position samples to fixtures/",
    )
    args = parser.parse_args()

    try:
        cfg = load(args.ip, args.token)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    transport = Transport(
        cfg.ip, cfg.token, timeout=cfg.timeout, min_interval=cfg.min_interval
    )
    vac = Vacuum(transport)
    reader = PoseReader(vac.position_raw)

    print(f"Connecting to {cfg.ip}...")
    print(f"Status: {vac.status_name()}  battery: {vac.battery()}%")
    print("\nEntering remote mode. The robot will respond to keys immediately.")
    print(HELP + "\n")

    if not vac.enter_remote_confirmed():
        # Not an error. Docked robots keep reporting `charged` until they are
        # actually asked to move, and the robot announces "remote control
        # start" regardless -- so carry on and let the keys decide.
        print(
            f"Robot still reports '{vac.status_name()}' rather than 'remote'.\n"
            "That is normal on the dock. Press w and watch whether it moves;\n"
            "the status line below shows what it reports."
        )

    record = None
    if not args.no_record:
        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        fixtures.mkdir(exist_ok=True)
        record_path = fixtures / f"jog-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        record = record_path.open("w")
        print(f"Recording raw samples to {record_path.name}\n")

    marks: list[Point] = []
    command = "halt"
    last_sent = 0.0
    origin: Point | None = None
    trip = 0.0
    previous: Point | None = None
    status = "?"
    status_checked = 0.0
    show_raw = True
    started_at = time.monotonic()

    actions = {
        "w": ("forward", vac.remote_forward),
        "s": ("back", vac.remote_back),
        "a": ("left", vac.remote_left),
        "d": ("right", vac.remote_right),
    }

    try:
        with RawKeyboard() as keyboard:
            while True:
                key = keyboard.key()

                if key == "q":
                    break
                if key == " ":
                    command = "halt"
                    vac.remote_halt()
                elif key in actions:
                    command, send = actions[key]
                    send()
                    last_sent = time.monotonic()
                elif key == "m":
                    sample = reader.read()
                    if sample:
                        marks.append(sample.pose.point)
                        print(
                            f"\r  marked #{len(marks)}: "
                            f"({sample.pose.x:.3f}, {sample.pose.y:.3f})"
                            f"  raw={sample.raw}\r"
                        )
                elif key == "g":
                    sample = reader.read()
                    if sample:
                        emit_gate(marks, sample.pose.point)
                elif key == "r":
                    origin, trip, previous = None, 0.0, None
                elif key == "t":
                    show_raw = not show_raw

                # Hold the command alive; if the firmware drives continuously
                # this is harmless, and if it nudges this keeps it moving.
                now = time.monotonic()
                if command != "halt" and now - last_sent >= args.hold:
                    actions_by_name = {v[0]: v[1] for v in actions.values()}
                    actions_by_name[command]()
                    last_sent = now

                if now - status_checked > 2.0:
                    try:
                        status = vac.status_name()
                    except Exception:  # noqa: BLE001
                        status = "?"
                    status_checked = now

                sample = reader.read()
                if sample:
                    point = sample.pose.point
                    if origin is None:
                        origin = point
                    if previous is not None:
                        trip += previous.distance_to(point)
                    previous = point
                    displacement = origin.distance_to(point)
                    # The raw string matters while the wire format is still
                    # being pinned down -- it is the ground truth, and the
                    # parsed values are only an interpretation of it.
                    if record is not None:
                        record.write(
                            json.dumps(
                                {
                                    "t": round(time.monotonic() - started_at, 3),
                                    "cmd": command,
                                    "raw": sample.raw,
                                }
                            )
                            + "\n"
                        )
                    tail = f"raw={sample.raw}" if show_raw else (
                        f"net={displacement:5.2f} path={trip:5.2f}"
                    )
                    print(
                        f"\r  {command:<7} [{status:<8}] "
                        f"x={sample.pose.x:+7.3f} y={sample.pose.y:+7.3f} "
                        f"hdg={sample.pose.heading:+7.1f}  "
                        f"marks={len(marks)}  {tail}          ",
                        end="",
                        flush=True,
                    )
                else:
                    print("\r  (no position data)          ", end="", flush=True)
    finally:
        if record is not None:
            record.close()
            print(f"\n\nRaw samples written to {record_path}")
        print("\nReleasing remote mode...")
        try:
            vac.remote_halt()
            time.sleep(0.2)
            vac.exit_remote()
            print(f"Done. Status: {vac.status_name()}")
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: could not release remote mode cleanly: {exc}")
            print("Check the robot, and use the Xiaomi app to stop it if needed.")

    if marks:
        print(f"\n{len(marks)} marks recorded:")
        for i, mark in enumerate(marks, 1):
            print(f"  {i}: ({mark.x:.3f}, {mark.y:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
