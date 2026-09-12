# Protocol Reference — Xiaomi Robot Vacuum S20+

Everything in this document was derived from the published MIoT spec, from open-source
integrations that already talk to this device family, and from community reports on the
exact model. Items marked **[UNVERIFIED]** must be confirmed against your physical robot in
Phase 0 before any code depends on them.

## 1. Device identity

| Field | Value |
|---|---|
| Marketing name | Xiaomi Robot Vacuum S20+ |
| MIoT model | `xiaomi.vacuum.b108gl` |
| Spec URN | `urn:miot-spec-v2:device:vacuum:0000A006:xiaomi-b108gl:1` |
| Sibling model | `xiaomi.vacuum.d106gl` (S20, non-plus) |
| Platform family | **ijai** (this determines the map format) |
| Rated climb height | 20 mm |

The S20 family is *not* a Dreame or Roborock OEM. It is an **ijai** platform device, which
matters enormously: it decides which map decoder applies, and it rules out Valetudo.

## 2. Transport

Two independent channels. We use both, for different jobs.

### 2a. Local — miIO / MIoT over UDP 54321

The standard Xiaomi binary protocol: handshake for the device clock, then AES-encrypted
JSON-RPC payloads keyed by the 16-byte device token.

```
{"id":1,"method":"get_properties","params":[{"did":"x","siid":2,"piid":1}]}
{"id":2,"method":"set_properties","params":[{"did":"x","siid":2,"piid":8,"value":4}]}
{"id":3,"method":"action","params":{"did":"x","siid":2,"aiid":13,"in":[{"piid":13,"value":"{\"room\":[5]}"}]}}
```

This gives **all control and all telemetry**. It does not give the map.

- The token is obtained once from your Mi account (a token-extractor tool), then stored.
  It is stable until the robot is re-paired to Wi-Fi.
- Local control on `b108gl` is **confirmed working** by an openHAB user who drove cleaning,
  room selection and settings entirely over LAN. One Home Assistant user reported
  `Unable to discover the device`, so treat reachability as a Phase 0 check, not a given.
- Note the argument-encoding trap: action inputs are **JSON strings nested inside JSON**,
  so they need escaping exactly as shown above. This bit several community implementers.

### 2b. Cloud — Xiaomi account API (map only)

The map is never served over LAN. The robot uploads it to Xiaomi object storage and exposes
only the *filename* locally. To render a map you must:

1. Log in to `account.xiaomi.com` (3-step: `serviceLogin` → `serviceLoginAuth2` → location
   redirect). 2FA is common and interactive.
2. Call the region API host (`{country}.api.io.mi.com/app/...`) with RC4-encrypted,
   HMAC-SHA256-signed parameters.

Endpoints we need:

| Purpose | Endpoint |
|---|---|
| Homes | `/v2/homeroom/gethome` |
| Devices in home (gives `did`, `mac`, `model`, owner id) | `/v2/home/home_device_list` |
| **Signed map download URL** | `/v2/home/get_interim_file_url_pro` |

The map URL call takes:
```json
{"obj_name": "<user_id>/<device_id>/<map-obj-name>"}
```
and returns a short-lived signed URL in `result.url`.

## 3. The map pipeline (the hard part)

This is the full chain. Each step is a place where an implementation silently produces
garbage, so build it with fixtures and unit tests from day one.

```
siid 7 / piid 1  (map-obj-name)        ← local MIoT poll
        ↓
POST /v2/home/get_interim_file_url_pro ← cloud, signed
        ↓
GET <signed url>                       → base64 text blob
        ↓
AES-128-ECB decrypt, PKCS#7 unpad      → an ASCII *hex string*
        ↓
bytes.fromhex(...)                     → zlib stream
        ↓
zlib.decompress(...)                   → protobuf bytes
        ↓
RobotMap.ParseFromString(...)          → structured map
```

### 3a. Key derivation

The AES key is derived per-device:

```
mac12      = device_mac.lower() with ':' removed          # 12 chars
modelTail  = model.split('.')[-1]                          # "b108gl"
             left-pad to 4 with '0' if shorter; if longer, take the LAST 4 → "08gl"
tempKey    = mac12 + modelTail                             # 16 chars = AES-128 key (utf-8)

plain      = f"{wifi_sn}+{owner_id}+{device_id}"
aeskey     = base64( AES-128-ECB-encrypt(plain, tempKey) )
mapKeyHex  = md5(aeskey).hexdigest()                       # 32 hex chars

→ actual map AES key = bytes.fromhex(mapKeyHex)            # 16 bytes
```

### 3b. `wifi_sn` — the known S20 landmine

`wifi_sn` is a required key input and it is the single most likely thing to break.

