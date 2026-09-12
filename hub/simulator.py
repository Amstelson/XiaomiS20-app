"""A small differential-drive simulator with a modelled threshold.

Exists so the crossing maneuver can be developed and regression-tested without
the robot -- hundreds of runs per second, deterministically, instead of one run
per trip to the doorway.

The threshold model is built around the observed behaviour: the ramp is
crossable, but only with enough speed at the lip. Speed builds while the
forward command is held, so **run-up distance determines whether the crossing
succeeds** -- which is exactly the relationship the calibration sweep is trying
to find on the real robot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import Gate, Point, normalize_deg
from .spec import Status


@dataclass
class ThresholdModel:
    """What it takes to get over the lip."""

    gate: Gate
    #: Speed needed at the lip to carry over, m/s.
    min_speed: float = 0.18
    #: Beyond this heading error the robot refuses to commit at all, degrees.
    max_heading_error: float = 25.0
    #: Half-width of the lip, metres. Inside this band the robot is on the ramp.
    band: float = 0.06
    #: How far short the robot stops when it declines to commit, metres.
    standoff: float = 0.09
    #: Spots where the lip is higher; (lateral centre, half width, speed penalty).
    bad_spots: list[tuple[float, float, float]] = field(default_factory=list)

    def required_speed(self, lateral: float) -> float:
        need = self.min_speed
        for centre, half_width, penalty in self.bad_spots:
            if abs(lateral - centre) <= half_width:
                need += penalty
        return need


@dataclass
class SimRobot:
    """Kinematics plus the threshold interaction."""

    threshold: ThresholdModel
    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0
    #: m/s at full command, and how fast it gets there. The acceleration is
    #: deliberately gentle, like the real robot: it means speed at the lip is
    #: still a function of run-up distance across the range we care about
    #: (roughly 0.1-0.8 m), which is the whole point of the calibration sweep.
    max_speed: float = 0.32
    acceleration: float = 0.12
    deceleration: float = 0.9
    turn_rate: float = 60.0  # deg/s

    speed: float = 0.0
    command: str = "halt"
    stuck: bool = False
    time: float = 0.0
    #: Set when the robot refuses to commit to the lip.
    refused: bool = False

    def _signed(self) -> float:
        return self.threshold.gate.signed_distance(Point(self.x, self.y))

    def _lateral(self) -> float:
        return self.threshold.gate.lateral_offset(Point(self.x, self.y))

    def tick(self, dt: float) -> None:
        self.time += dt

        if self.stuck:
            # High-centred: wheels turn, the robot does not move -- unless we
            # are reversing, which is how it gets free.
            if self.command == "back":
                self.stuck = False
            else:
                self.speed = 0.0
                return

        if self.command in ("forward", "back"):
            self.speed = min(self.max_speed, self.speed + self.acceleration * dt)
        elif self.command in ("left", "right"):
            self.heading = normalize_deg(
                self.heading
                + self.turn_rate * dt * (1 if self.command == "left" else -1)
            )
            self.speed = 0.0
            return
        else:
            self.speed = max(0.0, self.speed - self.deceleration * dt)

        if self.speed <= 0.0:
            return

        direction = 1.0 if self.command == "forward" else -1.0
        before = self._signed()
        rad = math.radians(self.heading)
        step = self.speed * dt * direction
        nx = self.x + math.cos(rad) * step
        ny = self.y + math.sin(rad) * step
        after = self.threshold.gate.signed_distance(Point(nx, ny))

        # Approaching the lip from the near side?
        if direction > 0 and before < 0 <= after + self.threshold.band:
            gate = self.threshold.gate
            heading_error = abs(gate.heading_error(self._pose_for_check()))
            if heading_error > self.threshold.max_heading_error:
                # Never commits: stops short of the lip, at bumper standoff.
                self.refused = True
                self.speed = 0.0
                self.x, self.y = self._point_short_of_lip()
                return
            if self.speed < self.threshold.required_speed(self._lateral()):
                # Rides up and strands on the crest.
                self.x, self.y = self._point_on_lip()
                self.stuck = True
                self.speed = 0.0
                return

        self.x, self.y = nx, ny

    def _pose_for_check(self):
        from .geometry import Pose

        return Pose(self.x, self.y, self.heading)

    def _point_short_of_lip(self) -> tuple[float, float]:
        gate = self.threshold.gate
        on_line = gate.point_at(self._lateral())
        backed = on_line + gate.normal.scaled(-self.threshold.standoff)
        return backed.x, backed.y

    def _point_on_lip(self) -> tuple[float, float]:
        gate = self.threshold.gate
        on_line = gate.point_at(self._lateral())
        nudged = on_line + gate.normal.scaled(0.02)
        return nudged.x, nudged.y

    # -- the surface the engine drives --------------------------------------

    def position_string(self) -> str:
        return f"{self.x:.4f},{self.y:.4f},{math.radians(self.heading):.4f}"


class SimVacuum:
    """Stands in for `hub.device.Vacuum` against a SimRobot."""

    def __init__(self, robot: SimRobot) -> None:
        self.robot = robot
        self.status_value = Status.IDLE
        self.fault_value = 0
        self.settings = {"suction": 2, "water": 2, "sweep_mop_type": 3}
        self.calls: list[str] = []
        self.resumed = False
        #: Set to make enter_remote fail, to test the refusal path.
        self.allow_remote = True

    def _record(self, name: str) -> None:
        self.calls.append(name)

    # state
    def status(self):
        return self.status_value

    def status_name(self) -> str:
        value = self.status_value
        return value.name.lower() if isinstance(value, Status) else str(value)

    def fault(self) -> int:
        return self.fault_value

    def position_raw(self) -> str:
        return self.robot.position_string()

    # settings
    def snapshot_settings(self):
        from .device import Settings

        return Settings(**self.settings)

    def apply_settings(self, settings) -> list[str]:
        self._record("apply_settings")
        for key in ("suction", "water", "sweep_mop_type"):
            value = getattr(settings, key)
            if value is not None:
                self.settings[key] = value
        return []

    # cleaning
    def pause(self):
        self._record("pause")
        self.status_value = Status.PAUSED

    def resume(self):
        self._record("resume")
        self.resumed = True
        self.status_value = Status.SWEEPING

    # remote
    def enter_remote(self):
        self._record("enter_remote")
        if self.allow_remote:
            self.status_value = Status.REMOTE

    def exit_remote(self):
        self._record("exit_remote")
        self.status_value = Status.IDLE
        self.robot.command = "halt"

    def in_remote_mode(self) -> bool:
        return self.status_value == Status.REMOTE

    def remote_forward(self):
        self.robot.command = "forward"

    def remote_back(self):
        self.robot.command = "back"

    def remote_left(self):
        self.robot.command = "left"

    def remote_right(self):
        self.robot.command = "right"

    def remote_halt(self):
        self.robot.command = "halt"


class VirtualClock:
    """Drives the simulator forward whenever the engine waits or samples."""

    def __init__(self, robot: SimRobot, step: float = 0.05) -> None:
        self.robot = robot
        self.step = step
        self.t = 0.0

    def now(self) -> float:
        # Every observation costs a little time, so stall windows still expire.
        self.advance(self.step)
        return self.t

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 1e-9:
            dt = min(self.step, remaining)
            self.robot.tick(dt)
            self.t += dt
            remaining -= dt


def make_doorway(
    *,
    width: float = 0.85,
    min_speed: float = 0.18,
    bad_spots: list[tuple[float, float, float]] | None = None,
) -> tuple[Gate, ThresholdModel]:
    """The living-room-to-hall doorway: threshold on x=0, crossed toward +x."""
    gate = Gate(
        id="hall",
        name="Living room -> hall",
        a=Point(0.0, -width / 2),
        b=Point(0.0, width / 2),
        from_side=Point(-1.5, 0.0),
        approach_distance=0.45,
        clearance=0.20,
    )
    model = ThresholdModel(
        gate=gate, min_speed=min_speed, bad_spots=bad_spots or []
    )
    return gate, model
