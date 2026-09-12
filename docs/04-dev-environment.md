# Development & testing environment

The goal of this setup is that **almost all development happens without the robot**, because
a hardware-in-the-loop iteration cycle is measured in minutes and involves walking to a
doorway. You want the robot for validation, not for iteration.

## Four layers

### Layer 1 — Hub, running locally

Python 3.12, `uv` for dependency management, FastAPI + uvicorn with reload.

```
uv venv && uv pip install python-miio protobuf pycryptodome pillow numpy \
                          fastapi uvicorn httpx pydantic aiosqlite pytest
```

Runs on your laptop during development; the same container later runs on the Pi.

### Layer 2 — Web client (the dev UI, and the design prototype for iOS)

Vite + React + TypeScript. The map view is a `<canvas>` layer stack: raster → rooms →
path → no-go/walls → gates → robot. Tailwind for chrome.

This is deliberately the *same* UX you eventually want on iOS, built where iteration is
instant. Keep all map maths (coordinate transforms, hit-testing, gate geometry) in a
framework-free `map-core` TS package so it survives the move to React Native.

### Layer 3 — Replay harness ⭐ the piece that makes this project tractable

A recorder wraps the transport and writes every exchange to disk:

```
fixtures/
  2026-09-12T19-04_kitchen-run/
    properties.jsonl     # timestamped property polls
    actions.jsonl        # everything we sent
    map_0001.bin ...     # raw encrypted blobs, exactly as downloaded
    meta.json            # model, mac, did, owner_id, wifi_sn
```

Then a `ReplayTransport` implements the same interface from those files. With it you can:

- develop and unit-test the **map decoder** against real blobs, offline, in milliseconds;
- replay a real cleaning run through the **gate engine** and assert it detects the stall;
- keep every past bug as a regression fixture.

Capture fixtures on day one, before writing any feature code. Every hour of robot time
becomes reusable.

> Fixtures contain your MAC, device id, owner id and `wifi_sn` — the AES key inputs. Keep
> `fixtures/` and any token/credential file out of git from the very first commit.

### Layer 4 — Kinematic simulator

A ~200-line differential-drive model: pose, heading, wheel speeds, plus a scripted
"threshold at x=2.4 m that blocks unless |heading error| < 12° and the chosen spot is not
in the bad band". It exposes the same interface as the real device.

This is how you develop the Gate Assist state machine — hundreds of runs per second, with
deterministic seeds, testing alignment logic, retry budgets, watchdog timeouts and the
restore path, without ever ramming the actual robot into your door frame. When the state
machine is green in the simulator, you take it to the hardware for tuning constants only.

## Test pyramid

| Level | What | Where |
|---|---|---|
| Unit | KDF, AES/zlib chain, protobuf→model, coordinate transforms, gate geometry | pure, fast |
| Fixture | decoder against recorded blobs; poller against recorded runs | replay harness |
| Simulation | Gate Assist state machine, watchdogs, restore-on-fault | kinematic sim |
| Integration | real robot, `--dry-run` (log intended actions, send nothing) | hardware |
| Acceptance | real crossings, N attempts, success rate measured | hardware |

## Hardware-in-the-loop safety rules

Write these into the code, not the wiki:

1. **Global dry-run flag.** Every outbound action passes one gate that can turn all writes
   into logs. Default it to on for new code paths.
2. **Restore-first.** The restore path (exit remote → restore settings → resume) is written
   and tested before the maneuver that needs it.
3. **Watchdog everywhere.** No remote-mode session without a hard ceiling that fires restore.
4. **Rate-limit the transport.** Newer Xiaomi firmware is unhappy with aggressive polling;
   cap it, and back off on errors.
5. **Never auto-apply map edits.** No-go zones and room splits are destructive and annoying
   to undo in the stock app. Always confirm, and snapshot the previous value first.
6. **Keep the stock app installed.** It is your escape hatch and your ground truth.

## Phase 0 probe script — run this first

Before any of the above matters, one script answers whether the plan holds. It should:

1. Connect locally over UDP 54321 with the token; confirm handshake and clock sync.
2. Dump **every** property in services 1–14, including piids not in the published spec
   (probe 7/p10–7/p50) — this is the hunt for `wifi_sn`.
3. Call `get-system-info` (6/A16) and dump `common-params`.
4. Log in to the cloud, resolve `did`/`mac`/`owner_id`, fetch `map-obj-name`, download the
   blob, and attempt the full decrypt→zlib→protobuf chain. **This proves or kills the map
   feature.**
5. Poll `vacuum-position` (7/p4) for 60 s at 5 Hz and report the true update rate and jitter.
6. With the robot in open floor: enter remote mode, issue a single `-up`, observe whether it
   drives continuously or nudges, `stop-remote`, `exit-remote`. Measure command latency.
6b. **Drive it at the problem threshold under remote control.** This is the decisive
   experiment. The robot currently "hits it and turns away", so the question is whether that
   refusal is the *navigation planner* declining the obstacle — in which case remote mode
   bypasses it and the feature works — or the *bumper/cliff firmware*, which remote mode will
   not override. Try both directions, and try an oblique entry, and record what happens.
   Have a hand ready to catch it.
7. Start a clean, pause it, enter and exit remote mode, `continue-sweep`, and confirm the
   clean resumes rather than restarting.

Steps 4, 5, 6, 6b and 7 resolve every **[UNVERIFIED]** item that the design depends on.
Nothing else should be built until they have answers — and 6b in particular determines
whether Gate Assist is a strong feature or a modest one.

## Path to iOS

Ship order: **web client → PWA on your phone → native**.

The web client installed to the iOS home screen is a genuinely usable app and costs nothing
extra. Use it while the functionality settles — which is exactly the sequencing you asked
for.

For the native app, **React Native (Expo)** is the recommendation over SwiftUI here, for one
reason: the map renderer and all its geometry is the bulk of the client work, and RN lets you
carry the `map-core` package and the canvas logic across unchanged. SwiftUI would mean
rewriting it, for a polish gain that does not matter in a personal app. Expo also gives you
TestFlight-free local installs via development builds.

Either way the app stays a thin client of the hub. Reaching it from outside the house is
**Tailscale**, not port-forwarding — the hub holds your Mi credentials and robot token and
must never be exposed to the internet.
