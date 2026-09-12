"""Crossing maneuver tests, run against the simulator in virtual time.

These cover the behaviour that matters when the engine is driving a real robot
around a real doorway: that more run-up wins, that beaching is escaped rather
than pushed through, and above all that the robot is never left in remote mode.
"""

import pytest

from hub.crossing import CrossingEngine, CrossingParams, Outcome
from hub.geometry import Point
from hub.simulator import SimRobot, SimVacuum, VirtualClock, make_doorway
from hub.spec import Status, Suction, WaterLevel
from hub.telemetry import PoseReader, PositionFormat


def build(
    *,
    start: tuple[float, float, float] = (-0.30, 0.0, 0.0),
    min_speed: float = 0.18,
    bad_spots=None,
    params: CrossingParams | None = None,
    max_speed: float = 0.32,
):
    gate, model = make_doorway(min_speed=min_speed, bad_spots=bad_spots)
    robot = SimRobot(
        threshold=model, x=start[0], y=start[1], heading=start[2], max_speed=max_speed
    )
    vac = SimVacuum(robot)
    clock = VirtualClock(robot)
    reader = PoseReader(
        vac.position_raw, PositionFormat(linear_scale=1.0, angle_in_radians=True)
    )
    engine = CrossingEngine(
        vac,
        gate,
        reader,
        params or CrossingParams(poll_interval=0.05, command_interval=0.1),
        clock=clock.now,
        sleep=clock.sleep,
    )
    return engine, vac, robot, gate


# -- the core relationship: run-up buys momentum -----------------------------


def test_short_runup_beaches():
    engine, _, robot, gate = build(min_speed=0.25)
    result = engine.attempt(runup=0.12)
    assert result.outcome is Outcome.BEACHED
    assert "straddling" in " ".join(result.notes)


def test_long_runup_crosses():
    engine, _, robot, gate = build(min_speed=0.25)
    result = engine.attempt(runup=0.75)
    assert result.outcome is Outcome.CROSSED
    assert gate.is_crossed(Point(robot.x, robot.y))


def test_there_is_a_runup_threshold_and_calibration_finds_it():
    """The whole point of the sweep: locate the shortest run-up that works."""
    outcomes = {}
    for runup in (0.10, 0.20, 0.35, 0.50, 0.70, 0.90):
        engine, _, _, _ = build(min_speed=0.25)
        outcomes[runup] = engine.attempt(runup=runup).outcome

    crossed = [r for r, o in outcomes.items() if o is Outcome.CROSSED]
    failed = [r for r, o in outcomes.items() if o is not Outcome.CROSSED]
    assert crossed, f"nothing crossed: {outcomes}"
    assert failed, f"everything crossed, model has no threshold: {outcomes}"
    # Monotonic: once a run-up works, every longer one works too.
    assert min(crossed) > max(failed)


def test_calibrate_stops_at_the_shortest_success():
    engine, _, _, _ = build(min_speed=0.25)
    results = engine.calibrate([0.9, 0.15, 0.6, 0.3], between=0.0)
    assert results[-1].outcome is Outcome.CROSSED
    # Ascending order, so earlier attempts are the shorter ones that failed.
    assert [r.runup for r in results] == sorted(r.runup for r in results)
    assert all(not r.outcome.is_success for r in results[:-1])


def test_staging_creeps_forward_when_parked_too_far_back():
    """Otherwise every short run-up in a sweep is silently a long one."""
    engine, _, robot, gate = build(start=(-0.90, 0.0, 0.0), min_speed=0.25)
    result = engine.attempt(runup=0.20)
    # 0.20 m of run-up is not enough for this threshold, so it must not cross.
    assert result.outcome is not Outcome.CROSSED
    starts = [s.signed for s in result.trace]
    # It should have come forward from -0.90 towards -0.20 before driving.
    assert min(starts) < -0.5
    assert any(-0.25 <= s <= -0.15 for s in starts)


def test_calibration_sweep_is_honest_about_short_runups():
    """A sweep from a far-back parking spot must still fail the short entries."""
    engine, _, _, _ = build(start=(-1.00, 0.0, 0.0), min_speed=0.25)
    results = engine.calibrate([0.15, 0.25, 0.45], between=0.0, stop_on_success=False)
    by_runup = {r.runup: r.outcome for r in results}
    assert by_runup[0.15] is not Outcome.CROSSED
    assert by_runup[0.45] is Outcome.CROSSED


# -- failure modes are told apart --------------------------------------------


