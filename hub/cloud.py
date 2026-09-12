"""Xiaomi cloud client.

Needed for two things: the per-device **token** that unlocks local control, and
later the **map blobs**, which are never served over LAN (see
docs/01-protocol-reference.md).

The account API is an odd one -- a three-step web login, then RC4-encrypted
request parameters signed with HMAC-SHA256 -- so the wire details are
documented inline rather than left to be rediscovered.

Two device listings are exposed, because they do not always agree:

* `list_devices()` uses `/home/device_list`, which returns everything on the
  account regardless of how it is grouped;
* `list_devices_by_home()` walks `/v2/homeroom/gethome` and
  `/v2/home/home_device_list`, which is what most tooling uses and what misses
  devices that are not filed under a home the account owns.

When the two disagree, the first is the one to believe.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

_LOG = logging.getLogger(__name__)

SERVERS = ["cn", "de", "us", "ru", "tw", "sg", "in", "i2"]

_LOGIN_URL = "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true"
_AUTH_URL = "https://account.xiaomi.com/pass/serviceLoginAuth2"
_JSON_PREFIX = "&&&START&&&"


class CloudError(RuntimeError):
    pass


class LoginFailed(CloudError):
    pass


class TwoFactorRequired(LoginFailed):
    def __init__(self, url: str):
        super().__init__(
            "This account needs two-factor confirmation. Open this URL, approve "
            f"the sign-in, then run the command again:\n  {url}"
        )
        self.url = url


@dataclass
class CloudDevice:
    did: str
    name: str
    model: str
    token: str
    local_ip: str | None = None
    mac: str | None = None
    online: bool = False
    server: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, entry: dict, server: str) -> "CloudDevice":
        return cls(
            did=str(entry.get("did", "")),
            name=entry.get("name", ""),
            model=entry.get("model", ""),
            token=entry.get("token", ""),
            local_ip=entry.get("localip") or None,
            mac=entry.get("mac") or None,
            online=bool(entry.get("isOnline")),
            server=server,
            raw=entry,
        )

    def describe(self) -> str:
        status = "online" if self.online else "offline"
        return (
            f"{self.name or '(unnamed)'}\n"
            f"    model: {self.model}\n"
            f"    ip:    {self.local_ip or 'unknown'}  ({status}, server {self.server})\n"
            f"    token: {self.token or 'not provided'}\n"
            f"    did:   {self.did}"
        )


# -- request crypto ----------------------------------------------------------
#
# Kept as free functions so they can be tested without a network or an account.


def generate_nonce(millis: int | None = None) -> str:
    """8 random bytes plus the current time in minutes, big-endian."""
    millis = int(time.time() * 1000) if millis is None else millis
    return base64.b64encode(
        os.urandom(8) + int(millis / 60000).to_bytes(4, byteorder="big")
    ).decode()


def signed_nonce(ssecurity: str, nonce: str) -> str:
    digest = hashlib.sha256(base64.b64decode(ssecurity) + base64.b64decode(nonce))
    return base64.b64encode(digest.digest()).decode()


def _rc4(key: str, data: bytes) -> bytes:
    """RC4 with the first 1024 bytes of keystream discarded, as Xiaomi does."""
    try:
        from Crypto.Cipher import ARC4
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise CloudError("pip install -r requirements.txt") from exc
    cipher = ARC4.new(base64.b64decode(key))
    cipher.encrypt(bytes(1024))
    return cipher.encrypt(data)


def encrypt_rc4(key: str, payload: str) -> str:
    return base64.b64encode(_rc4(key, payload.encode())).decode()


def decrypt_rc4(key: str, payload: str) -> bytes:
    return _rc4(key, base64.b64decode(payload))


def signature(url: str, method: str, nonce: str, params: dict[str, str]) -> str:
    parts = [method.upper(), url.split("com")[1].replace("/app/", "/")]
    parts += [f"{key}={value}" for key, value in params.items()]
    parts.append(nonce)
    return base64.b64encode(hashlib.sha1("&".join(parts).encode()).digest()).decode()


def encrypted_params(
    url: str, method: str, nonce: str, snonce: str, params: dict, ssecurity: str
) -> dict:
    """Build the RC4-encrypted, signed form body for an API call."""
    fields = dict(params)
    fields["rc4_hash__"] = signature(url, method, snonce, fields)
    fields = {key: encrypt_rc4(snonce, value) for key, value in fields.items()}
    fields["signature"] = signature(url, method, snonce, fields)
    fields["ssecurity"] = ssecurity
    fields["_nonce"] = nonce
    return fields


def api_url(server: str) -> str:
    prefix = "" if server == "cn" else f"{server}."
    return f"https://{prefix}api.io.mi.com/app"


def _strip_prefix(text: str) -> Any:
    if text.startswith(_JSON_PREFIX):
        text = text[len(_JSON_PREFIX) :]
    return json.loads(text)


# -- client ------------------------------------------------------------------


class XiaomiCloud:
    def __init__(self, username: str, password: str, *, timeout: int = 15) -> None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise CloudError(
                "The `requests` package is needed for cloud access. "
                "Run: pip install -r requirements.txt"
            ) from exc

        self.username = username
        self._password = password
        self.timeout = timeout
        self.user_id: str | None = None
        self.ssecurity: str | None = None
        self.service_token: str | None = None
        self._session = requests.Session()
        self._agent = (
            "Android-7.1.1-1.0.0-ONEPLUS A3010-136-"
            f"{''.join(str(int(x)) for x in os.urandom(13))[:13]} APP/xiaomi.smarthome"
            " APPV/62830"
        )
        self._device_id = "".join(
            "abcdefghijklmnopqrstuvwxyz"[b % 26] for b in os.urandom(6)
        ).upper()

    # -- login ---------------------------------------------------------------

    def login(self) -> str:
        """Authenticate, returning the user id the credentials resolved to."""
        sign = self._login_step1()
        location = self._login_step2(sign)
        self._login_step3(location)
        _LOG.info("logged in as user id %s", self.user_id)
        return self.user_id or ""

    def _login_step1(self) -> str | None:
        response = self._session.get(
            _LOGIN_URL,
            headers={"User-Agent": self._agent},
            cookies={"userId": self.username, "sdkVersion": "3.9", "deviceId": self._device_id},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise LoginFailed(f"login step 1 returned HTTP {response.status_code}")
        return _strip_prefix(response.text).get("_sign")

    def _login_step2(self, sign: str | None) -> str:
        fields = {
            "sid": "xiaomiio",
            "hash": hashlib.md5(self._password.encode()).hexdigest().upper(),
            "callback": "https://sts.api.io.mi.com/sts",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "user": self.username,
            "_json": "true",
        }
        if sign:
            fields["_sign"] = sign

        response = self._session.post(
            _AUTH_URL,
            headers={
                "User-Agent": self._agent,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            params=fields,
            cookies={"sdkVersion": "3.9", "deviceId": self._device_id},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise LoginFailed(f"login step 2 returned HTTP {response.status_code}")

        data = _strip_prefix(response.text)
        if data.get("notificationUrl"):
            raise TwoFactorRequired(data["notificationUrl"])
        if not data.get("location"):
            raise LoginFailed(
                "no location in the login response -- usually a wrong username or "
                f"password. Server said: {data.get('desc') or data.get('code')}"
            )

        self.ssecurity = data.get("ssecurity")
        self.user_id = str(data.get("userId", ""))
        return data["location"]

    def _login_step3(self, location: str) -> None:
        response = self._session.get(
            location, headers={"User-Agent": self._agent}, timeout=self.timeout
        )
        if response.status_code != 200:
            raise LoginFailed(f"login step 3 returned HTTP {response.status_code}")
        self.service_token = response.cookies.get("serviceToken")
        if not self.service_token:
            raise LoginFailed("no serviceToken cookie after login")

    # -- api -----------------------------------------------------------------

    def call(self, server: str, path: str, data: dict) -> Any:
        if not self.service_token or not self.ssecurity:
            raise CloudError("call login() first")

        url = api_url(server) + path
        nonce = generate_nonce()
        snonce = signed_nonce(self.ssecurity, nonce)
        params = {"data": json.dumps(data, separators=(",", ":"))}
        fields = encrypted_params(url, "POST", nonce, snonce, params, self.ssecurity)

        response = self._session.post(
            url,
            headers={
                "User-Agent": self._agent,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept-Encoding": "identity",
                "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
                "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
            },
            cookies={
                "userId": str(self.user_id),
                "yetAnotherServiceToken": self.service_token,
                "serviceToken": self.service_token,
                "locale": "en_GB",
                "timezone": "GMT+00:00",
                "is_daylight": "0",
                "dst_offset": "0",
                "channel": "MI_APP_STORE",
            },
            params=fields,
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise CloudError(f"{path} returned HTTP {response.status_code}")

        decrypted = decrypt_rc4(snonce, response.text)
        return json.loads(decrypted.decode("utf-8", errors="replace"))

    # -- devices -------------------------------------------------------------

    def list_devices(self, server: str) -> list[CloudDevice]:
        """Every device on the account, however it is grouped."""
        response = self.call(
            server, "/home/device_list", {"getVirtualModel": False, "getHuamiDevices": 0}
        )
        result = (response or {}).get("result") or {}
        return [CloudDevice.from_api(e, server) for e in result.get("list", [])]

    def list_homes(self, server: str) -> list[dict]:
        response = self.call(
            server,
            "/v2/homeroom/gethome",
            {
                "fg": True,
                "fetch_share": True,
                "fetch_share_dev": True,
                "limit": 300,
                "app_ver": 7,
            },
        )
        result = (response or {}).get("result") or {}
        return result.get("homelist", [])

    def list_devices_by_home(self, server: str) -> list[CloudDevice]:
        """Devices reached by walking homes -- what most tooling does."""
        devices: list[CloudDevice] = []
        for home in self.list_homes(server):
            response = self.call(
                server,
                "/v2/home/home_device_list",
                {
                    "home_owner": home.get("home_owner", self.user_id),
                    "home_id": int(home["id"]),
                    "limit": 200,
                    "get_split_device": True,
                    "support_smart_home": True,
                },
            )
            result = (response or {}).get("result") or {}
            devices += [
                CloudDevice.from_api(e, server) for e in result.get("device_info", [])
            ]
        return devices

    def device_count(self, server: str) -> Any:
        return self.call(
            server, "/v2/user/get_device_cnt", {"fetch_own": True, "fetch_share": True}
        )

    def search_all_servers(
        self, servers: list[str] | None = None
    ) -> Iterator[tuple[str, list[CloudDevice], Exception | None]]:
        """Try every region, yielding results as they arrive."""
        for server in servers or SERVERS:
            try:
                yield server, self.list_devices(server), None
            except Exception as exc:  # noqa: BLE001 - report, never abort the sweep
                yield server, [], exc
