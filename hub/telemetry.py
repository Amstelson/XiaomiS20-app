"""Pose telemetry.

`vacuum-position` (7/p4) is a string whose exact format is not documented for
this model. Rather than guess once and silently produce nonsense, the parser
accepts every plausible encoding, keeps the raw value alongside the result, and
makes unit handling explicit and overridable.

Run `tools/probe.py` to see what your robot actually emits, then pin the format
in config if the auto-detection guesses wrong.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator

from .geometry import Pose, normalize_deg

_LOG = logging.getLogger(__name__)

_NUMBER = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


class PositionParseError(ValueError):
    pass


@dataclass(frozen=True)
class PoseSample:
    pose: Pose
    raw: str
    at: float

    @property
    def age(self) -> float:
        return time.monotonic() - self.at


@dataclass(frozen=True)
class PositionFormat:
    """How to turn the raw string into metres and degrees.

    `linear_scale` multiplies x and y to reach metres (1.0 if already metres,
    0.001 for millimetres, 0.01 for centimetres). `angle_in_radians` says
    whether the heading needs converting. `None` means auto-detect per sample.
    """

    linear_scale: float | None = None
    angle_in_radians: bool | None = None

    def resolve(self, x: float, y: float, angle: float) -> "PositionFormat":
        """Fill in anything left on auto, using the magnitudes as evidence."""
        scale = self.linear_scale
        if scale is None:
            magnitude = max(abs(x), abs(y))
            if magnitude > 2000:
                scale = 0.001  # millimetres
            elif magnitude > 100:
                scale = 0.01  # centimetres
            else:
                scale = 1.0  # already metres
        radians = self.angle_in_radians
        if radians is None:
            # Headings in degrees routinely exceed 2*pi; radians never do.
            radians = abs(angle) <= 2 * math.pi + 1e-6
        return PositionFormat(linear_scale=scale, angle_in_radians=radians)


def _coerce(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _from_mapping(data: dict) -> tuple[float, float, float] | None:
    """Pull x/y/heading out of a JSON object, whatever the keys are called."""
    lowered = {str(k).lower(): v for k, v in data.items()}
    for nest in ("pose", "position", "point", "current", "curpos"):
        if nest not in lowered:
            continue
        nested = lowered[nest]
        if isinstance(nested, dict):
            inner = _from_mapping(nested)
            if inner is not None:
                return inner
        # The S20+ emits {"position": [x, y, angle]} -- a bare list under the
        # key, not an object.
        if isinstance(nested, (list, tuple)):
            numbers = [n for n in (_coerce(v) for v in nested) if n is not None]
            inner = _from_numbers(numbers)
            if inner is not None:
                return inner

    x = next((_coerce(lowered[k]) for k in ("x", "px", "posx") if k in lowered), None)
    y = next((_coerce(lowered[k]) for k in ("y", "py", "posy") if k in lowered), None)
    if x is None or y is None:
        return None
    angle = next(
        (
            _coerce(lowered[k])
            for k in ("phi", "a", "angle", "theta", "yaw", "heading", "dir")
            if k in lowered
        ),
        None,
    )
    return x, y, angle if angle is not None else 0.0


def _from_numbers(numbers: list[float]) -> tuple[float, float, float] | None:
    """Interpret a bare list of numbers.

    Observed shapes across this device family are ``x,y,phi`` and
    ``id,x,y,phi``. Anything longer is assumed to lead with an id or counter
    and to carry the pose in the last three fields.
    """
    if len(numbers) < 2:
        return None
    if len(numbers) == 2:
        return numbers[0], numbers[1], 0.0
    if len(numbers) == 3:
        return numbers[0], numbers[1], numbers[2]
    return numbers[-3], numbers[-2], numbers[-1]


def extract_triple(raw: Any) -> tuple[float, float, float]:
    """Pull the unconverted (x, y, angle) out of a raw position value.

    Kept separate from unit conversion so callers that need to decide on units
    -- notably PoseReader, when locking the format -- can inspect the original
    magnitudes rather than values that have already been scaled.
    """
    if raw is None:
        raise PositionParseError("position value is None")

    triple: tuple[float, float, float] | None = None

    if isinstance(raw, dict):
        triple = _from_mapping(raw)
    elif isinstance(raw, (list, tuple)):
        nums = [n for n in (_coerce(v) for v in raw) if n is not None]
        triple = _from_numbers(nums)
    else:
        text = str(raw).strip()
        if not text:
            raise PositionParseError("position value is empty")
        if text[0] in "{[":
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                triple = _from_mapping(decoded)
            elif isinstance(decoded, (list, tuple)):
                nums = [n for n in (_coerce(v) for v in decoded) if n is not None]
                triple = _from_numbers(nums)
        if triple is None:
            triple = _from_numbers([float(m) for m in _NUMBER.findall(text)])

    if triple is None:
        raise PositionParseError(f"could not parse position from {raw!r}")
    return triple


def parse_position(raw: Any, fmt: PositionFormat | None = None) -> Pose:
    """Parse a `vacuum-position` value into a world-frame Pose.

    Raises PositionParseError when nothing usable can be extracted, so a bad
    sample is never mistaken for the origin.
    """
    fmt = fmt or PositionFormat()
    x, y, angle = extract_triple(raw)
    resolved = fmt.resolve(x, y, angle)
    heading = math.degrees(angle) if resolved.angle_in_radians else angle
    return Pose(
        x=x * resolved.linear_scale,
        y=y * resolved.linear_scale,
        heading=normalize_deg(heading),
    )


class PoseReader:
    """Polls the robot for its pose, with a little resilience.

    Keeps the last good sample so a single dropped or unparseable read does not
    abort a maneuver mid-run-up.
    """

    def __init__(
        self,
        read_raw: Callable[[], Any],
        fmt: PositionFormat | None = None,
        *,
        max_stale: float = 2.0,
    ) -> None:
        self._read_raw = read_raw
        self._fmt = fmt or PositionFormat()
        self._max_stale = max_stale
        self._last: PoseSample | None = None
        self.parse_failures = 0
        self.read_failures = 0

    @property
    def last(self) -> PoseSample | None:
        return self._last

    def read(self) -> PoseSample | None:
        """Fetch a fresh pose, or the last good one if it is still recent."""
        try:
            raw = self._read_raw()
        except Exception as exc:  # noqa: BLE001 - transport errors vary
            self.read_failures += 1
            _LOG.debug("pose read failed: %s", exc)
            return self._fallback()

        try:
            x, y, angle = extract_triple(raw)
        except PositionParseError as exc:
            self.parse_failures += 1
            _LOG.debug("pose parse failed: %s", exc)
            return self._fallback()

        # The first good sample locks the units, derived from the *raw*
        # magnitudes. Without this a robot passing near the origin would look
        # like metres and every later sample would be scaled wrongly.
        if self._fmt.linear_scale is None or self._fmt.angle_in_radians is None:
            self._fmt = self._fmt.resolve(x, y, angle)
            _LOG.info(
                "pose format locked: scale=%s radians=%s (from %r)",
                self._fmt.linear_scale,
                self._fmt.angle_in_radians,
                raw,
            )

        heading = math.degrees(angle) if self._fmt.angle_in_radians else angle
        pose = Pose(
            x=x * self._fmt.linear_scale,
            y=y * self._fmt.linear_scale,
            heading=normalize_deg(heading),
        )
        self._last = PoseSample(pose=pose, raw=str(raw), at=time.monotonic())
        return self._last

    def _fallback(self) -> PoseSample | None:
        if self._last is not None and self._last.age <= self._max_stale:
            return self._last
        return None

    def stream(self, interval: float = 0.2) -> Iterator[PoseSample | None]:
        """Yield samples forever at roughly the requested interval."""
        while True:
            started = time.monotonic()
            yield self.read()
            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)

    def with_format(self, fmt: PositionFormat) -> "PoseReader":
        reader = PoseReader(self._read_raw, fmt, max_stale=self._max_stale)
        reader._last = self._last
        return reader
