"""Explicit first-administrator bootstrap.

Run `python -m app.auth.bootstrap USERNAME` in an interactive terminal.
"""

import argparse
import getpass

from app.auth.accounts import bootstrap_admin
from app.db import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first administrator account")
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("New administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    init_db()
    principal = bootstrap_admin(args.username, password)
    print(f"Administrator created: {principal.username} (id={principal.user_id})")


if __name__ == "__main__":
    main()
