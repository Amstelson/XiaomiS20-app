# Decisions

Recorded so later work does not re-litigate them.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Standalone Python hub.** Not a Home Assistant integration. | No HA in the house and no wish to adopt it. Buys a purpose-built API with no HA concepts leaking into the design. |
| D2 | **Hub runs as an LXC container on Proxmox.** Raspberry Pi is the fallback / field-test node. | Proxmox is already running and gives snapshots — valuable for software that drives a physical robot, because rollback is one click. |
| D3 | **Python 3.12 for the hub.** | `python-miio` already implements the miIO token crypto; every reference ijai map decoder is Python. The risky protocol work becomes porting, not inventing. |
| D4 | **Gate Assist must handle two distinct failure modes**, not one. | See `03-gate-assist.md` §1a — the observed behaviour is both beaching and refusal, on the same threshold, with directional asymmetry. |

## Proxmox deployment notes

The hub speaks **UDP 54321 directly to the robot**, so container networking matters:

- Use a **bridged** interface (`vmbr0`), not NAT. The hub must sit on the same L2 subnet as
  the robot — miIO handshake and discovery are unicast/broadcast UDP and do not survive NAT.
- If the robot is on a separate IoT VLAN, the hub needs an interface on that VLAN too.
  Routing UDP 54321 across subnets works for unicast once the IP is pinned, but discovery
  will not; pin the robot's IP by DHCP reservation and skip discovery.
- Unprivileged LXC is fine — no device passthrough is needed.
- Snapshot before every Gate Assist deployment. That is the point of running it here.

## Observed threshold behaviour (the design input)

One doorway: living room to hall. Curved aluminium with a slight ramp.

- **Hall -> living room: always fine.**
- **Living room -> hall: fails.** Hesitates at the lip, crosses too gently,
  strands halfway.
- **It used to work.** The robot would back off, build speed and carry over on
  momentum, entirely by itself.
- **It stopped after furniture was built around the opening.**

That history is the most valuable fact in the project: the capability exists in
the firmware and has been demonstrated at this exact threshold. The work is to
reproduce the conditions, not to invent a maneuver. See `03-gate-assist.md`.

| # | Decision | Rationale |
|---|---|---|
| D5 | **Threshold crossing is priority one. Maps second. Carpet later.** | User's call, and the threshold is the thing that breaks cleaning runs today. |
| D6 | **Run-up distance is the parameter to calibrate.** | Remote control offers no throttle, so run-up distance and an unbroken command stream are the only levers on momentum. |
