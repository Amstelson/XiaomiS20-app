# Architecture

## The central decision: a hub, not a phone-only app

The obvious design is "iOS app talks straight to the robot". I recommend against it, for one
concrete reason: **the obstacle-crossing feature is a closed control loop that has to run for
the entire duration of a clean.** It polls position, notices the robot is stuck, takes over,
drives a recovery maneuver, and hands control back. A phone cannot do that — iOS suspends
background apps within seconds, and you would lose the loop every time the screen locks or
you leave the house.

So:

```
┌──────────────────────────────────────────────────────────────┐
│  HUB  (LXC container on Proxmox; Pi as fallback / field node) │
│                                                               │
│   miio transport ─┐                                           │
│   cloud client  ──┼─→ Device Manager ─→ State Store (SQLite)  │
│   map decoder   ──┘         │                                 │
│                             ├─→ Gate Assist engine            │
│                             ├─→ Carpet policy engine          │
│                             └─→ Clean orchestrator            │
│                                                               │
│   REST + WebSocket API  ──────────────────────────────────┐   │
└───────────────────────────────────────────────────────────┼───┘
         ↑ UDP 54321 (LAN)        ↑ HTTPS (map only)        │
    ┌────┴─────┐              ┌───┴──────────┐              │
    │  Robot   │              │ Xiaomi cloud │         ┌────┴────┐
    └──────────┘              └──────────────┘         │ Clients │
                                                       │ web/iOS │
                                                       └─────────┘
```

The hub owns the robot. Clients are thin: they render state and send intents. This also means
the web dev client and the eventual iOS app talk to the *same* API, so nothing you build in
Phase 1 is thrown away when you move to iOS.

## Why Python for the hub

- `python-miio` already implements the miIO handshake, token crypto and MIoT envelopes.
  Reimplementing that in Swift before the protocol is even validated is pure waste.
- Every reference implementation of the ijai map decoder is Python. Porting a format you
  have not yet successfully decoded once is the wrong order of operations.
- `protobuf`, `pycryptodome`, `Pillow`/`numpy` are all one `pip install` away.

FastAPI + `uvicorn` for the API, `asyncio` throughout, SQLite for state. Packaged as a Docker
image, deployed into an unprivileged LXC container on Proxmox.

Proxmox matters here for one reason beyond convenience: **snapshots**. This is software that
drives a physical machine around your home, and being able to roll the hub back to the last
known-good state in one click is worth real money during Phase 4. Networking must be
**bridged, not NAT** — the miIO handshake is UDP on the robot's L2 subnet. See
`00-decisions.md` for the details.

## Module layout

```
hub/
  transport/
    miio_local.py      # UDP 54321, token crypto, MIoT get/set/action
    cloud.py           # Mi account login, signed API calls, map URL
  device/
    spec.py            # b108gl siid/piid/aiid constants — single source of truth
    s20.py             # typed facade: status, suction, rooms, no-go, carpet...
    poller.py          # adaptive poll loop (fast while cleaning, slow while docked)
  map/
    robot_map_pb2.py   # generated from RobotMap.proto
    crypto.py          # wifi_sn KDF + AES-ECB + hex + zlib
    decode.py          # raw blob → MapModel
    model.py           # MapModel: raster, rooms, path, charger, pose, objects
  features/
    gates.py           # threshold/gate assist engine  (see 03-gate-assist.md)
    carpet.py          # carpet policy engine
    orchestrator.py    # multi-room clean plans
  api/
    rest.py  ws.py
  store/
    db.py              # gates, attempts, carpet zones, map snapshots
```

`device/spec.py` holding every siid/piid as a named constant is not bureaucracy — it is what
stops the "was carpet-boost 6/6 or 2/6?" class of bug that plagues these integrations.

## Data ownership

Our app keeps its own overlay database and never depends on the robot to store our concepts:

| Lives on the robot | Lives in our hub |
|---|---|
| Permanent maps, room partitions | Gate definitions (thresholds) |
| No-go zones, virtual walls | Per-gate crossing attempt history |
| Per-room fan/water settings | Carpet zone overrides and policies |
| Cleaning schedules | Clean plans, run history, map snapshots |

Gates are anchored to **map coordinates plus a `permanent-map-id`**, so if you re-map the
house the gates are flagged stale rather than silently pointing at the wrong doorway.

## Two hard constraints to design around

**Map freshness.** The map is cloud-fetched and only refreshes when the robot uploads a new
`map-obj-name`. Live position (`7/p4`) is local and much fresher. So: render the *map* from
the cloud blob, and overlay the *robot* from the local position property. Never block the UI
on a cloud round-trip.

**Cloud session fragility.** Mi account login involves 2FA and the session expires. Keep
cloud strictly on the map path, make every cloud failure non-fatal, cache the last good map,
and surface "map is N minutes stale" in the UI rather than erroring. Control must keep
working with the cloud entirely down.
