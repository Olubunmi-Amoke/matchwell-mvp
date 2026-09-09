"""Privacy-safe restore verification for the Matchwell pilot database.

Used by the backup/restore operator scripts and the CI restore drill. Never
prints row content -- only table names and row counts -- so it is safe to
run against a restored copy of a real backup as well as synthetic CI data.

Usage:
    python scripts/backup/verify_invariants.py --database-url <restore-url> \\
        [--compare-url <source-url>] [--min-rows users=1 centers=1]

Exit code is non-zero if:
  * any of --min-rows thresholds are not met on the restore database, or
  * --compare-url is given and any invariant table's row count differs.

This script deliberately does not import matchwell's own dependency stack
so it stays runnable even against a database that was migrated by a
different application version than the one currently checked out.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

# Core tables every healthy Matchwell database must have at least one row
# in once the pilot has any real usage. Kept intentionally small and
# structural -- this is a schema/data-presence smoke check, not a business
# rule engine.
DEFAULT_INVARIANT_TABLES = (
    "centers",
    "communities",
    "consent_versions",
    "assessment_definitions",
    "pilot_plans",
)


def _table_counts(engine: Engine) -> dict[str, int]:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for table in sorted(tables):
            if table == "alembic_version":
                continue
            result = connection.execute(text(f'SELECT COUNT(*) FROM "{table}"'))
            counts[table] = int(result.scalar_one())
    return counts


def _parse_min_rows(pairs: list[str]) -> dict[str, int]:
    thresholds: dict[str, int] = {}
    for pair in pairs:
        table, _, value = pair.partition("=")
        thresholds[table.strip()] = int(value.strip())
    return thresholds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("MATCHWELL_VERIFY_DATABASE_URL"),
        help="Restore target connection URL (never printed in full).",
    )
    parser.add_argument(
        "--compare-url",
        default=os.environ.get("MATCHWELL_VERIFY_COMPARE_URL"),
        help="Optional source connection URL to diff row counts against.",
    )
    parser.add_argument(
        "--min-rows",
        action="append",
        default=[],
        metavar="TABLE=N",
        help="Require at least N rows in TABLE on the restore database. "
        "Repeatable. Defaults cover the core reference tables.",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or MATCHWELL_VERIFY_DATABASE_URL must be provided")

    thresholds = _parse_min_rows(args.min_rows) or {
        table: 1 for table in DEFAULT_INVARIANT_TABLES
    }

    restore_engine = create_engine(args.database_url)
    restore_counts = _table_counts(restore_engine)

    print("Restore database table row counts (structure/counts only):")
    for table, count in restore_counts.items():
        print(f"  {table}: {count}")

    failures: list[str] = []
    for table, minimum in thresholds.items():
        actual = restore_counts.get(table)
        if actual is None:
            failures.append(f"table '{table}' is missing from the restore database")
        elif actual < minimum:
            failures.append(
                f"table '{table}' has {actual} row(s); expected at least {minimum}"
            )

    if args.compare_url:
        compare_engine = create_engine(args.compare_url)
        compare_counts = _table_counts(compare_engine)
        print("Comparing restore counts against the source database ...")
        shared_tables = sorted(
            set(DEFAULT_INVARIANT_TABLES) & set(restore_counts) & set(compare_counts)
        )
        for table in shared_tables:
            if restore_counts[table] != compare_counts[table]:
                failures.append(
                    f"table '{table}' row count mismatch: "
                    f"source={compare_counts[table]} restore={restore_counts[table]}"
                )

    if failures:
        print("\nRestore verification FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nRestore verification passed: schema present and invariant counts match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
