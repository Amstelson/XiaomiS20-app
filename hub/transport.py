"""MIoT transport to the robot over the local network (UDP 54321).

Builds MIoT envelopes on top of `hub.miio_protocol`, keeping the exact payloads
-- and the per-property result codes -- visible rather than hidden behind a
library's mapping layer.

Two safety properties are built in and every caller gets them for free:

* **dry-run** -- reads always execute; writes and actions are logged and
  skipped. New code paths should be exercised this way first.
* **rate limiting** -- a minimum interval between outbound calls. Newer Xiaomi
  firmware is unhappy with aggressive polling.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .miio_protocol import MiioDeviceError, MiioProtocol, MiioProtocolError
from .spec import Action, Prop

_LOG = logging.getLogger(__name__)


class TransportError(RuntimeError):
    pass


class MiotError(TransportError):
    """The device answered, but reported a non-zero code for the property."""

    def __init__(self, target: str, code: int, payload: Any):
        super().__init__(f"{target} returned MIoT code {code}: {payload}")
        self.target = target
        self.code = code
        self.payload = payload


class Transport:
    """Local MIoT transport with dry-run and rate limiting."""

    def __init__(
        self,
        ip: str,
        token: str,
        *,
        timeout: int = 5,
        min_interval: float = 0.10,
        dry_run: bool = False,
    ) -> None:
        self.ip = ip
        self.dry_run = dry_run
        self._min_interval = min_interval
        self._last_call = 0.0
        self._lock = threading.Lock()
        self._proto = MiioProtocol(ip, token, timeout=timeout)

    @property
    def device_id(self) -> int | None:
        return self._proto.device_id

    def handshake(self) -> None:
        """Establish the device id and clock. Raises if the robot is unreachable."""
        with self._lock:
            self._throttle()
            self._proto.handshake()

    # -- plumbing ------------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    def _send(self, command: str, payload: Any) -> Any:
        with self._lock:
            self._throttle()
            _LOG.debug("-> %s %s", command, payload)
            try:
                result = self._proto.send(command, payload)
            except MiioDeviceError as exc:
                raise MiotError(command, exc.code or -1, exc.error) from exc
            except MiioProtocolError as exc:
                raise TransportError(str(exc)) from exc
            _LOG.debug("<- %s", result)
            return result

    # -- reads ---------------------------------------------------------------

    def get(self, prop: Prop) -> Any:
        """Read one property. Raises MiotError if the device rejects it."""
        result = self._send(
            "get_properties",
            [{"did": prop.name, "siid": prop.siid, "piid": prop.piid}],
        )
        if not result:
            raise TransportError(f"empty response reading {prop}")
        entry = result[0]
        code = entry.get("code", 0)
        if code != 0:
            raise MiotError(str(prop), code, entry)
        return entry.get("value")

    def try_get(self, prop: Prop) -> tuple[bool, Any]:
        """Read one property, reporting failure instead of raising.

        Used by the probe, which deliberately reads addresses that may not
        exist on this model.
        """
        try:
            return True, self.get(prop)
        except (MiotError, TransportError) as exc:
            return False, exc

    def get_many(self, props: list[Prop], *, chunk: int = 10) -> dict[str, Any]:
        """Read several properties, batching into one request where possible."""
        out: dict[str, Any] = {}
        for start in range(0, len(props), chunk):
            batch = props[start : start + chunk]
            result = self._send(
                "get_properties",
                [{"did": p.name, "siid": p.siid, "piid": p.piid} for p in batch],
            )
            by_did = {e.get("did"): e for e in (result or [])}
            for prop in batch:
                entry = by_did.get(prop.name)
                if entry is None or entry.get("code", 0) != 0:
                    out[prop.name] = None
                else:
                    out[prop.name] = entry.get("value")
        return out

    # -- writes --------------------------------------------------------------

    def set(self, prop: Prop, value: Any) -> Any:
        if self.dry_run:
            _LOG.info("[dry-run] set %s = %r", prop, value)
            return None
        result = self._send(
            "set_properties",
            [
                {
                    "did": f"set-{prop.name}",
                    "siid": prop.siid,
                    "piid": prop.piid,
                    "value": value,
                }
            ],
        )
        if result and result[0].get("code", 0) != 0:
            raise MiotError(str(prop), result[0]["code"], result[0])
        return result

    def action(self, action: Action, params: list | None = None) -> Any:
        """Invoke an action.

        ``params`` is the MIoT ``in`` list, e.g. ``[{"piid": 13, "value": '{"room":[5]}'}]``.
        Note that complex arguments are JSON strings nested inside the JSON
        envelope -- see docs/01-protocol-reference.md.
        """
        if self.dry_run:
            _LOG.info("[dry-run] action %s params=%r", action, params)
            return None
        return self._send(
            "action",
            {
                "did": f"call-{action.name}",
                "siid": action.siid,
                "aiid": action.aiid,
                "in": params or [],
            },
        )
