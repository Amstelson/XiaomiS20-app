"""Gate config loading tests."""

import pytest

from hub.gates import GateConfigError, parse_gates

GOOD = {
    "gate": [
        {
            "id": "hall",
            "name": "Living room -> hall",
            "a": [2.1, 3.4],
            "b": [2.1, 4.25],
            "from_side": [1.2, 3.8],
            "approach_distance": 0.55,
            "clearance": 0.2,
        }
    ]
}


def test_parses_a_gate():
    gates = parse_gates(GOOD)
    gate = gates["hall"]
    assert gate.length == pytest.approx(0.85)
    assert gate.approach_distance == pytest.approx(0.55)
    assert gate.crossing_heading == pytest.approx(0.0)


def test_defaults_applied_when_omitted():
    gate = parse_gates(
        {"gate": [{"id": "g", "a": [0, 0], "b": [0, 1], "from_side": [-1, 0.5]}]}
    )["g"]
    assert gate.approach_distance == pytest.approx(0.45)
    assert gate.clearance == pytest.approx(0.20)


def test_missing_id_rejected():
    with pytest.raises(GateConfigError, match="needs an id"):
        parse_gates({"gate": [{"a": [0, 0], "b": [0, 1], "from_side": [-1, 0]}]})


def test_duplicate_id_rejected():
    with pytest.raises(GateConfigError, match="duplicate"):
        parse_gates({"gate": [GOOD["gate"][0], GOOD["gate"][0]]})


@pytest.mark.parametrize("bad", [None, [1], [1, 2, 3], "nope"])
def test_malformed_point_rejected(bad):
    with pytest.raises(GateConfigError, match=r"must be \[x, y\]"):
        parse_gates({"gate": [{"id": "g", "a": bad, "b": [0, 1], "from_side": [-1, 0]}]})


def test_non_numeric_point_rejected():
    with pytest.raises(GateConfigError, match="not numeric"):
        parse_gates(
            {"gate": [{"id": "g", "a": ["x", "y"], "b": [0, 1], "from_side": [-1, 0]}]}
        )


def test_geometry_errors_surface_with_the_gate_id():
    with pytest.raises(GateConfigError, match="'g'"):
        parse_gates(
            {"gate": [{"id": "g", "a": [0, 0], "b": [0, 1], "from_side": [0, 0.5]}]}
        )


def test_empty_config_is_fine():
    assert parse_gates({}) == {}
