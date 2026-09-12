#!/usr/bin/env python3
"""List the devices on your Xiaomi account, with their tokens.

    python3 tools/cloud_devices.py

Queries two different endpoints, because they do not always agree. The
home-based listing -- what most tooling uses -- misses devices that are not
filed under a home the account owns; the flat listing does not.

It also prints the user id the credentials resolved to. Compare that against
the one shown in the Xiaomi Home app under Profile: if they differ, the account
is the problem, not the lookup.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.cloud import (  # noqa: E402
    SERVERS,
    CloudError,
    LoginFailed,
    TwoFactorRequired,
    XiaomiCloud,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-u", "--username", help="Mi account email, phone or id")
    parser.add_argument("-s", "--server", choices=SERVERS, help="default: try all")
    parser.add_argument(
        "--expect-user-id",
        help="the user id shown in the app; warns loudly if login resolves elsewhere",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    username = args.username or input("Mi account (email, phone or id): ").strip()
    password = getpass.getpass("Password: ")

    cloud = XiaomiCloud(username, password)
    try:
        user_id = cloud.login()
    except TwoFactorRequired as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 3
    except LoginFailed as exc:
        print(f"\nLogin failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nLogged in as user id {user_id}")
    if args.expect_user_id and args.expect_user_id.strip() != user_id:
        print(
            f"\n  *** This is NOT the account you expected ({args.expect_user_id}). ***\n"
            "  That explains an empty device list. Sign in as the account whose\n"
            "  user id matches the one in the Xiaomi Home app.\n"
        )

    servers = [args.server] if args.server else SERVERS
    total = 0

    for server in servers:
        try:
            flat = cloud.list_devices(server)
        except CloudError as exc:
            print(f"\n[{server}] error: {exc}")
            continue

        try:
            by_home = cloud.list_devices_by_home(server)
        except CloudError:
            by_home = []

        if not flat and not by_home:
            print(f"[{server}] no devices")
            continue

        total += len(flat)
        print(f"\n[{server}] {len(flat)} device(s) on the account "
              f"({len(by_home)} via homes)")
        for device in flat:
            print(f"\n  {device.describe()}")

        if len(flat) > len(by_home):
            print(
                f"\n  Note: {len(flat) - len(by_home)} device(s) are not filed under a "
                "home,\n  which is why the usual home-based tools report none."
            )

        vacuums = [d for d in flat if "vacuum" in d.model]
        if vacuums:
            best = vacuums[0]
            print("\n  For config.toml:\n")
            print("    [robot]")
            print(f'    ip = "{best.local_ip or "<find with tools/discover.py>"}"')
            print(f'    token = "{best.token}"')

    if not total:
        print(
            "\nNo devices on any server for this account.\n"
            "Check the user id above against Profile in the Xiaomi Home app --\n"
            "if it differs, the robot is registered to a different account."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
