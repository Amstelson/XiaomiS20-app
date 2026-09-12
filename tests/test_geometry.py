"""Geometry tests.

The crossing maneuver is only as trustworthy as this module, and it is the one
part we can verify completely without hardware -- so it is tested properly.
"""

import math

import pytest

from hub.geometry import Gate, Point, Pose, angle_diff, bearing, normalize_deg


def make_gate(**kw) -> Gate:
    """A 0.9 m threshold lying along the y axis at x=0, crossed toward +x."""
    defaults = dict(
        id="test",
        a=Point(0.0, -0.45),
        b=Point(0.0, 0.45),
        from_side=Point(-1.0, 0.0),
    )
    defaults.update(kw)
    return Gate(**defaults)


# -- angles ------------------------------------------------------------------


@pytest.mark.parametrize(
    "angle,expected",
    [(0, 0), (180, 180), (-180, 180), (190, -170), (360, 0), (-370, -10), (540, 180)],
)
def test_normalize_deg(angle, expected):
    assert normalize_deg(angle) == pytest.approx(expected)


def test_angle_diff_takes_short_way_round():
    assert angle_diff(170, -170) == pytest.approx(-20)
    assert angle_diff(-170, 170) == pytest.approx(20)
    assert angle_diff(10, 350) == pytest.approx(20)


# -- orientation -------------------------------------------------------------


def test_crossing_heading_never_prints_as_negative_zero():
    assert not f"{make_gate().crossing_heading:+.0f}".startswith("-0")


def test_normal_points_away_from_start_side():
    gate = make_gate()
    assert gate.normal.x == pytest.approx(1.0)
    assert gate.normal.y == pytest.approx(0.0, abs=1e-9)
    assert gate.crossing_heading == pytest.approx(0.0)


def test_normal_flips_with_start_side():
    """Same physical threshold, crossed the other way, must invert."""
    gate = make_gate(from_side=Point(1.0, 0.0))
    assert gate.normal.x == pytest.approx(-1.0)
    assert abs(gate.crossing_heading) == pytest.approx(180.0)


def test_endpoint_order_does_not_change_crossing_direction():
    """a/b order is arbitrary; only from_side decides which way across is."""
    forward = make_gate()
    reversed_endpoints = make_gate(a=Point(0.0, 0.45), b=Point(0.0, -0.45))
    assert reversed_endpoints.crossing_heading == pytest.approx(
        forward.crossing_heading
    )
    p = Point(0.3, 0.0)
    assert reversed_endpoints.signed_distance(p) == pytest.approx(
        forward.signed_distance(p)
    )


def test_diagonal_gate_normal_is_perpendicular():
    gate = make_gate(a=Point(0, 0), b=Point(1, 1), from_side=Point(1, 0))
    d, n = gate.direction, gate.normal
    assert d.x * n.x + d.y * n.y == pytest.approx(0.0, abs=1e-9)
    assert n.length() == pytest.approx(1.0)
    # from_side is below the line, so the normal must point above it.
    assert gate.signed_distance(Point(0, 1)) > 0


# -- signed distance ---------------------------------------------------------


def test_signed_distance_sign_and_magnitude():
    gate = make_gate()
    assert gate.signed_distance(Point(-0.5, 0.0)) == pytest.approx(-0.5)
    assert gate.signed_distance(Point(0.0, 0.0)) == pytest.approx(0.0)
    assert gate.signed_distance(Point(0.3, 0.2)) == pytest.approx(0.3)


def test_crossed_and_straddling_are_distinct_states():
    """The two outcomes the maneuver must tell apart."""
    gate = make_gate(clearance=0.2)
    beached = Point(0.02, 0.1)
    crossed = Point(0.25, 0.1)
    short = Point(-0.4, 0.1)

    assert gate.is_straddling(beached) and not gate.is_crossed(beached)
    assert gate.is_crossed(crossed) and not gate.is_straddling(crossed)
    assert not gate.is_crossed(short) and not gate.is_straddling(short)


# -- along-gate coordinates --------------------------------------------------


def test_lateral_offset_is_metres_from_the_middle():
    gate = make_gate()
    assert gate.lateral_offset(Point(0.0, 0.0)) == pytest.approx(0.0)
    assert gate.lateral_offset(Point(0.5, 0.3)) == pytest.approx(0.3)
    assert gate.lateral_offset(Point(-0.5, -0.2)) == pytest.approx(-0.2)


def test_point_at_and_lateral_offset_round_trip():
    gate = make_gate()
    for lateral in (-0.3, -0.1, 0.0, 0.25, 0.4):
        assert gate.lateral_offset(gate.point_at(lateral)) == pytest.approx(lateral)


def test_spots_stay_inside_the_jambs():
    gate = make_gate(edge_inset=0.15)
    spots = gate.spots(5)
    assert len(spots) == 5
    half_usable = gate.length / 2 - 0.15
    assert min(spots) == pytest.approx(-half_usable)
    assert max(spots) == pytest.approx(half_usable)
    assert all(abs(s) <= gate.length / 2 for s in spots)


