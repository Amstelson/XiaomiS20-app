"""Position parsing tests.

The wire format is unconfirmed, so the parser is tested against every encoding
this device family is known to use -- and against the failure cases, because a
mis-parsed pose during a maneuver is worse than no pose at all.
"""

import math

import pytest

from hub.telemetry import (
    PositionFormat,
    PositionParseError,
    PoseReader,
    parse_position,
)


# -- shapes ------------------------------------------------------------------


def test_csv_x_y_phi_in_metres_and_radians():
    pose = parse_position("1.5,2.25,1.5708")
    assert pose.x == pytest.approx(1.5)
    assert pose.y == pytest.approx(2.25)
    assert pose.heading == pytest.approx(90.0, abs=0.01)


def test_csv_with_leading_id_uses_the_last_three_fields():
    pose = parse_position("7,1.5,2.25,0")
    assert (pose.x, pose.y) == (pytest.approx(1.5), pytest.approx(2.25))


def test_json_object_keys():
    pose = parse_position('{"x": 1.0, "y": -2.0, "phi": 0.0}')
    assert (pose.x, pose.y, pose.heading) == (1.0, -2.0, 0.0)


def test_json_alternate_key_names():
    pose = parse_position('{"x": 1.0, "y": 2.0, "angle": 45}')
    assert pose.heading == pytest.approx(45.0)


def test_position_key_holding_a_bare_list():
    """The shape the S20+ actually emits."""
    pose = parse_position('{"position":[6,170,1551]}')
    assert (pose.x, pose.y) == (pytest.approx(0.06), pytest.approx(1.70))


def test_position_list_respects_an_explicit_format():
    fmt = PositionFormat(linear_scale=0.001, angle_in_radians=False)
    pose = parse_position('{"position":[6,170,1551]}', fmt)
    assert pose.x == pytest.approx(0.006)
    assert pose.y == pytest.approx(0.170)


def test_nested_pose_object():
    pose = parse_position('{"pose": {"x": 3.0, "y": 4.0, "phi": 0.0}, "id": 9}')
    assert (pose.x, pose.y) == (3.0, 4.0)


def test_json_array():
    pose = parse_position("[1.0, 2.0, 0.0]")
    assert (pose.x, pose.y) == (1.0, 2.0)


def test_native_dict_and_list_inputs():
    assert parse_position({"x": 1, "y": 2, "phi": 0}).x == 1
    assert parse_position([1, 2, 0]).y == 2


def test_quoted_and_spaced_values_tolerated():
    pose = parse_position('  "1.5, 2.5, 0.0"  ')
    assert (pose.x, pose.y) == (pytest.approx(1.5), pytest.approx(2.5))


def test_two_field_position_defaults_heading_to_zero():
    pose = parse_position("1.0,2.0")
    assert pose.heading == 0.0


# -- units -------------------------------------------------------------------


def test_millimetres_detected_from_magnitude():
    pose = parse_position("3400,-2250,0")
    assert pose.x == pytest.approx(3.4)
    assert pose.y == pytest.approx(-2.25)


def test_centimetres_detected_from_magnitude():
    pose = parse_position("340,-225,0")
    assert pose.x == pytest.approx(3.4)


def test_explicit_scale_overrides_detection():
    fmt = PositionFormat(linear_scale=0.001, angle_in_radians=False)
    pose = parse_position("340,225,90", fmt)
    assert pose.x == pytest.approx(0.34)
    assert pose.heading == pytest.approx(90.0)


def test_degrees_detected_when_angle_exceeds_two_pi():
    assert parse_position("1,2,90").heading == pytest.approx(90.0)
    assert parse_position("1,2,180").heading == pytest.approx(180.0)


def test_radians_detected_when_angle_is_small():
    assert parse_position("1,2,3.14159").heading == pytest.approx(180.0, abs=0.01)


def test_explicit_angle_units_override_detection():
    fmt = PositionFormat(linear_scale=1.0, angle_in_radians=False)
    assert parse_position("1,2,3", fmt).heading == pytest.approx(3.0)


def test_heading_normalised_into_range():
    pose = parse_position("1,2,370", PositionFormat(1.0, False))
    assert pose.heading == pytest.approx(10.0)
    pose = parse_position("1,2,-190", PositionFormat(1.0, False))
    assert pose.heading == pytest.approx(170.0)


# -- failures ----------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "", "   ", "no numbers here", "42"])
def test_unusable_values_raise(bad):
    with pytest.raises(PositionParseError):
        parse_position(bad)


# -- reader ------------------------------------------------------------------


def test_reader_returns_parsed_samples():
    reader = PoseReader(lambda: "1.0,2.0,0.0")
    sample = reader.read()
    assert sample is not None
    assert sample.pose.x == pytest.approx(1.0)
    assert sample.raw == "1.0,2.0,0.0"


def test_reader_falls_back_to_last_good_sample_on_error():
    values = iter(["1.0,2.0,0.0", None])

    def source():
        value = next(values)
        if value is None:
            raise RuntimeError("transport blip")
        return value

    reader = PoseReader(source)
    first = reader.read()
    second = reader.read()
    assert second is first
    assert reader.read_failures == 1


def test_reader_gives_up_once_the_cached_sample_is_stale():
    calls = iter(["1.0,2.0,0.0"])

    def source():
        try:
            return next(calls)
        except StopIteration:
            raise RuntimeError("gone") from None

    reader = PoseReader(source, max_stale=0.0)
    assert reader.read() is not None
    assert reader.read() is None


def test_reader_counts_parse_failures_separately():
    reader = PoseReader(lambda: "garbage")
    assert reader.read() is None
    assert reader.parse_failures == 1
    assert reader.read_failures == 0


def test_reader_locks_format_after_first_good_sample():
    """A robot passing near the origin must not flip to a different scale."""
    values = iter(["3400,2250,0", "10,5,0"])
    reader = PoseReader(lambda: next(values))
    first = reader.read()
    second = reader.read()
    assert first.pose.x == pytest.approx(3.4)
    # Locked to millimetres, so the small sample stays small.
    assert second.pose.x == pytest.approx(0.01)
