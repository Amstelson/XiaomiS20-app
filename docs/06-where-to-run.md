# Where to run it

Two different jobs, two different answers.

## Now: your laptop

For the experiments — probe, jog, calibrate — **run from your laptop on the
normal home Wi-Fi.** Reasons, in order of how much they matter:

1. **You need to be standing at the doorway.** `tools/jog.py` is hand-driving
   the robot while watching it meet the lip. You want your hands on the keys
   and your eyes on the robot, with Ctrl-C half a second away.
2. **Don't debug two things at once.** If the first crossing attempt misbehaves
   you want to be sure it is the robot, not container networking.
3. **Nothing needs to persist yet.** There is no daemon. Every tool is a
   one-shot run that exits.

Requirements are just: Python 3.11+, `pip install -r requirements.txt`, and the
laptop on the same subnet as the robot — which normal home Wi-Fi already is.

Confirm it before walking to the doorway:

```bash
python3 tools/doctor.py
```

That proves the handshake and the token, and measures round-trip latency, which
is the number that decides whether closed-loop control works from where you are.

## Later: an LXC on Proxmox

Move it once there is something worth running unattended — the watcher that
notices a stall near the gate during a normal clean and intervenes by itself.
That has to be up whenever the robot might clean, which your laptop is not.

An unprivileged container is plenty; no Docker, no nesting, no passthrough.

```bash
# on the Proxmox host
pct create 120 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname vacuum-hub \
  --cores 2 --memory 1024 --swap 512 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 \
  --onboot 1

pct start 120 && pct enter 120
```

```bash
# inside the container
apt update && apt install -y python3 python3-venv git
git clone https://github.com/Amstelson/XiaomiS20-app.git /opt/vacuum-hub
cd /opt/vacuum-hub
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.toml.example config.toml   # add IP and token
.venv/bin/python tools/doctor.py
```

Debian 12 ships Python 3.11, which is the minimum python-miio needs.

### The one thing to get right: networking

The hub speaks **UDP straight to the robot**, so:

- **Bridged, not NAT.** `--net0 ... bridge=vmbr0` puts the container on your LAN
  as a first-class host. NAT breaks the miIO handshake.
- **If the robot is on an IoT VLAN**, give the container a leg on that VLAN:
  `--net0 name=eth0,bridge=vmbr0,tag=20,ip=dhcp`. Routing unicast across subnets
  can work once the robot's IP is pinned, but discovery will not — so pin the
  robot's address with a DHCP reservation either way.
- `tools/doctor.py` reports a warning when the container and the robot are on
  different /24s, so you will not have to guess.

### Why Proxmox rather than the Pi

Both work. Proxmox wins on **snapshots**: this is software that drives a
physical machine around your home, and being able to roll the hub back to the
last known-good state in one click is worth having. Take a snapshot before each
deployment of a changed maneuver.

Keep the Pi as a second opinion — it is useful for checking whether a latency or
reachability problem is the network or the host.

## What runs where, in the end

| | Laptop | LXC |
|---|---|---|
| `doctor.py`, `probe.py` | yes | yes |
| `jog.py` — hand-driving at the doorway | **yes** | awkward; you are not there |
| `cross.py --calibrate` | **yes** — you want to watch | later, once trusted |
| the automatic watcher (not built yet) | no | **yes** |
| web UI and map rendering (later) | no | **yes** |

The tools take `--ip` and `--token`, so nothing is tied to one host. Run them
from wherever the job suits.