def test_bad_heading_produces_refusal_not_beaching():
    """A robot that never commits is a different diagnosis from one that strands."""
    params = CrossingParams(
        poll_interval=0.05, command_interval=0.1, align_tolerance=90.0
    )
    engine, _, _, _ = build(start=(-0.30, 0.0, 50.0), params=params)
    result = engine.attempt(runup=0.6, obliquity=50.0)
    assert result.outcome is Outcome.REFUSED
    assert "without committing" in " ".join(result.notes)


def test_device_fault_is_reported_as_faulted():
    engine, vac, _, _ = build()
    vac.fault_value = 2118
    result = engine.attempt(runup=0.5)
    assert result.outcome is Outcome.FAULTED
    assert result.fault == 2118


def test_bad_spot_on_the_lip_fails_where_a_good_spot_succeeds():
    """Thresholds are not uniform -- this is what spot search is for."""
    bad = [(0.0, 0.12, 0.30)]  # a high patch in the middle
    engine_mid, _, _, _ = build(min_speed=0.2, bad_spots=bad)
    assert engine_mid.attempt(0.0, runup=0.6).outcome is not Outcome.CROSSED

    engine_off, _, _, _ = build(start=(-0.30, 0.30, 0.0), min_speed=0.2, bad_spots=bad)
    assert engine_off.attempt(0.30, runup=0.6).outcome is Outcome.CROSSED


# -- beaching is escaped, never pushed through -------------------------------


def test_beaching_triggers_reverse_escape():
    engine, _, robot, gate = build(min_speed=0.30)
    result = engine.attempt(runup=0.12)
    assert result.outcome is Outcome.BEACHED
    assert not robot.stuck, "robot was left stranded on the lip"
    assert gate.signed_distance(Point(robot.x, robot.y)) < 0
    assert "reversed clear" in " ".join(result.notes)


def test_robot_is_not_driven_forward_while_beached():
    engine, _, robot, _ = build(min_speed=0.30)
    engine.attempt(runup=0.12)
    assert robot.command != "forward"


# -- safety invariants -------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs", [dict(runup=0.12), dict(runup=0.75), dict(runup=0.5)]
)
def test_remote_mode_always_released(kwargs):
    engine, vac, _, _ = build(min_speed=0.25)
    engine.attempt(**kwargs)
    assert not vac.in_remote_mode()
    assert "exit_remote" in vac.calls


def test_remote_mode_released_even_when_telemetry_dies_mid_run():
    engine, vac, robot, _ = build()
    reads = {"n": 0}
    original = vac.position_raw

    def flaky():
        reads["n"] += 1
        if reads["n"] > 6:
            raise RuntimeError("transport gone")
        return original()

    engine.reader = PoseReader(
        flaky, PositionFormat(1.0, True), max_stale=0.0
    )
    result = engine.attempt(runup=0.9)
    assert not vac.in_remote_mode()
    assert result.outcome in {Outcome.ABORTED, Outcome.TIMEOUT}


def test_settings_restored_after_attempt():
    engine, vac, _, _ = build()
    before = dict(vac.settings)
    engine.attempt(runup=0.6)
    assert vac.settings == before


def test_settings_changed_during_the_attempt():
    """Water off and full suction should actually be applied, then undone."""
    engine, vac, _, _ = build()
    seen = []
    original = vac.apply_settings

    def spy(settings):
        seen.append((settings.water, settings.suction))
        return original(settings)

    vac.apply_settings = spy
    engine.attempt(runup=0.6)
    assert (int(WaterLevel.OFF), int(Suction.FULL_SPEED)) in seen


def test_unconfirmed_remote_status_is_noted_but_not_treated_as_refusal():
    """A docked robot reports `charged` until asked to move -- aborting there
    would give up on a maneuver that would have worked."""
    engine, vac, _, _ = build(min_speed=0.25)
    original = vac.enter_remote

    def enter_but_keep_reporting_charged():
        original()
        vac.status_value = Status.CHARGED

    vac.enter_remote = enter_but_keep_reporting_charged
    result = engine.attempt(runup=0.8)
    assert result.outcome is Outcome.CROSSED
    assert any("status still" in n for n in result.notes)


def test_a_robot_that_ignores_remote_commands_fails_cleanly():
    engine, vac, robot, _ = build()
    vac.allow_remote = False
    before = (robot.x, robot.y)
    result = engine.attempt(runup=0.5)
    assert result.outcome in {Outcome.REFUSED, Outcome.TIMEOUT, Outcome.ABORTED}
    assert (robot.x, robot.y) == before, "robot must not have moved"
    assert not vac.in_remote_mode()
    assert "exit_remote" in vac.calls


