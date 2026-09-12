# Gate Assist — consistent threshold crossing

## 1. The actual problem

One threshold: the doorway between the living room and the hall. Curved
aluminium with a slight ramp.

- **Hall → living room: fine.** Always has been.
- **Living room → hall: fails.** The robot hesitates at the lip, creeps over
  too gently, and strands halfway across.

The decisive detail is the history. **The robot used to cross it.** It would
meet the threshold, back off a little, build speed, and carry over on momentum —
the correct maneuver, chosen by itself. It stopped doing that after furniture
was built around the opening. Now it goes over too carefully and beaches.

So this is not a missing capability. The firmware can do it and has done it.
Something in the new geometry suppresses the behaviour — most plausibly the
navigation planner treating the narrowed opening as a tight space and
derating its approach, or simply no longer having the clear run-up it used to
take. Either way:

> **The job is not to invent a maneuver. It is to execute the maneuver the
> robot has stopped choosing.**

That is a much easier problem than the general one, and it is why remote-control
mode is the right tool: it takes the planner's caution out of the loop entirely.

### What this means for expectations

We still cannot increase motor torque — no such property exists. But we do not
need to: the threshold is demonstrably crossable by this robot at this
threshold, because it used to happen daily. We only need to reproduce the
conditions.

The one caveat worth keeping honest: if the furniture has left less clear floor
on the living-room side than the robot needs for a run-up, no amount of software
recovers that. The engine detects and reports exactly this case rather than
grinding away at it — see `could not open Nm of run-up` in the notes. If that
is what comes back, the answer is to move the furniture a little or add a ramp,
and we will know within one calibration run.

## 2. What we control

There is no throttle. Remote control gives direction commands only:

| Action | Address |
|---|---|
| enter remote mode | `6 / A2` |
| forward / back / left / right | `6 / A9` `6 / A12` `6 / A10` `6 / A11` |
| stop / exit | `6 / A13` `6 / A14` |
| resume the interrupted clean | `6 / A1` |

So momentum has exactly two levers:

1. **Run-up distance** — how far back the robot starts before it meets the lip.
   This is the dominant parameter and the one worth calibrating.
2. **An unbroken forward command stream** — if the firmware needs the direction
   command re-issued, gaps between commands mean coasting. `command_interval`
   controls the cadence; `--no-reissue` covers the case where one command drives
   continuously. Which it is gets settled by `tools/probe.py --remote-test`.

Secondary levers, each a single property write and each worth an A/B once the
main one works: mop water off (`2/p9`), suction (`2/p8`), and sweep-only mode
(`2/p3 = 1`) in case it unloads the mop assembly — relevant here because the
assembly sits at the back and the reported S20+ failure mode is the mop bracket
detaching on exactly this kind of obstacle.

## 3. The maneuver

Implemented in `hub/crossing.py`.

```
  1. CHECK        refuse unless the robot is already staged near the gate
  2. SNAPSHOT     save suction, water, mop mode
  3. PREPARE      water off, suction full (optionally sweep-only)
  4. PAUSE        if a clean is running
  5. ENTER REMOTE 6/A2, confirm status -> 7 Remote
  6. STAGE        move to exactly `runup` metres back from the line --
                  reversing if too close, creeping forward if too far back
  7. ALIGN        rotate onto the crossing heading, within 5 deg
  8. DRIVE        hold forward, sampling pose, and classify how it ends:
                    past the line by `clearance`        -> CROSSED
                    climbed on, then stalled            -> BEACHED
                    stalled without ever reaching it    -> REFUSED
                    fault / out of time                 -> FAULTED / TIMEOUT
  9. ESCAPE       on BEACHED, stop and reverse clear. Never push through.
 10. RESTORE      exit remote, restore settings, resume the clean
```

Step 6 matters more than it looks. Staging to the *exact* run-up distance in
both directions is what makes the calibration sweep honest — parking further
back than requested would silently give every short attempt a long run-up, and
the sweep would report a shorter working distance than the robot really needs.

### Classification

Beaching and refusal are told apart by **whether the robot ever climbed onto the
lip**, not by where it happens to be sitting when it stops — a refusal can stop
close to the line too. `best_signed >= lip_reach` means it got on; stalling
after that is beaching, stalling without it is a refusal. The two want different
responses, so the distinction earns its keep.

### Safety invariants

1. **The robot is never left in remote mode.** Teardown runs in a `finally`,
   and again as a belt-and-braces check afterwards. Tested by killing telemetry
   mid-run.
2. **Beaching is never pushed through.** High-centred, driving on just spins the
   wheels against no traction — pointless, hard on the drive motors, and a
   plausible route to the mop-bracket problem. Detection triggers an immediate
   reverse escape.
3. **Takeover only happens next to the gate.** There is no path planning here,
   so the engine refuses to drive a robot that is not already staged rather than
   blunder across a room that now has furniture in it.

## 4. Calibration is the deliverable

The single most useful number is **the shortest run-up that reliably crosses**.
Shortest, not merely sufficient — because the run-up has to fit in whatever
space the furniture left.

```
python3 tools/cross.py hall --calibrate
```

This sweeps ascending run-up distances and stops at the first that works. Set
that (plus a little margin) as `approach_distance` in `gates.toml`.

## 5. Later: making it automatic and self-improving

Once a crossing works reliably by hand, the engine gets wired to a watcher that
notices the robot stalling near the gate during a normal clean and intervenes
on its own. At that point the attempt log becomes useful:

```sql
CREATE TABLE crossing_attempts (
  id INTEGER PRIMARY KEY,
  gate_id TEXT, lateral REAL, runup REAL, obliquity REAL,
  water INTEGER, suction INTEGER, sweep_mop_type INTEGER,
  outcome TEXT,            -- crossed | beached | refused | faulted | timeout
  reached REAL, duration_ms INTEGER, ts INTEGER
);
```

Ranking over `(lateral, runup)` by a Beta posterior — Thompson sampling, a few
lines, no ML stack — lets the engine settle on the spot and run-up that actually
work at this threshold, and adapt if the furniture moves again. Thresholds are
not uniform along their width, and this is exactly the knowledge the stock
firmware throws away after every run.

Worth building only after the manual crossing is solved. The parameters come
first; the automation is the easy part.
