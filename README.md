# XiaomiS20-app

An alternative control app for the **Xiaomi Robot Vacuum S20+** (`xiaomi.vacuum.b108gl`),
built because the stock Xiaomi Home app is unreliable and awkward for map work.

## Goals

- **Better map control** — responsive live map, sane no-go/virtual-wall editing, room
  management that does not fight back
- **Real carpet control** — per-room and per-zone carpet policy, applied automatically
- **Gate Assist** — supervised, learning threshold crossing so the robot stops getting
  stuck on the high doorway
- **iOS eventually** — web client first, native later, same backend throughout

## Status

Planning. No code yet. See `docs/`.

## Documents

| | |
|---|---|
| [`docs/01-protocol-reference.md`](docs/01-protocol-reference.md) | Device, transports, the map pipeline, full control surface, and what does not exist |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Hub + thin-client design and why |
| [`docs/03-gate-assist.md`](docs/03-gate-assist.md) | The threshold-crossing feature, honestly scoped |
| [`docs/04-dev-environment.md`](docs/04-dev-environment.md) | Dev/test setup, replay harness, simulator, Phase 0 probe |
| [`docs/05-roadmap.md`](docs/05-roadmap.md) | Phased plan with exit criteria |

## Start here

`docs/05-roadmap.md` → Phase 0. Nothing else should be built until the probe script answers
its six questions.

## Security

The hub holds your Mi account credentials and the robot's device token. Never expose it to
the internet — use Tailscale. `fixtures/`, `secrets/` and tokens are gitignored; keep them
that way, since recorded fixtures contain the AES key inputs for your map data.