def test_resume_only_after_a_successful_crossing():
    engine, vac, _, _ = build(min_speed=0.25)
    engine.attempt(runup=0.12)
    assert not vac.resumed

    engine2, vac2, _, _ = build(min_speed=0.25)
    engine2.attempt(runup=0.8)
    assert vac2.resumed


def test_paused_before_taking_over_a_running_clean():
    engine, vac, _, _ = build()
    vac.status_value = Status.SWEEPING
    engine.attempt(runup=0.6)
    assert "pause" in vac.calls


# -- refusing to act when not staged -----------------------------------------


def test_refuses_to_act_when_far_from_the_gate():
    """No path planning here, so a distant robot must be left alone."""
    engine, vac, _, _ = build(start=(-4.0, 2.0, 0.0))
    result = engine.attempt(runup=0.5)
    assert result.outcome is Outcome.NOT_STAGED
    assert "enter_remote" not in vac.calls


def test_already_across_is_a_no_op():
    engine, vac, _, _ = build(start=(0.45, 0.0, 0.0))
    result = engine.attempt(runup=0.5)
    assert result.outcome is Outcome.CROSSED
    assert "enter_remote" not in vac.calls


def test_blocked_runup_is_reported_as_such():
    """Furniture behind the doorway is the whole reason this problem exists."""
    engine, _, robot, _ = build(start=(-0.25, 0.0, 0.0))

    # Pin the robot: it cannot reverse any further than it already is.
    original_tick = robot.tick

    def penned(dt):
        before = (robot.x, robot.y)
        original_tick(dt)
        if robot.command == "back" and robot.x < -0.28:
            robot.x, robot.y = before

    robot.tick = penned
    result = engine.attempt(runup=0.80)
    assert result.outcome is Outcome.ABORTED
    assert "run-up" in " ".join(result.notes)


# -- dry run -----------------------------------------------------------------


def test_dry_run_describes_the_plan_without_moving():
    engine, vac, robot, _ = build()
    engine.dry_run = True
    before = (robot.x, robot.y, robot.heading)
    result = engine.attempt(runup=0.55)
    assert result.outcome is Outcome.DRY_RUN
    assert (robot.x, robot.y, robot.heading) == before
    assert "enter_remote" not in vac.calls
    assert any("staging point" in n for n in result.notes)
    assert any("would drive to" in n for n in result.notes)
    settings = next(n for n in result.notes if n.startswith("settings:"))
    assert "water off" in settings and "suction full_speed" in settings


def test_dry_run_still_refuses_when_not_staged():
    engine, _, _, _ = build(start=(-4.0, 2.0, 0.0))
    engine.dry_run = True
    assert engine.attempt(runup=0.5).outcome is Outcome.NOT_STAGED


def test_dry_run_is_inferred_from_the_transport():
    gate, model = make_doorway()
    robot = SimRobot(threshold=model, x=-0.3, y=0.0, heading=0.0)
    vac = SimVacuum(robot)

    class FakeTransport:
        dry_run = True

    vac.t = FakeTransport()
    clock = VirtualClock(robot)
    engine = CrossingEngine(
        vac,
        gate,
        PoseReader(vac.position_raw, PositionFormat(1.0, True)),
        CrossingParams(poll_interval=0.05),
        clock=clock.now,
        sleep=clock.sleep,
    )
    assert engine.dry_run is True
    assert engine.attempt(runup=0.5).outcome is Outcome.DRY_RUN


# -- operator confirmation ---------------------------------------------------


def test_confirmation_hook_can_decline():
    gate, model = make_doorway()
    robot = SimRobot(threshold=model, x=-0.3, y=0.0, heading=0.0)
    vac = SimVacuum(robot)
    clock = VirtualClock(robot)
    engine = CrossingEngine(
        vac,
        gate,
        PoseReader(vac.position_raw, PositionFormat(1.0, True)),
        CrossingParams(poll_interval=0.05),
        confirm=lambda _msg: False,
        clock=clock.now,
        sleep=clock.sleep,
    )
    result = engine.attempt(runup=0.5)
    assert "declined" in " ".join(result.notes)
    assert "enter_remote" not in vac.calls


# -- traces are recorded for calibration -------------------------------------


def test_attempt_records_a_usable_trace():
    engine, _, _, _ = build()
    result = engine.attempt(runup=0.6)
    assert len(result.trace) > 3
    assert result.best_signed > -1.0
    assert result.duration > 0
    assert "runup=" in result.summary()
