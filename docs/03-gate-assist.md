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

- hitting the lip at the wrong geometry, so it either slews off or grounds out on the crest;
- hitting a *particular spot* on the threshold that happens to be higher, worn, or has a
  screw head, when 20 cm to the left it would go over fine;
- approaching from a standstill with no run-up because the bumper triggered a slow-down;
- the robot making one attempt, marking it impassable, and re-routing for the rest of the run.

Every one of those is a navigation problem, and navigation is exactly what the remote-control
actions give us. The realistic outcome is **converting "fails sometimes and abandons the
room" into "crosses reliably, occasionally on the second attempt"** — which is what you asked
for. Expect to measure this, not assume it.

## 1a. The observed failure taxonomy — and why it drives the design

The threshold in question fails in **two different ways**, and **only in one direction**:

| Mode | What it looks like | What it means physically |
|---|---|---|
| **Beach** | Rides up, strands on the crest, wheels spinning | High-centring. The chassis or the mop assembly grounds out on top before the drive wheels regain purchase. |
| **Refusal** | Hits the lip and turns away without committing | The robot's own bumper/cliff logic aborts the attempt before the climb starts. |

Directional asymmetry means one side of the threshold has a steeper or squarer lip than the
other — which is exactly the kind of thing per-direction learning captures for free.

Three consequences, and they shape everything below:

**1. The two modes want partially opposite remedies.** A refusal is helped by a square,
perpendicular, committed approach with run-up. A beach is often helped by the *opposite* —
a slightly oblique entry, so the wheels meet the lip sequentially and the robot pitches over
it rather than lifting flat and grounding out in the middle. A single hand-tuned rule cannot
serve both. This is the strongest argument for the learning approach: record which mode
occurred, and let per-spot, per-direction statistics discover which entry geometry works
*here*, rather than encoding a guess.

**2. Outcomes must be typed, not boolean.** `crossed | beached | refused | faulted` — not
`success | failure`. A beach and a refusal at the same spot are different evidence and must
update different arms of the policy. This is a schema decision that is painful to retrofit,
so it goes in from the first commit.

**3. Beaching needs an escape routine, not persistence.** When high-centred, continuing to
drive forward spins the wheels against no traction. It achieves nothing, and on this model it
is a plausible route to the reported mop-bracket detachment, as well as needless drive-motor
load. So:

> **Beach detection is a hard interrupt.** If the pose is straddling the gate line and static
> for more than ~2 s, stop driving forward immediately, reverse out along the entry vector,
> and only then consider another attempt at a different spot.

Discriminating the two modes is straightforward with the geometry we already have — take the
signed distance from the gate line:

- **refusal** — motion stops while the signed distance is still clearly negative (short of
  the line), usually followed by a heading change;
- **beach** — signed distance sits near zero (straddling) and the pose freezes.

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
    obliquity_set: list[float]  # entry angles to explore, e.g. [0°, ±12°, ±22°]
    hard_direction: int | None  # set once learned; the side that actually fails
    enabled: bool
```

Crossing points are sampled along `a→b`, inset from both ends so the robot never tries to
climb at a door jamb. The policy's arms are the cross product
`(spot × direction × obliquity)`; each carries a running record, typed by outcome.

Because this threshold only fails in one direction, the engine should learn to stop
intervening in the easy direction entirely — a gate that succeeds unassisted 20 times running
in one direction drops to passive monitoring that way, and keeps its attempt budget for the
side that needs it.

## 4. The maneuver

```
                    ┌─ triggered by ─────────────────────────────┐
                    │  • predicted: pose heading toward a gate    │
                    │  • reactive:  stall or fault near a gate    │
                    └────────────────────────────────────────────┘
                                     ↓
  1. PAUSE            2/A6, confirm status → 5 Paused
  2. SNAPSHOT         record pose, suction, water, mop type
  3. PREPARE          mop water → Off (2/p9 = 0); suction → Full Speed (2/p8 = 4);
                      optionally Sweep Mop Type → Sweep (2/p3 = 1) — see §6, this is the
                      lever most likely to matter for beaching
  4. ENTER REMOTE     6/A2, confirm status → 7 Remote
  5. SELECT ARM       best-ranked (spot, obliquity) for this direction
  6. BACK OFF         drive to approach_distance behind the gate, normal to a→b
  7. ALIGN            rotate to the arm's target heading (gate normal ± obliquity), ±5°
  8. DRIVE            issue -up repeatedly at the command refresh rate
  9. EVALUATE         signed distance past the line > clearance   → CROSSED
                      static & straddling the line (> 2 s)        → BEACHED, escape now
                      static & short of the line  (> 3 s)         → REFUSED
 10. ESCAPE           on BEACHED: stop, reverse along the entry vector until clear
 11. RETRY            next-best arm, up to max_attempts; then give up and flag the gate
 12. RESTORE          exit-remote (6/A14), restore settings, continue-sweep (6/A1)
```

Every step is guarded. The engine holds a hard watchdog: if anything takes longer than a
configured ceiling, or the robot reports a fault, it unconditionally runs step 12 and reports
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
  entry_obliquity REAL,     -- 0 = square to the lip; the beach/refusal trade-off
  outcome TEXT,             -- crossed | beached | refused | faulted
  duration_ms INTEGER, ts INTEGER
);
```

Spot ranking is a Beta posterior over success rate (Thompson sampling, a few lines of code —
no ML stack). The arms are `(spot, direction, obliquity bucket)`, which is what lets the
policy resolve the conflict in §1a empirically: if square entry keeps beaching at this
threshold, the oblique arms win on their own, without anyone deciding in advance.

The first few runs explore the threshold; after that the engine goes straight to the geometry
that actually works, in the direction that actually needs help. Thresholds are not uniform,
and this is precisely the knowledge the stock firmware throws away after every run.

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
