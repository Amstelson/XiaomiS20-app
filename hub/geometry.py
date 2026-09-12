"""Gate geometry.

A *gate* is a threshold modelled as a line segment in world coordinates, with a
crossing direction. Everything here is pure maths on plain floats so it can be
tested exhaustively without a robot.

Coordinate convention
---------------------
World coordinates are metres, x to the right and y up. Headings are degrees,
0 deg pointing along +x, increasing counter-clockwise.

The robot's own convention is **not assumed to match** -- `tools/jog.py` drives
the robot a known distance and reports how x, y and heading actually move, so
the frame can be calibrated before any autonomous maneuver runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def normalize_deg(angle: float) -> float:
    """Wrap an angle to (-180, 180]."""
    wrapped = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 else wrapped


def angle_diff(a: float, b: float) -> float:
    """Smallest signed rotation taking heading `b` to heading `a`, in degrees."""
    return normalize_deg(a - b)


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def __sub__(self, other: "Point") -> "Point":
        return Point(self.x - other.x, self.y - other.y)

    def __add__(self, other: "Point") -> "Point":
        return Point(self.x + other.x, self.y + other.y)

    def scaled(self, k: float) -> "Point":
        return Point(self.x * k, self.y * k)

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def distance_to(self, other: "Point") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Pose:
    """Robot pose. `heading` is degrees in the world frame."""

    x: float
    y: float
    heading: float

    @property
    def point(self) -> Point:
        return Point(self.x, self.y)


@dataclass(frozen=True)
class Gate:
    """A threshold to be crossed.

    `a` and `b` are the endpoints of the threshold line. `from_side` is any
    point on the side the robot starts from; it fixes the sign of the crossing
    direction, so there is no ambiguity about which way "across" means.
    """

    id: str
    a: Point
    b: Point
    from_side: Point
    #: How far back from the line to stage the run-up, in metres.
    approach_distance: float = 0.45
    #: How far past the line counts as crossed, in metres.
    clearance: float = 0.20
    #: Endpoints are shrunk by this much so we never aim at a door jamb.
    edge_inset: float = 0.12
    name: str = ""
    map_id: int | None = None
    notes: str = ""
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    # -- basic vectors -------------------------------------------------------

    def __post_init__(self) -> None:
        if self.a.distance_to(self.b) < 1e-6:
            raise ValueError(f"gate {self.id!r}: endpoints are coincident")
        if self.approach_distance <= 0:
            raise ValueError(f"gate {self.id!r}: approach_distance must be positive")
        if abs(self.signed_distance(self.from_side)) < 1e-6:
            raise ValueError(
                f"gate {self.id!r}: from_side lies on the gate line; "
                "pick a point clearly on the starting side"
            )

    @property
    def length(self) -> float:
        return self.a.distance_to(self.b)

    @property
    def direction(self) -> Point:
        """Unit vector along the threshold, a -> b."""
        d = self.b - self.a
        return d.scaled(1.0 / d.length())

    @property
    def normal(self) -> Point:
        """Unit normal pointing in the crossing direction (away from from_side)."""
        d = self.direction
        n = Point(-d.y, d.x)
        # Flip so the normal points away from the starting side.
        rel = self.from_side - self.a
        if n.x * rel.x + n.y * rel.y > 0:
            n = n.scaled(-1.0)
        return n

    @property
    def crossing_heading(self) -> float:
        """Heading, in degrees, that drives straight across the threshold."""
        n = self.normal
        # The `+ 0.0` collapses negative zero, which otherwise prints as "-0".
        return math.degrees(math.atan2(n.y, n.x)) + 0.0

    # -- queries -------------------------------------------------------------

    def signed_distance(self, p: Point) -> float:
        """Distance from the gate line; positive once past it, in metres."""
        d = self.b - self.a
        length = d.length()
        nx, ny = -d.y / length, d.x / length
        rel = p - self.a
        raw = nx * rel.x + ny * rel.y
        # Orient so "positive" always means the far side from from_side.
        rel_from = self.from_side - self.a
        if nx * rel_from.x + ny * rel_from.y > 0:
            raw = -raw
        return raw

    def parameter(self, p: Point) -> float:
        """Where along a->b the point projects, as a fraction. Not clamped."""
        d = self.b - self.a
        rel = p - self.a
        return (d.x * rel.x + d.y * rel.y) / (d.length() ** 2)

    def lateral_offset(self, p: Point) -> float:
        """Distance from the midpoint along the threshold, in metres.

        Negative toward `a`, positive toward `b`. This is the coordinate the
        crossing-spot search works in, because it is in real units rather than
        a fraction of an arbitrary line length.
        """
        return (self.parameter(p) - 0.5) * self.length

    def point_at(self, lateral: float) -> Point:
        """A point on the gate line, `lateral` metres from the midpoint."""
        mid = Point((self.a.x + self.b.x) / 2, (self.a.y + self.b.y) / 2)
        return mid + self.direction.scaled(lateral)

    def staging_point(self, lateral: float, distance: float | None = None) -> Point:
        """Where to sit before a run-up at the given spot."""
        back = distance if distance is not None else self.approach_distance
        return self.point_at(lateral) + self.normal.scaled(-back)

    def target_point(self, lateral: float, distance: float | None = None) -> Point:
        """Where the robot should end up once it has crossed."""
        ahead = distance if distance is not None else self.clearance
        return self.point_at(lateral) + self.normal.scaled(ahead)

    def is_crossed(self, p: Point) -> bool:
        return self.signed_distance(p) >= self.clearance

    def is_straddling(self, p: Point, *, band: float = 0.12) -> bool:
        """True when the robot is sitting on the threshold itself.

        This is what beaching looks like: signed distance near zero and no
        longer changing.
        """
        return abs(self.signed_distance(p)) <= band

    def usable_lateral_range(self) -> tuple[float, float]:
        """The span of crossing spots, with the jambs trimmed off."""
        half = self.length / 2 - self.edge_inset
        if half <= 0:
            raise ValueError(
                f"gate {self.id!r}: edge_inset {self.edge_inset} leaves no usable width "
                f"on a {self.length:.2f} m gate"
            )
        return (-half, half)

    def spots(self, count: int = 5) -> list[float]:
        """Evenly spaced candidate crossing spots, as lateral offsets."""
        if count < 1:
            raise ValueError("count must be >= 1")
        low, high = self.usable_lateral_range()
        if count == 1:
            return [0.0]
        step = (high - low) / (count - 1)
        return [low + step * i for i in range(count)]

    def approach_heading(self, obliquity: float = 0.0) -> float:
        """Heading for the run-up, optionally angled off the perpendicular."""
        return normalize_deg(self.crossing_heading + obliquity)

    def heading_error(self, pose: Pose, obliquity: float = 0.0) -> float:
        """How far the robot's heading is from the desired approach, in degrees."""
        return angle_diff(self.approach_heading(obliquity), pose.heading)

    def distance_to_line(self, p: Point) -> float:
        return abs(self.signed_distance(p))

    def is_near(self, p: Point, radius: float = 0.8) -> bool:
        """Whether a point is close enough to this gate to be about to use it."""
        lateral = self.lateral_offset(p)
        low, high = self.usable_lateral_range()
        within_width = (low - radius) <= lateral <= (high + radius)
        return within_width and self.distance_to_line(p) <= radius


def bearing(origin: Point, target: Point) -> float:
    """Heading in degrees from one point to another."""
    d = target - origin
    return math.degrees(math.atan2(d.y, d.x))
