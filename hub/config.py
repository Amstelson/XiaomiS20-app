"""Configuration loading.

Values come from (in order of precedence): explicit arguments, environment
variables, then `config.toml` in the repository root. The token is device
material -- it is never logged in full.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    ip: str
    token: str
    timeout: int = 5
    #: Minimum seconds between outbound calls, to stay friendly to the firmware.
    min_interval: float = 0.10

    def redacted_token(self) -> str:
        if len(self.token) < 8:
            return "*" * len(self.token)
        return f"{self.token[:4]}{'*' * (len(self.token) - 8)}{self.token[-4:]}"


def load(ip: str | None = None, token: str | None = None) -> Config:
    """Load configuration, preferring explicit args, then env, then config.toml."""
    data: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as fh:
            data = tomllib.load(fh).get("robot", {})

    ip = ip or os.environ.get("ROBOT_IP") or data.get("ip")
    token = token or os.environ.get("ROBOT_TOKEN") or data.get("token")

    if not ip or not token:
        raise ConfigError(
            "Robot IP and token are required.\n"
            "Set them either as environment variables:\n"
            "    export ROBOT_IP=192.168.1.42\n"
            "    export ROBOT_TOKEN=<32 hex characters>\n"
            f"or in {CONFIG_PATH}:\n"
            "    [robot]\n"
            '    ip = "192.168.1.42"\n'
            '    token = "0123456789abcdef0123456789abcdef"\n'
        )

    token = token.strip()
    if len(token) != 32 or any(c not in "0123456789abcdefABCDEF" for c in token):
        raise ConfigError(
            f"Token should be 32 hex characters, got {len(token)}. "
            "Extract it with Xiaomi-cloud-tokens-extractor."
        )

    return Config(
        ip=ip.strip(),
        token=token.lower(),
        timeout=int(os.environ.get("ROBOT_TIMEOUT", data.get("timeout", 5))),
        min_interval=float(
            os.environ.get("ROBOT_MIN_INTERVAL", data.get("min_interval", 0.10))
        ),
    )
