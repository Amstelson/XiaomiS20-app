# Gate Assist — consistent threshold crossing

This is the feature you actually want, and it is the one that needs the most honesty about
what is achievable. Read section 1 before anything else.

## 1. What we can and cannot do

**We cannot make the robot stronger.** There is no property, action or hidden command in the
`b108gl` spec that changes motor torque, wheel speed or suspension behaviour. Nothing at the
app layer can raise the 20 mm rating. If the threshold is physically beyond the robot, no
software fixes it and a small bevelled ramp is the honest answer.

**What we can do is change how it approaches, and refuse to give up.** Threshold failures on
these robots are rarely "not enough power at the limit" — they are:

- hitting the lip at an angle, so one wheel climbs and the other does not, and it slews off;
- hitting a *particular spot* on the threshold that happens to be higher, worn, or has a
  screw head, when 20 cm to the left it would go over fine;
- approaching from a standstill with no run-up because the bumper triggered a slow-down;
- the robot making one attempt, marking it impassable, and re-routing for the rest of the run.

Every one of those is a navigation problem, and navigation is exactly what the remote-control
actions give us. The realistic outcome is **converting "fails sometimes and abandons the
room" into "crosses reliably, occasionally on the second attempt"** — which is what you asked
for. Expect to measure this, not assume it.

## 2. The primitives we have

From service 6: `remote-control` (A2) to enter remote mode, then `start-remote-up` (A9),
`-left` (A10), `-right` (A11), `-down` (A12), `stop-remote` (A13), `exit-remote` (A14).
Status (2/p1) reports `7 Remote` while engaged; `continue-sweep` (6/A1) resumes the
interrupted clean, backed by `sweep-break-switch` (6/p11).

Position comes from `vacuum-position` (7/p4), polled locally.

Three things about these primitives are **[UNVERIFIED]** and decide the shape of the feature.
They are the first thing Phase 0 measures:

1. **Position poll rate and latency.** A control loop needs ≥2 Hz with <500 ms lag. If the
   property only updates once per second or is stale, the loop must run open-loop on timed
   maneuvers instead of closed-loop on position.
2. **Whether direction commands are continuous or impulse.** If `start-remote-up` means
   "drive until stopped", we can build momentum. If it is a fixed nudge, run-up is impossible
   and we fall back to spot-selection and retries.
3. **Whether remote mode can be entered mid-clean and `continue-sweep` genuinely resumes**
   the remaining plan rather than restarting the room.

If (2) comes back "fixed nudge", the feature still works — it just loses the run-up strategy
and leans on spot selection, which the data suggests is the bigger factor anyway.

## 3. The gate model

A **gate** is a user-drawn line segment on the map with a crossing direction:

```python
@dataclass
class Gate:
    id: str
    map_id: int                 # permanent-map-id this is anchored to
    a: Point; b: Point          # the threshold line, world coords (metres)
    bidirectional: bool
    approach_distance: float    # default 0.45 m — where the run-up starts
    clearance: float            # default 0.20 m — how far past to call it crossed
    max_attempts: int           # default 3
    strategy: Strategy          # PERPENDICULAR_RUNUP | SPOT_SEARCH | NUDGE
    enabled: bool
```

Crossing points are sampled along `a→b`, inset from both ends so the robot never tries to
climb at a door jamb. Each sample carries a running success record.

## 4. The maneuver

```
                    ┌─ triggered by ─────────────────────────────┐
                    │  • predicted: pose heading toward a gate    │
                    │  • reactive:  stall or fault near a gate    │
                    └────────────────────────────────────────────┘
                                     ↓
  1. PAUSE            2/A6, confirm status → 5 Paused
  2. SNAPSHOT         record pose, suction, water, mop type
  3. PREPARE          mop water → Off (2/p9 = 0); suction → Full Speed (2/p8 = 4)
  4. ENTER REMOTE     6/A2, confirm status → 7 Remote
  5. SELECT SPOT      best-ranked crossing point for this direction
  6. BACK OFF         drive to approach_distance behind the gate, normal to a→b
  7. ALIGN            rotate until |heading − gate_normal| < 5°
  8. DRIVE            issue -up repeatedly at the command refresh rate
  9. EVALUATE         crossed if signed distance past the line > clearance
                        success → record, go to 11
                        no progress for 3 s → abort this attempt
 10. RETRY            next-best spot, up to max_attempts; then give up and flag the gate
 11. RESTORE          exit-remote (6/A14), restore settings, continue-sweep (6/A1)
```

Every step is guarded. The engine holds a hard watchdog: if anything takes longer than a
configured ceiling, or the robot reports a fault, it unconditionally runs step 11 and reports
failure. **The robot must never be left parked in remote mode by a crashed engine** — that
is the single worst failure mode of this design, so the restore path is written first and
tested with injected faults.

## 5. Stall detection

A stall near a gate is: status is `4 Sweeping`, and the position has stayed inside a small
radius (~8 cm) for longer than a threshold (~4 s), and the pose is within ~0.6 m of a gate
line. Fault codes (2/p2, plus `fault-index` 6/p13) are a second, faster trigger once we have
catalogued which codes the robot emits when it beaches itself — Phase 0 should deliberately
strand it on the threshold a few times and record what it reports.

## 6. The part that makes this better than the stock app

**Per-spot learning.** Every attempt writes a row:

```sql
CREATE TABLE crossing_attempts (
  id INTEGER PRIMARY KEY,
  gate_id TEXT, spot_index INTEGER, direction INTEGER,
  approach_heading_err REAL, entry_speed_proxy REAL,
  mop_water INTEGER, suction INTEGER,
  outcome TEXT,             -- crossed | stalled | aborted | fault
  duration_ms INTEGER, ts INTEGER
);
```

Spot ranking is a Beta posterior over success rate (Thompson sampling, a few lines of code —
no ML stack). The first few runs explore the threshold; after that the engine goes straight
to the spot that actually works, in the direction that actually works. Thresholds are not
uniform, and this is precisely the knowledge the stock firmware throws away after every run.

Secondary levers worth A/B-ing once the harness exists, since each is one property write:
mop water off vs. on, suction level, and `Sweep Mop Type` (2/p3) set to `1 Sweep` — if that
retracts or unloads the mop assembly it may measurably improve clearance, which also
addresses the reported S20+ issue of the mop bracket detaching on thresholds.

## 7. Defensive complement

Gate Assist is opportunistic. The robust complement is **orchestration**: instead of letting
the robot free-roam the whole flat and discover the threshold mid-plan, the hub runs rooms
in an explicit sequence (2/A13 per room) and performs a deliberate assisted crossing between
them. Fewer unplanned encounters, and every crossing happens under supervision with a known
approach. Build Gate Assist first, then layer this on.
