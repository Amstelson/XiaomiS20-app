"""Typed facade over the S20+.

Everything the rest of the hub needs, expressed in terms of the robot rather
than siid/piid pairs. Also owns settings snapshot/restore, which the crossing
maneuver depends on to put things back exactly as it found them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from . import spec
from .spec import Status, Suction, SweepMopType, WaterLevel
from .transport import MiotError, Transport

_LOG = logging.getLogger(__name__)


@dataclass
class Settings:
    """The settings a maneuver may disturb, so they can be restored."""

    suction: int | None = None
    water: int | None = None
    sweep_mop_type: int | None = None

    def describe(self) -> str:
        def name(enum, value):
            try:
                return enum(value).name.lower()
            except (ValueError, TypeError):
                return str(value)

        return (
            f"suction={name(Suction, self.suction)} "
            f"water={name(WaterLevel, self.water)} "
            f"mode={name(SweepMopType, self.sweep_mop_type)}"
        )


class Vacuum:
    def __init__(self, transport: Transport) -> None:
        self.t = transport

    # -- state ---------------------------------------------------------------

    def status(self) -> Status | int:
        value = self.t.get(spec.STATUS)
        try:
            return Status(value)
        except ValueError:
            return value

    def status_name(self) -> str:
        value = self.status()
        return value.name.lower() if isinstance(value, Status) else f"unknown({value})"

    def fault(self) -> int:
        return int(self.t.get(spec.FAULT) or 0)

    def fault_index(self) -> Any:
        ok, value = self.t.try_get(spec.FAULT_INDEX)
        return value if ok else None

    def battery(self) -> int:
        return int(self.t.get(spec.BATTERY_LEVEL))

    def position_raw(self) -> Any:
        return self.t.get(spec.VACUUM_POSITION)

    def mop_attached(self) -> bool | None:
        ok, value = self.t.try_get(spec.MOP_STATUS)
        return bool(value) if ok else None

    def summary(self) -> dict[str, Any]:
        values = self.t.get_many(
            [
                spec.STATUS,
                spec.FAULT,
                spec.BATTERY_LEVEL,
                spec.SUCTION_LEVEL,
                spec.MOP_WATER_LEVEL,
                spec.SWEEP_MOP_TYPE,
                spec.SWEEP_TYPE,
                spec.VACUUM_POSITION,
            ]
        )
        return values

    # -- settings ------------------------------------------------------------

    def snapshot_settings(self) -> Settings:
        values = self.t.get_many(
            [spec.SUCTION_LEVEL, spec.MOP_WATER_LEVEL, spec.SWEEP_MOP_TYPE]
        )
        return Settings(
            suction=values.get(spec.SUCTION_LEVEL.name),
            water=values.get(spec.MOP_WATER_LEVEL.name),
            sweep_mop_type=values.get(spec.SWEEP_MOP_TYPE.name),
        )

    def apply_settings(self, settings: Settings) -> list[str]:
        """Apply any non-None fields. Returns a list of failures, never raises.

        Restore must be best-effort: one rejected write cannot be allowed to
        abandon the remaining restores, least of all leaving the robot in
        remote mode.
        """
        problems: list[str] = []
        for prop, value in (
            (spec.SUCTION_LEVEL, settings.suction),
            (spec.MOP_WATER_LEVEL, settings.water),
            (spec.SWEEP_MOP_TYPE, settings.sweep_mop_type),
        ):
            if value is None:
                continue
            try:
                self.t.set(prop, value)
            except (MiotError, Exception) as exc:  # noqa: BLE001
                problems.append(f"{prop.name}={value!r}: {exc}")
                _LOG.warning("failed to set %s to %r: %s", prop, value, exc)
        return problems

    def set_suction(self, level: Suction | int) -> None:
        self.t.set(spec.SUCTION_LEVEL, int(level))

    def set_water(self, level: WaterLevel | int) -> None:
        self.t.set(spec.MOP_WATER_LEVEL, int(level))

    def set_sweep_mop_type(self, mode: SweepMopType | int) -> None:
        self.t.set(spec.SWEEP_MOP_TYPE, int(mode))

    # -- cleaning ------------------------------------------------------------

    def start(self) -> Any:
        return self.t.action(spec.START_SWEEP)

    def pause(self) -> Any:
        return self.t.action(spec.PAUSE)

    def stop(self) -> Any:
        return self.t.action(spec.STOP_SWEEP)

    def resume(self) -> Any:
        """Continue an interrupted clean (breakpoint resume)."""
        return self.t.action(spec.CONTINUE_SWEEP)

    def dock(self) -> Any:
        return self.t.action(spec.START_CHARGE)

    def find(self) -> Any:
        return self.t.action(spec.FIND_VACUUM)

    def clean_rooms(self, room_ids: list[int]) -> Any:
        import json

        payload = json.dumps({"room": list(room_ids)}, separators=(",", ":"))
        return self.t.action(
            spec.START_ROOM_SWEEP, [{"piid": spec.ROOM_IDS.piid, "value": payload}]
        )

    def room_info(self) -> Any:
        ok, value = self.t.try_get(spec.ROOM_INFO)
        return value if ok else None

    # -- remote control ------------------------------------------------------
    #
    # These are the only primitives that let us steer the robot directly, and
    # therefore the entire basis of the crossing maneuver.

    def enter_remote(self) -> Any:
        return self.t.action(spec.REMOTE_CONTROL)

    def remote_forward(self) -> Any:
        return self.t.action(spec.REMOTE_UP)

    def remote_back(self) -> Any:
        return self.t.action(spec.REMOTE_DOWN)

    def remote_left(self) -> Any:
        return self.t.action(spec.REMOTE_LEFT)

    def remote_right(self) -> Any:
        return self.t.action(spec.REMOTE_RIGHT)

    def remote_halt(self) -> Any:
        return self.t.action(spec.REMOTE_STOP)

    def exit_remote(self) -> Any:
        return self.t.action(spec.REMOTE_EXIT)

    def in_remote_mode(self) -> bool:
        return self.status() == Status.REMOTE

    def system_info(self) -> Any:
        return self.t.action(spec.GET_SYSTEM_INFO)
