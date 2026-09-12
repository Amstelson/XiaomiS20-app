"""MIoT address book for xiaomi.vacuum.b108gl (Xiaomi Robot Vacuum S20+).

Single source of truth for every siid/piid/aiid. Nothing else in the codebase
should contain a bare service or property number -- "was carpet-boost 6/6 or
2/6?" is the bug class this module exists to prevent.

Derived from the published spec:
urn:miot-spec-v2:device:vacuum:0000A006:xiaomi-b108gl:1
"""

from __future__ import annotations

from enum import IntEnum
from typing import NamedTuple

MODEL = "xiaomi.vacuum.b108gl"
SPEC_URN = "urn:miot-spec-v2:device:vacuum:0000A006:xiaomi-b108gl:1"


class Prop(NamedTuple):
    siid: int
    piid: int
    name: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.name} ({self.siid}/p{self.piid})"


class Action(NamedTuple):
    siid: int
    aiid: int
    name: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.name} ({self.siid}/A{self.aiid})"


# --- service 1: device information -----------------------------------------
MANUFACTURER = Prop(1, 1, "manufacturer")
DEVICE_MODEL = Prop(1, 2, "model")
DEVICE_ID = Prop(1, 3, "device-id")
FIRMWARE = Prop(1, 4, "firmware-version")
SERIAL_NUMBER = Prop(1, 5, "serial-number")

# --- service 2: vacuum ------------------------------------------------------
STATUS = Prop(2, 1, "status")
FAULT = Prop(2, 2, "fault")
SWEEP_MOP_TYPE = Prop(2, 3, "sweep-mop-type")
SWEEP_TYPE = Prop(2, 4, "sweep-type")
CLEANING_AREA = Prop(2, 5, "cleaning-area")
CLEANING_TIME = Prop(2, 6, "cleaning-time")
CLEAN_TIMES = Prop(2, 7, "clean-times")
SUCTION_LEVEL = Prop(2, 8, "suction-level")
MOP_WATER_LEVEL = Prop(2, 9, "mop-water-level")
ZONE_IDS = Prop(2, 10, "zone-ids")
RESTRICTED_AREAS = Prop(2, 11, "restricted-sweep-areas")
RESTRICTED_WALLS = Prop(2, 12, "restricted-walls")
ROOM_IDS = Prop(2, 13, "vacuum-room-ids")
ROOM_NAME = Prop(2, 14, "room-name")
POINTS = Prop(2, 15, "points")
MODE = Prop(2, 16, "mode")
EDGE_SWING = Prop(2, 17, "edge-swing-tail-sweep")
EDGE_FREQUENCY = Prop(2, 18, "edge-sweep-frequency")
NOTICE = Prop(2, 19, "notice")
CARPET_METHOD = Prop(2, 20, "carpet-cleaning-method")
CARPET_DISCRIMINATE = Prop(2, 21, "carpet-discriminate")

START_SWEEP = Action(2, 1, "start-sweep")
STOP_SWEEP = Action(2, 2, "stop-sweeping")
START_SWEEP_ONLY = Action(2, 3, "start-only-sweep")
START_MOP = Action(2, 4, "start-mop")
START_SWEEP_MOP = Action(2, 5, "start-sweep-mop")
PAUSE = Action(2, 6, "pause-sweeping")
GET_ZONE_CONFIGS = Action(2, 7, "get-zone-configs")
GET_ROOM_CONFIGS = Action(2, 8, "get-room-configs")
SET_ZONE = Action(2, 9, "set-zone")
SET_ROOM_CONFIGS = Action(2, 10, "set-room-clean-configs")
SPLIT_ROOM = Action(2, 11, "split-room")
MERGE_ROOMS = Action(2, 12, "merge-rooms")
START_ROOM_SWEEP = Action(2, 13, "start-vacuum-room-sweep")
START_BUILD_MAP = Action(2, 14, "start-build-map")

# --- service 3: battery -----------------------------------------------------
BATTERY_LEVEL = Prop(3, 1, "battery-level")
CHARGING_STATE = Prop(3, 2, "charging-state")
VOLTAGE = Prop(3, 3, "voltage")
START_CHARGE = Action(3, 1, "start-charge")

# --- service 6: vacuum-extend ----------------------------------------------
MOP_STATUS = Prop(6, 1, "mop-status")
FIRMWARE_EXT = Prop(6, 2, "firmware-version-ext")
DND_SWITCH = Prop(6, 3, "dnd-switch")
DND_TIME = Prop(6, 4, "dnd-time")
ORDER_CLEAN = Prop(6, 5, "order-clean")
CARPET_BOOST = Prop(6, 6, "carpet-boost")
CARPET_AVOIDANCE = Prop(6, 7, "carpet-avoidance")
CARPET_DISPLAY = Prop(6, 8, "carpet-display")
STATUS_EXTEND = Prop(6, 9, "status-extend")
ROOM_INFO = Prop(6, 10, "room-info")
SWEEP_BREAK_SWITCH = Prop(6, 11, "sweep-break-switch")
COMMON_PARAMS_6 = Prop(6, 12, "common-params")
FAULT_INDEX = Prop(6, 13, "fault-index")

