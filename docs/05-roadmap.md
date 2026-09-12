# Roadmap

Sequenced so that the riskiest unknowns are resolved first and nothing substantial is built
on an unvalidated assumption.

## Phase 0 — Prove the protocol  *(the gate on everything else)*

Run the probe script from `04-dev-environment.md`. Deliverables:

- [ ] Local miIO connection to `b108gl` confirmed, token working
- [ ] Full property dump committed as the reference fixture
- [ ] **`wifi_sn` located** and a map blob decrypted end-to-end to a parsed `RobotMap`
- [ ] `vacuum-position` real update rate measured
- [ ] Remote-control semantics characterised (continuous vs. impulse, latency)
- [ ] Pause → remote → exit → `continue-sweep` confirmed to resume, not restart

**Exit criterion:** a rendered PNG of your actual flat, produced from a decoded blob.

If `wifi_sn` cannot be found, the map feature needs a different approach and Phase 2 is
re-planned — better to learn that in week one.

> **Revised priority.** Threshold crossing moved ahead of maps, and carpet
> moved to the back, at the user's direction. Phases 1 and 4 below are
> substantially built already -- see the repository root.

## Phase 1 — Hub skeleton + control

- Transport layer, `spec.py` constants, typed device facade
- Adaptive poller, SQLite state store
- REST + WebSocket API, dry-run flag, rate limiting
- Recorder + `ReplayTransport`, first fixtures committed
- Minimal web client: status, battery, start/stop/pause, suction, water, room list

**Exit criterion:** you can run a room clean from the browser and never open the stock app.

## Phase 2 — Maps

- `RobotMap.proto` → generated bindings; crypto + decode chain, unit-tested on fixtures
- `MapModel` and canvas renderer: raster, rooms, path, charger, live pose overlay
- Map management: rename, select, delete, auto-partition, split/merge rooms
- No-go zones and virtual walls: draw, preview, write, with snapshot-before-write

**Exit criterion:** live map with the robot moving on it, and you can edit a no-go zone
more pleasantly than in the stock app.

## Phase 3 — Carpet

- Surface and control `Carpet Cleaning Method` (2/p20), `Carpet Discriminate` (2/p21),
  `carpet-boost` (6/p6)
- Decode and render the carpet layer from `carpet-obj-name` (7/p8)
- Per-room and per-zone carpet policy in our overlay DB, applied by the orchestrator
  before each room (e.g. *mop off + boost on* for the rug room, *avoid* during a mop run)

**Exit criterion:** a mop run that provably never wets a carpet, without you toggling
anything.

## Phase 4 — Gate Assist  *(the headline feature)*

- Kinematic simulator
- Gate model, editor in the web client, overlay persistence
- Stall/fault detection; state machine; **restore path and watchdogs first**
- Simulator-green, then hardware with dry-run, then live
- Attempt logging + Thompson-sampling spot ranking
- A/B the secondary levers (mop water, suction, sweep-only)

**Exit criterion:** measured crossing success over ≥30 real attempts, with a before/after
number. This is a feature you can actually evaluate — do so.

## Phase 5 — Orchestration & polish

- Multi-room clean plans with deliberate supervised crossings between rooms
- Schedules, run history, consumables
- Failure notifications

## Phase 6 — iOS

- PWA on the home screen throughout Phases 1–5
- React Native client reusing `map-core`; Tailscale for remote access

## Suggested first commits

1. `docs/` (this set)
2. `hub/device/spec.py` — every siid/piid/aiid as named constants
3. `tools/probe.py` — the Phase 0 script
4. `.gitignore` covering `fixtures/`, `secrets/`, `*.token`, `.env`
