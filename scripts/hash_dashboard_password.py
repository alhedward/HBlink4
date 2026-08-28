#!/usr/bin/env python3
"""Generate a password hash for dashboard/config.json admin.password_hash."""

from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.admin import hash_password


def main() -> int:
    password = getpass("Dashboard admin password: ")
    confirm = getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match", file=sys.stderr)
        return 1
    if len(password) < 10:
        print("Password must be at least 10 characters", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
