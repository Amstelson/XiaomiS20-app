#!/usr/bin/env python3
"""Run the crossing maneuver against the simulator -- no robot needed.

Useful for sanity-checking parameter changes before taking them to hardware,
and for seeing what the calibration sweep will look like.

    python3 tools/simulate.py
    python3 tools/simulate.py --min-speed 0.28 --bad-spot 0.0 0.12 0.3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.crossing import CrossingEngine, CrossingParams  # noqa: E402
from hub.simulator import SimRobot, SimVacuum, VirtualClock, make_doorway  # noqa: E402
from hub.telemetry import PoseReader, PositionFormat  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-speed", type=float, default=0.25,
                        help="speed needed at the lip, m/s")
    parser.add_argument("--bad-spot", type=float, nargs=3, action="append",
                        metavar=("LATERAL", "HALFWIDTH", "PENALTY"),
                        help="a higher patch on the threshold")
    parser.add_argument("--sweep", type=float, nargs="+",
                        default=[0.15, 0.25, 0.35, 0.45, 0.55, 0.70, 0.85])
    parser.add_argument("--lateral", type=float, default=0.0)
    args = parser.parse_args()

    gate, model = make_doorway(
        min_speed=args.min_speed,
        bad_spots=[tuple(s) for s in (args.bad_spot or [])],
    )
    print(f"Simulated doorway: {gate.length:.2f} m wide")
    print(f"Needs {model.min_speed:.2f} m/s at the lip")
    for centre, half, penalty in model.bad_spots:
        print(f"  high patch at {centre:+.2f} m (+/-{half:.2f} m), +{penalty:.2f} m/s")
    print()

    results = []
    for runup in sorted(args.sweep):
        robot = SimRobot(threshold=model, x=-0.30, y=args.lateral, heading=0.0)
        vac = SimVacuum(robot)
        clock = VirtualClock(robot)
        engine = CrossingEngine(
            vac,
            gate,
            PoseReader(vac.position_raw, PositionFormat(1.0, True)),
            CrossingParams(poll_interval=0.05, command_interval=0.1),
            clock=clock.now,
            sleep=clock.sleep,
        )
        result = engine.attempt(args.lateral, runup=runup)
        results.append(result)
        print(f"  {result.summary()}")

    crossed = [r for r in results if r.outcome.is_success]
    print()
    if crossed:
        print(f"Shortest run-up that works: {min(r.runup for r in crossed):.2f} m")
    else:
        print("Nothing crossed at any run-up in the sweep.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
