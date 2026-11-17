"""Explicit first-administrator bootstrap.

Run `python -m app.auth.bootstrap USERNAME` in an interactive terminal.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.auth.accounts import bootstrap_admin
from app.db import init_db, session_scope
from app.infrastructure.orm import Base


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first administrator account")
    parser.add_argument("username", nargs="?")
    parser.add_argument("--status", action="store_true", help="Report whether an active administrator exists")
    parser.add_argument("--password-stdin", action="store_true", help="Read one password line for unattended installation")
    args = parser.parse_args()
    init_db()
    if args.status:
        users = Base.metadata.tables["users"]
        with session_scope() as session:
            active_admin = session.execute(select(users.c.id).where(
                users.c.role == "admin", users.c.disabled == 0,
            ).limit(1)).first()
        print("admin-ready" if active_admin else "needs-bootstrap")
        return
    if not args.username:
        parser.error("username is required unless --status is used")
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            parser.error("password input is empty")
    else:
        password = getpass.getpass("New administrator password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match")
    principal = bootstrap_admin(args.username, password)
    print(f"Administrator created: {principal.username} (id={principal.user_id})")


if __name__ == "__main__":
    main()