CONTINUE_SWEEP = Action(6, 1, "continue-sweep")
REMOTE_CONTROL = Action(6, 2, "remote-control")
ADD_ORDER_CLEAN = Action(6, 3, "add-order-clean")
EDIT_ORDER_CLEAN = Action(6, 4, "edit-order-clean")
DELETE_ORDER_CLEAN = Action(6, 5, "delete-order-clean")
FIND_VACUUM = Action(6, 6, "find-vacuum")
START_CUSTOM_SWEEP = Action(6, 7, "start-custom-sweep")
TRY_LISTEN = Action(6, 8, "try-listen")
REMOTE_UP = Action(6, 9, "start-remote-up")
REMOTE_LEFT = Action(6, 10, "start-remote-left")
REMOTE_RIGHT = Action(6, 11, "start-remote-right")
REMOTE_DOWN = Action(6, 12, "start-remote-down")
REMOTE_STOP = Action(6, 13, "stop-remote")
REMOTE_EXIT = Action(6, 14, "exit-remote")
STOP_AND_GOCHARGE = Action(6, 15, "stop-and-gocharge")
GET_SYSTEM_INFO = Action(6, 16, "get-system-info")
UPLOAD_LOG = Action(6, 17, "upload-log")

# --- service 7: vacuum-map --------------------------------------------------
MAP_OBJ_NAME = Prop(7, 1, "map-obj-name")
TRAJECTORY_OBJ_NAME = Prop(7, 2, "trajectory-obj-name")
CLEAN_RECORD = Prop(7, 3, "clean-record")
VACUUM_POSITION = Prop(7, 4, "vacuum-position")
PERMANENT_MAP = Prop(7, 5, "permanent-map")
PERMANENT_MAP_ID = Prop(7, 6, "permanent-map-id")
COMMON_PARAMS_7 = Prop(7, 7, "common-params")
CARPET_OBJ_NAME = Prop(7, 8, "carpet-obj-name")
MAP_CONTROL_SWITCH = Prop(7, 9, "map-control-switch")

CLEAN_PERMANENT_MAP = Action(7, 1, "clean-permanent-map")
DELETE_PERMANENT_MAP = Action(7, 2, "delete-permanent-map")
SET_PERMANENT_MAP = Action(7, 3, "set-permanent-map")
SAVE_PERMANENT_MAP = Action(7, 4, "save-permanent-map")
AUTO_ROOM_PARTITION = Action(7, 5, "auto-room-partition")
SET_MAP_NAME = Action(7, 6, "set-map-name")
CLEAN_HISTORY = Action(7, 7, "clean-history")
SET_MAP_CONTROL = Action(7, 8, "set-map-control")
GET_ONE_HISTORY_MAP = Action(7, 9, "get-one-history-map")

# --- consumables ------------------------------------------------------------
CONSUMABLES = {
    "main-brush": (Prop(8, 1, "brush-left-time"), Prop(8, 2, "brush-life-level")),
    "side-brush": (Prop(9, 1, "brush-left-time"), Prop(9, 2, "brush-life-level")),
    "filter": (Prop(10, 1, "filter-left-time"), Prop(10, 2, "filter-life-level")),
    "mop": (Prop(11, 1, "brush-left-time"), Prop(11, 2, "brush-life-level")),
}


class Status(IntEnum):
    IDLE = 1
    CHARGING = 2
    BREAK_CHARGING = 3
    SWEEPING = 4
    PAUSED = 5
    GO_CHARGING = 6
    REMOTE = 7
    CHARGED = 8
    MAPPING = 9
    UPDATING = 10


class SweepType(IntEnum):
    GLOBAL = 1
    AREA = 4
    MAPPING = 5
    GO_CHARGING = 6
    REMOTE_CONTROL = 7
    SELECT_ROOM = 8
    CUSTOM_CLEAN = 9


class SweepMopType(IntEnum):
    SWEEP = 1
    MOP = 2
    SWEEP_MOP = 3
    SWEEP_THEN_MOP = 4


class Suction(IntEnum):
    SILENT = 1
    BASIC = 2
    STRONG = 3
    FULL_SPEED = 4


class WaterLevel(IntEnum):
    OFF = 0
    LEVEL1 = 1
    LEVEL2 = 2
    LEVEL3 = 3


class CarpetMethod(IntEnum):
    SELF_ADAPTION = 0
    AVOID = 1
    IGNORE = 2


#: Statuses in which the robot is under our remote-control command.
REMOTE_STATUSES = frozenset({Status.REMOTE})

#: Statuses from which it is safe to begin a takeover.
TAKEOVER_OK_STATUSES = frozenset(
    {Status.IDLE, Status.SWEEPING, Status.PAUSED, Status.GO_CHARGING, Status.REMOTE}
)