def test_spots_are_ordered_and_unique():
    spots = make_gate().spots(7)
    assert spots == sorted(spots)
    assert len(set(spots)) == len(spots)


def test_single_spot_is_the_centre():
    assert make_gate().spots(1) == [0.0]


def test_edge_inset_wider_than_gate_is_rejected():
    with pytest.raises(ValueError, match="usable width"):
        make_gate(edge_inset=0.6).spots(3)


# -- staging and targets -----------------------------------------------------


def test_staging_point_sits_behind_the_gate():
    gate = make_gate(approach_distance=0.5)
    staging = gate.staging_point(0.0)
    assert gate.signed_distance(staging) == pytest.approx(-0.5)
    assert gate.lateral_offset(staging) == pytest.approx(0.0)


def test_staging_distance_can_be_overridden_for_a_run_up_sweep():
    gate = make_gate(approach_distance=0.45)
    for distance in (0.25, 0.4, 0.65, 0.9):
        staging = gate.staging_point(0.1, distance)
        assert gate.signed_distance(staging) == pytest.approx(-distance)
        assert gate.lateral_offset(staging) == pytest.approx(0.1)


def test_target_point_is_past_the_line():
    gate = make_gate(clearance=0.25)
    target = gate.target_point(0.0)
    assert gate.signed_distance(target) == pytest.approx(0.25)
    assert gate.is_crossed(target)


def test_staging_to_target_distance_is_runup_plus_clearance():
    gate = make_gate(approach_distance=0.5, clearance=0.2)
    staging, target = gate.staging_point(0.0), gate.target_point(0.0)
    assert staging.distance_to(target) == pytest.approx(0.7)


# -- headings ----------------------------------------------------------------


def test_approach_heading_applies_obliquity():
    gate = make_gate()
    assert gate.approach_heading(0) == pytest.approx(0.0)
    assert gate.approach_heading(20) == pytest.approx(20.0)
    assert gate.approach_heading(-20) == pytest.approx(-20.0)


def test_heading_error_measures_misalignment():
    gate = make_gate()
    assert gate.heading_error(Pose(-0.5, 0, 0)) == pytest.approx(0.0)
    assert gate.heading_error(Pose(-0.5, 0, 15)) == pytest.approx(-15.0)
    assert gate.heading_error(Pose(-0.5, 0, -15), obliquity=0) == pytest.approx(15.0)
    # Aligned to an oblique approach means zero error for that approach.
    assert gate.heading_error(Pose(-0.5, 0, 20), obliquity=20) == pytest.approx(0.0)


def test_heading_error_wraps_across_180():
    gate = make_gate(from_side=Point(1.0, 0.0))  # crossing heading is 180
    assert abs(gate.heading_error(Pose(0.5, 0, -179))) == pytest.approx(1.0)


def test_bearing_between_points():
    assert bearing(Point(0, 0), Point(1, 0)) == pytest.approx(0)
    assert bearing(Point(0, 0), Point(0, 1)) == pytest.approx(90)
    assert bearing(Point(0, 0), Point(-1, 0)) == pytest.approx(180)


# -- proximity ---------------------------------------------------------------


def test_is_near_covers_the_doorway_approach():
    gate = make_gate()
    assert gate.is_near(Point(-0.3, 0.0))
    assert gate.is_near(Point(0.3, 0.2))
    assert not gate.is_near(Point(-3.0, 0.0))
    assert not gate.is_near(Point(0.0, 4.0))


# -- validation --------------------------------------------------------------


def test_coincident_endpoints_rejected():
    with pytest.raises(ValueError, match="coincident"):
        make_gate(a=Point(1, 1), b=Point(1, 1))


def test_from_side_on_the_line_rejected():
    with pytest.raises(ValueError, match="from_side"):
        make_gate(from_side=Point(0.0, 0.2))


def test_non_positive_approach_distance_rejected():
    with pytest.raises(ValueError, match="approach_distance"):
        make_gate(approach_distance=0.0)


# -- a realistic doorway -----------------------------------------------------


def test_living_room_to_hall_scenario():
    """The real case: cross a 0.85 m doorway, living room -> hall."""
    gate = Gate(
        id="hall",
        name="Living room -> hall",
        a=Point(2.10, 3.40),
        b=Point(2.10, 4.25),
        from_side=Point(1.20, 3.80),  # living room side
        approach_distance=0.55,
        clearance=0.22,
    )
    assert gate.crossing_heading == pytest.approx(0.0)
    assert gate.length == pytest.approx(0.85)

    staging = gate.staging_point(0.0)
    assert staging.x == pytest.approx(2.10 - 0.55)
    assert gate.signed_distance(staging) < 0

    # Halfway onto the threshold is beaching, not success.
    assert gate.is_straddling(Point(2.12, 3.82))
    assert not gate.is_crossed(Point(2.12, 3.82))
    # Well into the hall is success.
    assert gate.is_crossed(Point(2.45, 3.82))