- The reference ijai implementation reads it from **siid 7 / piid 45**, splits on `,` and
  takes index 11. **That piid is not in the published `b108gl` spec** (service 7 stops at
  piid 9), so on the S20+ it must be sourced elsewhere — most likely the
  `get-system-info` action (**siid 6 / aiid 16**, returns `common-params`) or
  `siid 1 / piid 5` (Serial Number). **[UNVERIFIED — Phase 0 probe target #1]**
- On the S20 (`d106gl`) the value has a **non-standard format containing a slash**, e.g.
  `57054/B2AE7F5NE03300` — 20 characters, not alphanumeric. Reference implementations
  hardcode an 18-char `isalnum()` check and reject it. Once the raw value is passed through
  unmodified, maps decrypt correctly. **Do not sanitise this string.** Accept 10–25 chars,
  allow `/`, and feed it to the KDF byte-for-byte.

### 3c. `RobotMap` protobuf — fields that matter

```proto
uint32                mapType      = 1;
MapExtInfo            mapExtInfo   = 2;   // mapVersion, carpetOffsetInfo, boundaryInfo
MapHeadInfo           mapHead      = 3;   // sizeX, sizeY, minX/minY/maxX/maxY, resolution
MapDataInfo           mapData      = 4;   // bytes: 1 byte per pixel
repeated AllMapInfo   mapInfo      = 5;   // saved/permanent maps
DeviceHistoryPoseInfo historyPose  = 6;   // the cleaning path
DevicePoseDataInfo    chargeStation= 7;   // x, y, phi
DeviceCurrentPoseInfo currentPose  = ...; // live position
RoomDataInfo          roomDataInfo = ...; // roomId, roomName, per-room clean preferences
DeviceAreaDataInfo    ...               ; // virtual walls / no-go areas
ObjectDataInfo        ...               ; // AI-detected obstacles: type, x, y, photo url
FurnitureDataInfo     ...               ;
```

`mapData.mapData` is an 8-bit-per-pixel raster, row-major, **origin bottom-left** (flip Y
when drawing). Pixel values:

| Value | Meaning |
|---|---|
| `0x00` | outside / unknown |
| `0x01` | scanned floor |
| `0x02` | newly discovered area |
| `0xFF` | wall |
| `10–59` | room id (value − 10) |
| `60–109` | room id, currently selected |

World coordinates come from `mapHead`: `world_x = minX + px * resolution`.

`ObjectDataInfo` is worth special attention — the robot already stores detected obstacles
with a type id, world coordinates and a photo URL. If the S20+ populates it, that is a
free head start for the obstacle work. **[UNVERIFIED — Phase 0 probe target #2]**

## 4. Complete control surface (`b108gl`)

Only the entries we actually use are listed; `r/w/n` = read/write/notify.

### Service 2 — `vacuum`

| iid | Name | Access | Values |
|---|---|---|---|
| p1 | Status | r,n | 1 Idle, 2 Charging, 3 BreakCharging, 4 Sweeping, 5 Paused, 6 GoCharging, **7 Remote**, 8 Charged, 9 Mapping, 10 Updating |
| p2 | Device Fault | r,n | uint32 bitfield |
| p3 | Sweep Mop Type | r,w,n | 1 Sweep, 2 Mop, 3 Sweep+Mop, 4 Sweep then Mop |
| p4 | Sweep Type | r,w,n | 1 Global, 4 Area, 5 Mapping, 6 GoCharging, **7 RemoteControl**, 8 SelectRoom, 9 CustomClean |
| p8 | Suction Level | r,w,n | 1 Silent, 2 Basic, 3 Strong, 4 Full Speed |
| p9 | Mop Water Output | r,w,n | 0 Off, 1–3 |
| p11 | **Restricted Sweep Areas** | r,w,n | string — no-go zones |
| p12 | **Restricted Walls** | r,w,n | string — virtual walls |
| p20 | **Carpet Cleaning Method** | r,w,n | 0 Self-Adaption, 1 Avoid, 2 Ignore |
| p21 | **Carpet Discriminate** | r,w,n | bool |
| A8 | Get Room Configs | in p13 → out p14 | |
| A10 | Set Room Clean Configs | in p14 → out p14 | per-room fan/water/mode/times |
| A11 / A12 | Split Room / Merge Rooms | in p15 → out p15 | |
| A13 | **Start Room Sweep** | in p13 | `{"room":[5]}` |
| A1/A2/A6 | Start / Stop / Pause | — | |

### Service 6 — `vacuum-extend`

| iid | Name | Access |
|---|---|---|
| p6 | **carpet-boost** | r,w,n |
| p7 | carpet-avoidance | r,n |
| p10 | room-info | r,n |
| p11 | sweep-break-switch (breakpoint resume) | r,w,n |
| p13 | fault-index | r,n |
| A1 | **continue-sweep** (resume) | — |
| A2 | **remote-control** (enter remote mode) | — |
| A9/A10/A11/A12 | **start-remote-up / -left / -right / -down** | — |
| A13/A14 | **stop-remote / exit-remote** | — |
| A16 | get-system-info → `common-params` | — |

`room-info` (6/p10) returns a table like:
```json
{"version":2,"room_attrs":[
  ["id","room_name","fan_level","water_level","clean_mode","clean_times","mop_mode","on"],
  [3,"Lounge",2,1,3,1,0,1],
  [4,"Kitchen",3,2,3,1,0,1]]}
```

### Service 7 — `vacuum-map`

| iid | Name | Access |
|---|---|---|
| p1 | **map-obj-name** | r,n |
| p2 | trajectory-obj-name | r,n |
| p4 | **vacuum-position** | r,n |
| p5 / p6 | permanent-map / permanent-map-id | r,n |
| p8 | **carpet-obj-name** (carpet layer) | — |
| A2 / A3 | delete- / set-permanent-map | in p6 |
| A5 | auto-room-partition | → p7 |
| A6 | set-map-name | in p7 |
| A9 | get-one-history-map | in p7 → p7 |

## 5. What does **not** exist

Stated plainly, because it constrains the whole obstacle feature:

- **There is no torque, wheel-speed, climb-mode, or suspension property.** Nothing in the
  spec lets an app make the motors push harder.
- There is no obstacle-avoidance sensitivity setting.
- There is no explicit mop-lift command (lift is automatic on carpet detection).
- **Valetudo does not support this robot.** Its ~49-model support list covers Dreame,
  Roborock, MOVA and Eureka platforms; ijai is absent, and there is no published exploit
  chain for the S20 family. Rooting is not an available path — and attempting it would
  risk bricking a working robot for no gain, since local MIoT already gives us control.

The obstacle feature therefore has to be built at the **navigation** layer, out of the
remote-control primitives. Section 3 of `03-gate-assist.md` explains how, and what that
realistically buys.
