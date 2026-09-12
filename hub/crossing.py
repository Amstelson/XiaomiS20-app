"""The threshold crossing maneuver.

Background
----------
This robot used to cross the problem threshold by itself: it would back off,
build speed, and carry over the ramp on momentum. After furniture was built
around the opening it stopped choosing that maneuver -- it now creeps at the
lip and strands halfway across.

So the job here is not to invent a capability. It is to *execute the maneuver
the robot has stopped choosing*, using remote-control mode, which takes the
navigation planner's caution out of the loop.

We have no throttle. The only lever on momentum is **how much run-up distance**
the robot gets before it meets the lip, plus keeping the forward command stream
unbroken so it never coasts. Run-up distance is therefore the parameter worth
calibrating, and `calibrate()` sweeps it.

Safety
------
Three invariants, in priority order:

1. The robot is never left in remote mode. Restore runs in a `finally`, and
   again on any unexpected exception.
2. Beaching is never pushed through. High-centred, driving on just spins the
   wheels; detection triggers an immediate reverse escape.
3. Takeover only happens next to the gate. There is no path planning here, so
   the engine refuses to drive a robot that is not already staged near the
   threshold rather than blunder across a room full of new furniture.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterator

from .device import Settings, Vacuum
from .geometry import Gate, Pose
from .spec import Status, Suction, SweepMopType, WaterLevel
from .telemetry import PoseReader

_LOG = logging.getLogger(__name__)


class Outcome(str, Enum):
    CROSSED = "crossed"
    BEACHED = "beached"
    REFUSED = "refused"
    FAULTED = "faulted"
    TIMEOUT = "timeout"
    NOT_STAGED = "not-staged"
    ABORTED = "aborted"
    DRY_RUN = "dry-run"

    @property
    def is_success(self) -> bool:
        return self is Outcome.CROSSED


@dataclass
class CrossingParams:
    """Tunables for one crossing attempt."""

    #: Metres of run-up before the lip. The main lever on momentum.
    runup: float = 0.45
    #: Degrees off perpendicular. 0 is square; the ramp profile favours square.
    obliquity: float = 0.0

    #: How often to re-issue the forward command during the run.
    command_interval: float = 0.5
    #: Set False if the robot drives continuously from a single command.
    reissue_forward: bool = True
    #: How often to sample the pose.
    poll_interval: float = 0.25

    #: Heading tolerance for the run-up, in degrees.
    align_tolerance: float = 5.0
    #: Whether `remote_left` turns counter-clockwise in the world frame.
    #: Confirm with tools/jog.py before trusting an autonomous run.
    left_is_ccw: bool = True

    #: Per-phase time limits, seconds.
    stage_timeout: float = 20.0
    align_timeout: float = 20.0
    drive_timeout: float = 12.0
    escape_timeout: float = 12.0
    #: Hard ceiling on the whole attempt.
    total_budget: float = 90.0

    #: Progress smaller than this over `stall_window` counts as stalled.
    stall_distance: float = 0.03
    stall_window: float = 1.5
    #: Signed distance at or beyond which the robot counts as having climbed
    #: onto the lip. Reaching this and then stalling is beaching; stalling
    #: without ever reaching it is a refusal to commit.
    lip_reach: float = -0.04
    #: How far back to retreat after a beach.
    escape_distance: float = 0.25

    #: Settings applied for the crossing. None leaves the current value alone.
    water: WaterLevel | None = WaterLevel.OFF
    suction: Suction | None = Suction.FULL_SPEED
    sweep_mop_type: SweepMopType | None = None

    #: Resume the interrupted clean once the crossing finishes.
    resume_after: bool = True
    #: How far from the gate the robot may be and still be considered staged.
    max_staging_distance: float = 1.2


@dataclass
class Sample:
    at: float
    signed: float
    lateral: float
    heading_error: float
    pose: Pose


@dataclass
class Attempt:
    gate_id: str
    outcome: Outcome
    lateral: float
    runup: float
    obliquity: float
    duration: float
    start_pose: Pose | None = None
    end_pose: Pose | None = None
    best_signed: float = float("-inf")
    fault: int = 0
    notes: list[str] = field(default_factory=list)
    trace: list[Sample] = field(default_factory=list)
    restore_problems: list[str] = field(default_factory=list)

    @property
    def progress(self) -> float:
        """Furthest the robot got past the line, in metres. Negative if short."""
        return self.best_signed if self.best_signed != float("-inf") else 0.0

    def summary(self) -> str:
        return (
            f"{self.outcome.value:<11} runup={self.runup:.2f}m "
            f"lateral={self.lateral:+.2f}m obliquity={self.obliquity:+.0f}deg "
            f"reached={self.progress:+.2f}m in {self.duration:.1f}s"
            + (f"  [{'; '.join(self.notes)}]" if self.notes else "")
        )


class CrossingAborted(RuntimeError):
    pass


class CrossingEngine:
    def __init__(
        self,
        vacuum: Vacuum,
        gate: Gate,
        reader: PoseReader,
        params: CrossingParams | None = None,
        *,
        confirm: Callable[[str], bool] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self.vac = vacuum
        self.gate = gate
        self.reader = reader
        self.params = params or CrossingParams()
        self._confirm = confirm
        # Injectable so the maneuver can be exercised against the simulator in
        # virtual time, deterministically and without real waiting.
        self._now = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._deadline = 0.0
        if dry_run is None:
            dry_run = bool(getattr(getattr(vacuum, "t", None), "dry_run", False))
        self.dry_run = dry_run

    # -- helpers -------------------------------------------------------------

    def _pose(self) -> Pose:
        sample = self.reader.read()
        if sample is None:
            raise CrossingAborted("lost pose telemetry")
        return sample.pose

    def _check_budget(self) -> None:
        if self._now() > self._deadline:
            raise CrossingAborted("total time budget exhausted")

    def _sample(self) -> Sample:
        pose = self._pose()
        return Sample(
            at=self._now(),
            signed=self.gate.signed_distance(pose.point),
            lateral=self.gate.lateral_offset(pose.point),
            heading_error=self.gate.heading_error(pose, self.params.obliquity),
            pose=pose,
        )

    @contextmanager
    def _remote_mode(self, attempt: Attempt) -> Iterator[None]:
        """Own remote mode for the duration, and always give it back."""
        self.vac.enter_remote()
        self._sleep(0.4)
        status = self.vac.status()
        if status != Status.REMOTE:
            self.vac.exit_remote()
            raise CrossingAborted(
                f"robot did not enter remote mode (status={self.vac.status_name()})"
            )
        try:
            yield
        finally:
            for step in (self.vac.remote_halt, self.vac.exit_remote):
                try:
                    step()
                except Exception as exc:  # noqa: BLE001
                    attempt.restore_problems.append(f"{step.__name__}: {exc}")
                    _LOG.error("failed during remote-mode teardown: %s", exc)

    def _pump(
        self,
        *,
        command: Callable[[], object],
        done: Callable[[Sample], bool],
        timeout: float,
        attempt: Attempt,
        progress: Callable[[Sample], float] | None = None,
        reissue: bool = True,
    ) -> tuple[bool, Sample | None]:
        """Drive with `command` until `done`, or until stalled or out of time.

        Returns (finished, last_sample). `progress` extracts the scalar we
        expect to keep improving; if it stops improving by `stall_distance`
        within `stall_window`, the pump reports a stall by returning False.
        """
        started = self._now()
        last_command = 0.0
        best = float("-inf")
        best_at = started
        last: Sample | None = None

        command()
        last_command = self._now()

        while True:
            self._check_budget()
            now = self._now()
            if now - started > timeout:
                attempt.notes.append(f"phase timed out after {timeout:.0f}s")
                return False, last

            last = self._sample()
            attempt.trace.append(last)
            if done(last):
                return True, last

            if progress is not None:
                value = progress(last)
                if value > best + self.params.stall_distance:
                    best, best_at = value, now
                elif now - best_at > self.params.stall_window:
                    return False, last

            if reissue and now - last_command >= self.params.command_interval:
                command()
                last_command = now

            self._sleep(self.params.poll_interval)

    # -- phases --------------------------------------------------------------

    #: How close to the staging point counts as staged, metres.
    STAGE_TOLERANCE = 0.03

    def _stage(self, attempt: Attempt, lateral: float, runup: float) -> None:
        """Position the robot exactly `runup` metres back from the threshold.

        Reverses if it is too close, and creeps forward if it is too far back.
        The forward case matters for calibration: starting further back than
        the requested run-up would silently give every short attempt a long
        run-up, and the sweep would report a shorter working distance than is
        really needed.
        """
        target = -runup
        sample = self._sample()

        if abs(sample.signed - target) <= self.STAGE_TOLERANCE:
            return

        if sample.signed < target:
            finished, last = self._pump(
                command=self.vac.remote_forward,
                done=lambda s: s.signed >= target,
                timeout=self.params.stage_timeout,
                attempt=attempt,
                progress=lambda s: s.signed,
            )
            self.vac.remote_halt()
            if not finished:
                attempt.notes.append(
                    f"could not close up to the {runup:.2f}m mark "
                    f"(sat at {-(last.signed if last else 0):.2f}m)"
                )
            self._sleep(0.5)
            return

        finished, last = self._pump(
            command=self.vac.remote_back,
            done=lambda s: s.signed <= target,
            timeout=self.params.stage_timeout,
            attempt=attempt,
            # Reversing means the signed distance should keep decreasing.
            progress=lambda s: -s.signed,
        )
        self.vac.remote_halt()
        if not finished:
            reached = -last.signed if last else 0.0
            attempt.notes.append(
                f"could not open {runup:.2f}m of run-up (reached {reached:.2f}m) "
                "- most likely blocked by furniture behind the doorway"
            )
            raise CrossingAborted("run-up space unavailable")
        self._sleep(0.4)

    def _align(self, attempt: Attempt) -> None:
        """Rotate onto the approach heading."""
        tolerance = self.params.align_tolerance
        sample = self._sample()
        if abs(sample.heading_error) <= tolerance:
            return

        turn_ccw = sample.heading_error > 0
        command = (
            self.vac.remote_left
            if turn_ccw == self.params.left_is_ccw
            else self.vac.remote_right
        )
        finished, last = self._pump(
            command=command,
            done=lambda s: abs(s.heading_error) <= tolerance,
            timeout=self.params.align_timeout,
            attempt=attempt,
            progress=lambda s: -abs(s.heading_error),
        )
        self.vac.remote_halt()
        if not finished:
            error = last.heading_error if last else float("nan")
            attempt.notes.append(f"alignment stalled at {error:+.0f}deg error")
        self._sleep(0.3)

    def _drive(self, attempt: Attempt) -> Outcome:
        """The momentum run. Classifies how it ended."""
        gate, params = self.gate, self.params
        started = self._now()
        best = float("-inf")
        best_at = started
        last_command = 0.0

        self.vac.remote_forward()
        last_command = self._now()

        while True:
            self._check_budget()
            now = self._now()

            sample = self._sample()
            attempt.trace.append(sample)
            attempt.best_signed = max(attempt.best_signed, sample.signed)

            if sample.signed >= gate.clearance:
                return Outcome.CROSSED

            fault = self.vac.fault()
            if fault:
                attempt.fault = fault
                attempt.notes.append(f"device fault {fault}")
                return Outcome.FAULTED

            if sample.signed > best + params.stall_distance:
                best, best_at = sample.signed, now
            elif now - best_at > params.stall_window:
                # Stalled. Whether it ever got onto the lip says which failure
                # this is, and the two want opposite responses.
                if attempt.best_signed >= params.lip_reach:
                    attempt.notes.append(
                        f"stranded straddling the lip at {sample.signed:+.2f}m"
                    )
                    return Outcome.BEACHED
                attempt.notes.append(
                    f"stopped {abs(sample.signed):.2f}m short without committing"
                )
                return Outcome.REFUSED

            if now - started > params.drive_timeout:
                attempt.notes.append("drive phase timed out")
                return Outcome.TIMEOUT

            if params.reissue_forward and now - last_command >= params.command_interval:
                self.vac.remote_forward()
                last_command = now

            self._sleep(params.poll_interval)

    def _escape(self, attempt: Attempt) -> None:
        """Back off a beached robot. Never push a high-centred robot forward."""
        self.vac.remote_halt()
        self._sleep(0.3)
        target = -self.params.escape_distance
        finished, last = self._pump(
            command=self.vac.remote_back,
            done=lambda s: s.signed <= target,
            timeout=self.params.escape_timeout,
            attempt=attempt,
            progress=lambda s: -s.signed,
        )
        self.vac.remote_halt()
        if finished:
            attempt.notes.append("reversed clear of the threshold")
        else:
            reached = last.signed if last else float("nan")
            attempt.notes.append(
                f"COULD NOT REVERSE CLEAR (stuck at {reached:+.2f}m) - "
                "the robot probably needs lifting off by hand"
            )
            _LOG.error("escape failed; robot may still be on the threshold")

    # -- public API ----------------------------------------------------------

    def plan(
        self, pose: Pose, lateral: float, runup: float, obliquity: float
    ) -> list[str]:
        """Describe what an attempt would do, without doing any of it.

        Checks the gate definition against where the robot actually is, which
        is the useful thing a dry run can verify -- a dry run cannot rehearse
        the maneuver itself, because nothing moves and the pose never changes.
        """
        gate = self.gate
        signed = gate.signed_distance(pose.point)
        staging = gate.staging_point(lateral, runup)
        target = gate.target_point(lateral)
        heading = gate.approach_heading(obliquity)
        move = "reverse" if signed > -runup else "creep forward"
        return [
            f"robot is {abs(signed):.2f} m "
            f"{'short of' if signed < 0 else 'past'} the threshold, "
            f"{gate.lateral_offset(pose.point):+.2f} m off centre",
            f"would {move} to staging point "
            f"({staging.x:.2f}, {staging.y:.2f}), {runup:.2f} m back",
            f"would turn to {heading:+.0f} deg "
            f"(currently {pose.heading:+.0f} deg, "
            f"{gate.heading_error(pose, obliquity):+.0f} deg to correct)",
            f"would drive to ({target.x:.2f}, {target.y:.2f}), "
            f"{runup + gate.clearance:.2f} m of travel in all",
            "settings: "
            + ", ".join(
                f"{label} {value.name.lower()}"
                for label, value in (
                    ("water", self.params.water),
                    ("suction", self.params.suction),
                    ("mode", self.params.sweep_mop_type),
                )
                if value is not None
            ),
        ]

    def attempt(
        self,
        lateral: float = 0.0,
        *,
        runup: float | None = None,
        obliquity: float | None = None,
    ) -> Attempt:
        """Run one crossing attempt and restore everything afterwards."""
        params = self.params
        runup = params.runup if runup is None else runup
        obliquity = params.obliquity if obliquity is None else obliquity
        self.params = replace_params(params, runup=runup, obliquity=obliquity)

        attempt = Attempt(
            gate_id=self.gate.id,
            outcome=Outcome.ABORTED,
            lateral=lateral,
            runup=runup,
            obliquity=obliquity,
            duration=0.0,
        )
        started = self._now()
        self._deadline = started + params.total_budget
        saved: Settings | None = None

        try:
            pose = self._pose()
            attempt.start_pose = pose

            if not self.gate.is_near(pose.point, radius=params.max_staging_distance):
                attempt.outcome = Outcome.NOT_STAGED
                attempt.notes.append(
                    f"robot is {self.gate.distance_to_line(pose.point):.2f}m from the "
                    "gate - move it near the threshold first; this engine does not "
                    "path-plan across a room"
                )
                return attempt

            if self.gate.is_crossed(pose.point):
                attempt.outcome = Outcome.CROSSED
                attempt.notes.append("already on the far side")
                return attempt

            if self.dry_run:
                attempt.outcome = Outcome.DRY_RUN
                attempt.notes.extend(self.plan(pose, lateral, runup, obliquity))
                return attempt

            if self._confirm and not self._confirm(
                f"Take over the robot at gate {self.gate.id!r} "
                f"(run-up {runup:.2f}m, obliquity {obliquity:+.0f}deg)?"
            ):
                attempt.notes.append("declined by operator")
                return attempt

            saved = self.vac.snapshot_settings()
            _LOG.info("saved settings: %s", saved.describe())
            desired = Settings(
                suction=int(params.suction) if params.suction is not None else None,
                water=int(params.water) if params.water is not None else None,
                sweep_mop_type=(
                    int(params.sweep_mop_type)
                    if params.sweep_mop_type is not None
                    else None
                ),
            )
            self.vac.apply_settings(desired)

            if self.vac.status() == Status.SWEEPING:
                self.vac.pause()
                self._sleep(0.5)

            with self._remote_mode(attempt):
                self._stage(attempt, lateral, runup)
                self._align(attempt)
                attempt.outcome = self._drive(attempt)
                self.vac.remote_halt()
                if attempt.outcome is Outcome.BEACHED:
                    self._escape(attempt)

        except CrossingAborted as exc:
            attempt.notes.append(str(exc))
            if attempt.outcome is Outcome.ABORTED:
                attempt.outcome = Outcome.ABORTED
        except Exception as exc:  # noqa: BLE001
            attempt.notes.append(f"unexpected error: {exc}")
            _LOG.exception("crossing attempt failed")
        finally:
            self.params = params
            attempt.duration = self._now() - started
            try:
                attempt.end_pose = self._pose()
            except Exception:  # noqa: BLE001
                pass
            if saved is not None:
                attempt.restore_problems += self.vac.apply_settings(saved)
            # Belt and braces: the context manager exits remote mode, but if we
            # never reached it -- or it failed -- make sure anyway.
            try:
                if self.vac.in_remote_mode():
                    self.vac.exit_remote()
            except Exception as exc:  # noqa: BLE001
                attempt.restore_problems.append(f"exit_remote: {exc}")
            if params.resume_after and attempt.outcome.is_success:
                try:
                    self.vac.resume()
                except Exception as exc:  # noqa: BLE001
                    attempt.notes.append(f"resume failed: {exc}")

        return attempt

    def calibrate(
        self,
        runups: list[float],
        *,
        lateral: float = 0.0,
        obliquity: float = 0.0,
        stop_on_success: bool = True,
        between: float = 4.0,
    ) -> list[Attempt]:
        """Sweep run-up distance to find the shortest one that works.

        Ascending order matters: the shortest successful run-up is the one that
        will still fit once furniture constrains the approach, so we want the
        minimum that crosses, not merely one that does.
        """
        results: list[Attempt] = []
        for runup in sorted(runups):
            _LOG.info("--- calibration: run-up %.2f m ---", runup)
            result = self.attempt(lateral, runup=runup, obliquity=obliquity)
            results.append(result)
            print(f"  {result.summary()}")
            if result.outcome is Outcome.NOT_STAGED:
                print("  stopping: robot is not staged at the gate")
                break
            if result.outcome.is_success and stop_on_success:
                break
            self._sleep(between)
        return results


def replace_params(params: CrossingParams, **changes) -> CrossingParams:
    from dataclasses import replace

    return replace(params, **changes)
