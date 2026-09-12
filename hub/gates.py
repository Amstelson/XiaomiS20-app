"""Loading gate definitions from gates.toml.

Gates are our own concept, not the robot's, so they live in our config rather
than on the device. `tools/jog.py` emits ready-made blocks for this file.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .geometry import Gate, Point

ROOT = Path(__file__).resolve().parent.parent
GATES_PATH = ROOT / "gates.toml"


class GateConfigError(RuntimeError):
    pass


def _point(raw, field: str, gate_id: str) -> Point:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise GateConfigError(
            f"gate {gate_id!r}: {field} must be [x, y], got {raw!r}"
        )
    try:
        return Point(float(raw[0]), float(raw[1]))
    except (TypeError, ValueError) as exc:
        raise GateConfigError(f"gate {gate_id!r}: {field} is not numeric") from exc


def parse_gates(data: dict) -> dict[str, Gate]:
    gates: dict[str, Gate] = {}
    for entry in data.get("gate", []):
        gate_id = entry.get("id")
        if not gate_id:
            raise GateConfigError("every [[gate]] needs an id")
        if gate_id in gates:
            raise GateConfigError(f"duplicate gate id {gate_id!r}")
        try:
            gates[gate_id] = Gate(
                id=gate_id,
                name=entry.get("name", ""),
                a=_point(entry.get("a"), "a", gate_id),
                b=_point(entry.get("b"), "b", gate_id),
                from_side=_point(entry.get("from_side"), "from_side", gate_id),
                approach_distance=float(entry.get("approach_distance", 0.45)),
                clearance=float(entry.get("clearance", 0.20)),
                edge_inset=float(entry.get("edge_inset", 0.12)),
                map_id=entry.get("map_id"),
                notes=entry.get("notes", ""),
            )
        except ValueError as exc:
            raise GateConfigError(f"gate {gate_id!r}: {exc}") from exc
    return gates


def load_gates(path: Path | None = None) -> dict[str, Gate]:
    path = path or GATES_PATH
    if not path.exists():
        raise GateConfigError(
            f"{path} not found.\n"
            "Create it by driving the robot to each end of the threshold with\n"
            "tools/jog.py, pressing 'm' at each end, then 'g'."
        )
    with path.open("rb") as fh:
        return parse_gates(tomllib.load(fh))
