"""Fail-closed validation of an explicitly approved existing ledger database.

The caller must supply the approved path independently of the configured path.
This public module contains no deployment-specific database location.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
from typing import Mapping


REQUIRED_TABLES = frozenset(
    {"balances", "store_mappings", "sync_state_events", "transactions", "transfers"}
)


class ReleaseDbGuardError(RuntimeError):
    """Raised before any application client is created when DB identity is unsafe."""


@dataclass(frozen=True)
class ReleaseDbIdentity:
    path: Path
    tables: tuple[str, ...]
    counts: Mapping[str, int]


def _configured_path(configured_path: str | os.PathLike[str] | None) -> Path:
    raw = str(configured_path or "").strip()
    if not raw:
        raise ReleaseDbGuardError("ANAPAY_SQLITE_PATH is required for the fixed release")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ReleaseDbGuardError("ANAPAY_SQLITE_PATH must be absolute")
    return path


def validate_release_db(
    configured_path: str | os.PathLike[str] | None,
    *,
    expected_path: Path,
) -> ReleaseDbIdentity:
    """Validate the approved existing DB and read its identity in read-only mode."""

    path = _configured_path(configured_path)
    expected = expected_path.expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ReleaseDbGuardError("configured SQLite DB does not exist as a regular file")
    try:
        same_file = os.path.samefile(path, expected)
    except OSError:
        same_file = False
    if not same_file:
        raise ReleaseDbGuardError("configured SQLite DB is not the approved existing DB")

    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        raise ReleaseDbGuardError("approved SQLite DB could not be opened read-only") from None
    try:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if isinstance(row[0], str)
        )
        missing = REQUIRED_TABLES.difference(tables)
        if missing:
            raise ReleaseDbGuardError("approved SQLite DB schema is incomplete")
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("transactions", "transfers", "sync_state_events", "balances")
        }
    except sqlite3.Error:
        raise ReleaseDbGuardError("approved SQLite DB schema could not be read") from None
    finally:
        connection.close()

    if counts["transactions"] <= 0 or counts["sync_state_events"] <= 0 or counts["balances"] <= 0:
        raise ReleaseDbGuardError("approved SQLite DB is unexpectedly empty")
    return ReleaseDbIdentity(path=path.resolve(), tables=tables, counts=counts)
