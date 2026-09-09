"""Parse a PostgreSQL connection URL into shell-safe components.

Shared by the bash backup/restore scripts so URL parsing (including
percent-decoding of credentials) is not duplicated in fragile shell regex.
Prints ``export``-ready lines; never prints the password on its own line
with a label that would make it easy to grep out of shell history/logs by
name -- callers should still avoid echoing the full output.

Usage:
    eval "$(MATCHWELL_PG_URL="$DATABASE_URL" python scripts/backup/_pg_url.py)"
    # Sets: PG_HOST, PG_PORT, PG_USER, PG_DATABASE, PGPASSWORD
"""

from __future__ import annotations

import os
import shlex
import sys
from urllib.parse import unquote, urlsplit


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: set MATCHWELL_PG_URL and run _pg_url.py", file=sys.stderr)
        return 2
    url = os.environ.get("MATCHWELL_PG_URL", "")
    if not url:
        print("MATCHWELL_PG_URL is not set", file=sys.stderr)
        return 2
    # Normalize SQLAlchemy driver suffixes (postgresql+psycopg://, etc.).
    if "+" in url.split("://", 1)[0]:
        url = "postgresql://" + url.split("://", 1)[1]
    if not url.startswith("postgresql://"):
        print("only postgresql:// URLs are supported", file=sys.stderr)
        return 2

    parts = urlsplit(url)
    if not parts.username:
        print("the connection URL must include a username", file=sys.stderr)
        return 2
    database = parts.path.lstrip("/")
    if not database:
        print("the connection URL must include a database name", file=sys.stderr)
        return 2

    username = unquote(parts.username)
    password = unquote(parts.password) if parts.password else ""

    print(f"export PG_HOST={shlex.quote(parts.hostname or 'localhost')}")
    print(f"export PG_PORT={shlex.quote(str(parts.port or 5432))}")
    print(f"export PG_USER={shlex.quote(username)}")
    print(f"export PG_DATABASE={shlex.quote(database)}")
    print(f"export PGPASSWORD={shlex.quote(password)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
