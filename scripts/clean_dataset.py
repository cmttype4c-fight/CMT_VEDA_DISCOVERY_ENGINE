#!/usr/bin/env python3
"""
Deployment CLI for the "clean the candidate dataset, keep one candidate"
operation (app/services/reset_service.py::dry_run_selective_counts /
execute_selective_reset).

This is a thin, explicit wrapper around that service module -- it adds no
logic of its own beyond argument parsing and printing. Run it from the
repository root, with the SAME environment the API/worker processes use
(DATABASE_URL in particular -- see app/config.py); it connects to whatever
database that environment points at, exactly like the API does.

SAFETY (read before running against anything but a local/test database):

    1. Take a database backup first (e.g. `pg_dump`). This script cannot
       take one itself -- it has no shell/OS access to the database host.
    2. Always run WITHOUT --confirm first. That is a read-only dry run:
       it prints exactly what would be deleted and what would be kept,
       and changes nothing in the database.
    3. Only pass --confirm once you've reviewed that output and are sure.

Usage:

    # 1. Dry run -- always do this first. Read-only, never writes.
    python3 scripts/clean_dataset.py --retain 4952752b-42a2-4b33-939c-94e445c36b8c

    # 2. The real, destructive run -- only after reviewing step 1's output
    #    and taking a backup.
    python3 scripts/clean_dataset.py --retain 4952752b-42a2-4b33-939c-94e445c36b8c --confirm

Exit codes: 0 on success (dry run printed, or cleanup verified clean);
1 if the named candidate does not exist, or if the post-cleanup state
could not be verified clean (nothing is left half-done either way --
execute_selective_reset runs as a single transaction).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running this script directly (`python3 scripts/clean_dataset.py`)
# from the repository root without needing the package pre-installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import session_scope  # noqa: E402
from app.services.reset_service import (  # noqa: E402
    SelectiveResetCounts,
    dry_run_selective_counts,
    execute_selective_reset,
)


def _print_counts(label: str, counts: SelectiveResetCounts) -> None:
    print(f"\n{label}")
    print(f"  retain_candidate_id          = {counts.retain_candidate_id}")
    print(f"  retained candidate exists    = {counts.retained_candidate_exists}")
    print("  rows that would be DELETED (not the retained candidate's):")
    for table, n in counts.deleted.items():
        print(f"    {table:<28} {n}")
    print(f"    {'TOTAL':<28} {counts.total_to_delete}")
    print("  rows that would be RETAINED (the one kept candidate's own):")
    for table, n in counts.retained.items():
        print(f"    {table:<28} {n}")
    print("  never touched (out of scope for this operation):")
    for table in counts.preserved:
        print(f"    {table}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete every Discovery candidate (and its dependent rows) except one, "
        "for the 'Simplify Discovery Newsletter Workflow and Clean Dataset' task."
    )
    parser.add_argument("--retain", required=True, metavar="CANDIDATE_UUID", help="The one candidate id to keep.")
    parser.add_argument(
        "--confirm", action="store_true",
        help="Actually perform the deletion. Without this flag, only a read-only dry run is printed.",
    )
    args = parser.parse_args()

    with session_scope() as db:
        pre = dry_run_selective_counts(db, args.retain)

        if not pre.retained_candidate_exists:
            print(
                f"ERROR: no candidate with id {args.retain!r} exists in this database. "
                "Refusing to proceed -- fix the --retain value and try again.",
                file=sys.stderr,
            )
            return 1

        if not args.confirm:
            _print_counts("DRY RUN (nothing has been changed) --", pre)
            print("\nRe-run with --confirm to actually perform this deletion.")
            return 0

        _print_counts("About to delete the following --", pre)
        result = execute_selective_reset(db, retain_candidate_id=args.retain, confirm=True)
        _print_counts("Done. Post-cleanup state --", result.post_counts)

        if not result.verified_clean:
            print(
                "\nWARNING: post-cleanup state did not verify as clean "
                "(expected exactly 1 surviving candidate and 0 rows left to delete). "
                "The transaction still committed what it did -- inspect the database directly.",
                file=sys.stderr,
            )
            return 1

        print(f"\nVerified: exactly one candidate remains ({args.retain}), with only its own dependent rows.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
