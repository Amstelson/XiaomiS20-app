#!/usr/bin/env python3
"""Run the crossing maneuver at a gate.

    # find the shortest run-up that works -- do this first
    python3 tools/cross.py hall --calibrate

    # then a single crossing with a known-good run-up
    python3 tools/cross.py hall --runup 0.55

    # rehearse without touching the robot
    python3 tools/cross.py hall --runup 0.55 --dry-run

Park the robot near the threshold on the side it struggles from before running
this. The engine deliberately refuses to act on a robot that is not already
staged -- it has no path planning and will not drive across a room.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.config import ConfigError, load  # noqa: E402
from hub.crossing import CrossingEngine, CrossingParams, Outcome  # noqa: E402
from hub.device import Vacuum  # noqa: E402
from hub.gates import GateConfigError, load_gates  # noqa: E402
from hub.spec import Suction, SweepMopType, WaterLevel  # noqa: E402
from hub.telemetry import PoseReader, PositionFormat  # noqa: E402
from hub.transport import Transport  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

DEFAULT_SWEEP = [0.25, 0.35, 0.45, 0.55, 0.70, 0.85]


def confirm(message: str) -> bool:
    return input(f"{message} [y/N] ").strip().lower() in ("y", "yes")


def save(results: list, gate_id: str) -> Path:
    FIXTURES.mkdir(exist_ok=True)
    path = FIXTURES / f"crossing-{gate_id}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(
        json.dumps(
            [
                {
                    "outcome": r.outcome.value,
                    "runup": r.runup,
                    "lateral": r.lateral,
                    "obliquity": r.obliquity,
                    "reached": r.progress,
                    "duration": r.duration,
                    "fault": r.fault,
                    "notes": r.notes,
                    "restore_problems": r.restore_problems,
                    "trace": [
                        {
                            "t": round(s.at, 3),
                            "signed": round(s.signed, 4),
                            "lateral": round(s.lateral, 4),
                            "heading_error": round(s.heading_error, 2),
                        }
                        for s in r.trace
                    ],
                }
                for r in results
            ],
            indent=2,
        )
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gate", help="gate id from gates.toml")
    parser.add_argument("--ip")
    parser.add_argument("--token")
    parser.add_argument("--runup", type=float, help="run-up distance in metres")
    parser.add_argument("--lateral", type=float, default=0.0,
                        help="crossing spot, metres from the middle of the threshold")
    parser.add_argument("--obliquity", type=float, default=0.0,
                        help="degrees off perpendicular")
    parser.add_argument("--calibrate", action="store_true",
                        help="sweep run-up distances to find the shortest that works")
    parser.add_argument("--sweep", type=float, nargs="+", default=DEFAULT_SWEEP)
    parser.add_argument("--keep-water", action="store_true",
                        help="do not turn the mop water off for the crossing")
    parser.add_argument("--sweep-only", action="store_true",
                        help="switch to sweep-only mode (may lift the mop assembly)")
    parser.add_argument("--no-reissue", action="store_true",
                        help="send each direction command once (if drive is continuous)")
    parser.add_argument("--no-resume", action="store_true",
                        help="do not resume the interrupted clean afterwards")
    parser.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        cfg = load(args.ip, args.token)
        gates = load_gates()
    except (ConfigError, GateConfigError) as exc:
        print(exc, file=sys.stderr)
        return 2

    gate = gates.get(args.gate)
    if gate is None:
        print(
            f"No gate {args.gate!r}. Known: {', '.join(sorted(gates)) or '(none)'}",
            file=sys.stderr,
        )
        return 2

    transport = Transport(
        cfg.ip,
        cfg.token,
        timeout=cfg.timeout,
        min_interval=cfg.min_interval,
        dry_run=args.dry_run,
    )
    vac = Vacuum(transport)
    reader = PoseReader(vac.position_raw, PositionFormat())

    params = CrossingParams(
        runup=args.runup if args.runup is not None else gate.approach_distance,
        obliquity=args.obliquity,
        reissue_forward=not args.no_reissue,
        water=None if args.keep_water else WaterLevel.OFF,
        suction=Suction.FULL_SPEED,
        sweep_mop_type=SweepMopType.SWEEP if args.sweep_only else None,
        resume_after=not args.no_resume,
    )

    engine = CrossingEngine(
        vac, gate, reader, params, confirm=None if args.yes else confirm
    )

    print(f"Gate {gate.id!r}: {gate.name or '(unnamed)'}")
    print(f"  width {gate.length:.2f} m, crossing heading {gate.crossing_heading:+.0f} deg")
    sample = reader.read()
    if sample is None:
        print("\nNo position telemetry. Run tools/probe.py first.", file=sys.stderr)
        return 1
    print(
        f"  robot at ({sample.pose.x:.2f}, {sample.pose.y:.2f}) "
        f"heading {sample.pose.heading:+.0f} deg, "
        f"{gate.signed_distance(sample.pose.point):+.2f} m from the threshold"
    )
    if args.dry_run:
        print("  DRY RUN -- reads happen, nothing is written and nothing moves.")

    if args.dry_run:
        result = engine.attempt(args.lateral)
        print("\nPlan:")
        for note in result.notes:
            print(f"  {note}")
        print("\nNothing was sent. Re-run without --dry-run to do it.")
        return 0

    if args.calibrate:
        print(f"\nCalibrating run-up over {args.sweep} m, shortest first.")
        print("Re-park the robot on the starting side between attempts if it crosses.\n")
        results = engine.calibrate(
            args.sweep, lateral=args.lateral, obliquity=args.obliquity
        )
    else:
        results = [engine.attempt(args.lateral)]
        print(f"\n{results[-1].summary()}")

    print()
    succeeded = [r for r in results if r.outcome is Outcome.CROSSED]
    if succeeded:
        best = min(succeeded, key=lambda r: r.runup)
        print(f"Shortest run-up that crossed: {best.runup:.2f} m")
        print(f"Set approach_distance = {best.runup:.2f} for gate {gate.id!r}.")
    else:
        print("Nothing crossed.")
        modes = {r.outcome.value for r in results}
        if "beached" in modes:
            print("  Beaching: it climbs on but lacks momentum. Try a longer run-up,")
            print("  --sweep-only, and a different --lateral spot.")
        if "refused" in modes:
            print("  Refusing: it never commits. Remote mode may not override the")
            print("  bumper firmware -- check the run-up is clear and square.")
        if "aborted" in modes:
            print("  Aborted: check the notes above, most likely no run-up room.")

    problems = [p for r in results for p in r.restore_problems]
    if problems:
        print("\nWARNING -- problems restoring robot state:")
        for problem in problems:
            print(f"  {problem}")
        print("Check the robot before leaving it unattended.")

    if not args.dry_run:
        print(f"\nSaved to {save(results, gate.id).relative_to(ROOT)}")
    return 0 if succeeded or not args.calibrate else 1


if __name__ == "__main__":
    raise SystemExit(main())
