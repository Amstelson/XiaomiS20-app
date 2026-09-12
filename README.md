# XiaomiS20-app

An alternative control app for the **Xiaomi Robot Vacuum S20+**
(`xiaomi.vacuum.b108gl`), built because the stock Xiaomi Home app is unreliable
and awkward for map work.

Priorities, in order:

1. **Threshold crossing** — the robot strands halfway across the living-room /
   hall doorway. This is the thing that breaks cleaning runs today.
2. **Maps** — a responsive live map with no-go and virtual-wall editing that
   does not fight back.
3. Carpet handling, scheduling, iOS — later.

## The threshold problem in one paragraph

The robot used to cross that doorway by backing off, building speed, and
carrying over on momentum — its own maneuver, chosen by itself. It stopped after
furniture was built around the opening, and now creeps at the lip and beaches.
The capability is in the firmware; something in the new geometry suppresses it.
So this project does not try to invent a maneuver — it **executes the one the
robot has stopped choosing**, via remote-control mode, which takes the
navigation planner's caution out of the loop. Full reasoning in
[`docs/03-gate-assist.md`](docs/03-gate-assist.md).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.toml.example config.toml    # add your robot's IP and token
```

The token comes from your Mi account, once, with
[Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor).
`config.toml`, `gates.toml` and `fixtures/` are gitignored — they hold device
material.

## Use it in this order

**1. Check we can talk to the robot at all.**

```bash
python3 tools/probe.py                 # read-only
python3 tools/probe.py --remote-test   # also drives it briefly in open floor
```

This dumps every property, hunts for the `wifi_sn` needed later for map
decryption, measures how fast position telemetry actually updates, and — with
`--remote-test` — settles whether a direction command drives continuously or
nudges once. Output lands in `fixtures/`.

**2. Drive it at the threshold by hand.**

```bash
python3 tools/jog.py
```

`w`/`s` drive, `a`/`d` turn, space stops, `q` quits and releases remote mode.
This is the decisive experiment: **does remote control cross the threshold where
the cleaning planner won't?** Try it with a good run-up. Have a hand ready.

While you are there, press `m` at each end of the doorway and then `g` — it
prints a ready-made gate block for `gates.toml`, so you never read coordinates
off a map by hand.

**3. Find the shortest run-up that works.**

```bash
cp gates.toml.example gates.toml       # paste in what jog.py printed
python3 tools/cross.py hall --calibrate
```

Park the robot in the living room near the doorway first. The sweep tries
ascending run-up distances and stops at the first that crosses. Shortest matters,
because the run-up has to fit in the space the furniture left.

Then put that number in `gates.toml` as `approach_distance`, and:

```bash
python3 tools/cross.py hall            # one crossing
python3 tools/cross.py hall --dry-run  # rehearse, nothing moves
```

## Working without the robot

```bash
python3 -m pytest            # 95 tests, no hardware needed
python3 tools/simulate.py    # run the maneuver against the simulator
```

`hub/simulator.py` models a differential drive plus a threshold that needs a
minimum speed at the lip — so run-up distance determines success, exactly the
relationship the real calibration is looking for. The maneuver state machine is
developed there, at hundreds of runs per second, rather than by ramming the real
robot into a door frame.

## Layout

```
hub/
  spec.py        every siid/piid/aiid for this model, named
  transport.py   miIO/MIoT over UDP 54321, with dry-run and rate limiting
  device.py      typed facade: status, settings, cleaning, remote control
  telemetry.py   pose parsing (format unconfirmed, so deliberately tolerant)
  geometry.py    gate maths -- pure, and tested exhaustively
  crossing.py    the crossing maneuver state machine
  simulator.py   differential drive + threshold model, for offline development
  gates.py       gates.toml loading
tools/
  probe.py       Phase 0 discovery
  jog.py         manual keyboard control + gate capture
  cross.py       run or calibrate a crossing
  simulate.py    the maneuver against the simulator
docs/            research findings and design
```

## Safety

Three invariants the code keeps, and the tests enforce:

1. **The robot is never left in remote mode** — teardown runs in a `finally`,
   plus a belt-and-braces check afterwards.
2. **Beaching is never pushed through** — high-centred, it stops and reverses
   clear instead of spinning the wheels.
3. **Takeover only happens next to the gate** — there is no path planning here,
   so a robot that is not already staged is left alone.

Keep the stock app installed. It is the escape hatch and the ground truth.

The hub holds your Mi credentials and device token — never expose it to the
internet; use Tailscale.

## Documents

| | |
|---|---|
| [`docs/00-decisions.md`](docs/00-decisions.md) | Settled decisions, Proxmox notes, observed threshold behaviour |
| [`docs/01-protocol-reference.md`](docs/01-protocol-reference.md) | Device, transports, the map pipeline, full control surface |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Hub + thin-client design and why |
| [`docs/03-gate-assist.md`](docs/03-gate-assist.md) | The crossing maneuver, in detail |
| [`docs/04-dev-environment.md`](docs/04-dev-environment.md) | Dev/test setup, replay harness, simulator |
| [`docs/05-roadmap.md`](docs/05-roadmap.md) | Phased plan |
