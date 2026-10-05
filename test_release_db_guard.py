from __future__ import annotations

import hashlib
import sqlite3

import pytest

from release_db_guard import ReleaseDbGuardError, validate_release_db


SCHEMA = (
    "CREATE TABLE balances (asset_name TEXT PRIMARY KEY, balance, last_updated TEXT, idempotency_key TEXT)",
    "CREATE TABLE store_mappings (store TEXT PRIMARY KEY, large TEXT)",
    "CREATE TABLE sync_state_events (event_id TEXT PRIMARY KEY, status TEXT)",
    "CREATE TABLE transactions (email_date TEXT)",
    "CREATE TABLE transfers (date TEXT)",
)


def make_db(path, *, populated: bool = True, omit: str | None = None):
    """Build a synthetic fixture database; never use an operational ledger."""

    con = sqlite3.connect(path)
    try:
        for statement in SCHEMA:
            if omit is None or f" {omit} " not in statement:
                con.execute(statement)
        if populated:
            con.execute("INSERT INTO balances VALUES ('fixture-asset', 1, 'fixture-time', 'fixture-key')")
            con.execute("INSERT INTO sync_state_events VALUES ('fixture-event', 'verified')")
            con.execute("INSERT INTO transactions VALUES ('2000-01-01')")
        con.commit()
    finally:
        con.close()


def test_validate_release_db_accepts_samefile_and_reads_counts(tmp_path):
    db = tmp_path / "ledger.db"
    make_db(db)

    identity = validate_release_db(db, expected_path=db)

    assert identity.path == db.resolve()
    assert identity.counts["transactions"] == 1
    assert identity.counts["sync_state_events"] == 1


def test_validate_release_db_rejects_missing_path_without_creating_it(tmp_path):
    db = tmp_path / "missing.db"

    with pytest.raises(ReleaseDbGuardError, match="does not exist"):
        validate_release_db(db, expected_path=db)

    assert not db.exists()


def test_validate_release_db_rejects_relative_path(tmp_path):
    with pytest.raises(ReleaseDbGuardError, match="must be absolute"):
        validate_release_db("ledger.db", expected_path=tmp_path / "ledger.db")


def test_validate_release_db_rejects_wrong_existing_db(tmp_path):
    expected = tmp_path / "approved.db"
    wrong = tmp_path / "wrong.db"
    make_db(expected)
    make_db(wrong)

    with pytest.raises(ReleaseDbGuardError, match="not the approved"):
        validate_release_db(wrong, expected_path=expected)


def test_validate_release_db_rejects_empty_or_incomplete_schema(tmp_path):
    empty = tmp_path / "empty.db"
    make_db(empty, populated=False)
    with pytest.raises(ReleaseDbGuardError, match="unexpectedly empty"):
        validate_release_db(empty, expected_path=empty)

    incomplete = tmp_path / "incomplete.db"
    make_db(incomplete, omit="transfers")
    with pytest.raises(ReleaseDbGuardError, match="schema is incomplete"):
        validate_release_db(incomplete, expected_path=incomplete)


@pytest.mark.parametrize("configured_path", [None, "", "  "])
def test_validate_release_db_rejects_missing_configuration(tmp_path, configured_path):
    with pytest.raises(ReleaseDbGuardError, match="is required"):
        validate_release_db(configured_path, expected_path=tmp_path / "approved.db")


def test_validate_release_db_requires_explicit_approved_path(tmp_path):
    with pytest.raises(TypeError, match="expected_path"):
        validate_release_db(tmp_path / "ledger.db")


def test_validate_release_db_rejects_directory(tmp_path):
    with pytest.raises(ReleaseDbGuardError, match="regular file"):
        validate_release_db(tmp_path, expected_path=tmp_path)


def test_validate_release_db_preserves_database_bytes(tmp_path):
    db = tmp_path / "ledger.db"
    make_db(db)
    before = hashlib.sha256(db.read_bytes()).digest()

    validate_release_db(db, expected_path=db)

    assert hashlib.sha256(db.read_bytes()).digest() == before


@pytest.mark.parametrize("filename", ["ledger?.db", "ledger#.db", "ledger space.db"])
def test_validate_release_db_handles_uri_reserved_characters(tmp_path, filename):
    db = tmp_path / filename
    make_db(db)
    before = hashlib.sha256(db.read_bytes()).digest()

    identity = validate_release_db(db, expected_path=db)

    assert identity.path == db.resolve()
    assert identity.counts["transactions"] == 1
    assert hashlib.sha256(db.read_bytes()).digest() == before
    assert not (tmp_path / "ledger").exists()
