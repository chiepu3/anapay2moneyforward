"""Fail-closed boundary checks for delayed transaction mail.

The boundary map is intentionally explicit and per account.  An account that
is absent from the map is not classified by this guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from collections.abc import Mapping, MutableMapping
from typing import Any


NEEDS_REVIEW = "needs_review"
VERIFIED = "verified"
REMOVED_DUPLICATE = "removed_duplicate"
STALE_REASON = "残高照合境界より前の利用日の遅延メールのため要確認"
MISSING_DATE_REASON = "利用日時を確認できないため要確認"
BOUNDARY_TIME_UNKNOWN_REASON = "残高照合境界当日の時刻が不明なため要確認"
BOUNDARY_CONFIG_INVALID_REASON = "残高照合境界の設定を確認できないため要確認"
IDENTITY_UNKNOWN_REASON = "同期対象の確定したメッセージ識別子がないため要確認"
ALREADY_TERMINAL_REASON = "Money Forward同期済み状態のため同期対象外"
UNKNOWN_STATUS_REASON = "既知のMoney Forward同期対象statusではないため同期対象外"
KNOWN_SYNC_STATUSES = frozenset({"pending", "applying", "applied", NEEDS_REVIEW})
TERMINAL_STATUSES = frozenset({"done", VERIFIED, REMOVED_DUPLICATE})


@dataclass(frozen=True)
class VerifiedBalanceBoundary:
    """One explicitly verified account boundary.

    ``time_known`` must be false when the operator only verified a calendar
    day.  A boundary-day event is then quarantined because ordering is not
    provable.
    """

    boundary: date | datetime | str
    time_known: bool = False


@dataclass(frozen=True)
class BoundaryDecision:
    status: str
    reason: str = ""


@dataclass(frozen=True)
class SyncCandidateFilterResult:
    records: tuple[Any, ...]
    index_mapping: dict[int, int] | None
    excluded_reasons: tuple[str, ...]


def _field(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _set_field(record: Any, name: str, value: Any) -> None:
    if isinstance(record, MutableMapping):
        record[name] = value
    else:
        setattr(record, name, value)


def _usage_value(record: Any, *, transfer: bool) -> Any:
    return _field(record, "date" if transfer else "date_of_use")


def _accounts(record: Any, *, transfer: bool) -> tuple[str, ...]:
    if transfer:
        values = (_field(record, "source_asset"), _field(record, "target_asset"))
    else:
        values = (_field(record, "asset_name"),)
    return tuple(dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip()))


def _has_explicit_identity(record: Any) -> bool:
    """Require the persisted provider/message identity; never synthesize one."""

    provider = str(_field(record, "provider") or "").strip()
    message_id = str(
        _field(record, "message_id") or _field(record, "gmail_message_id") or ""
    ).strip()
    return bool(provider and message_id)


def _parse(value: Any) -> tuple[date, datetime | None, bool] | None:
    if isinstance(value, datetime):
        return value.date(), value, True
    if isinstance(value, date):
        return value, None, False
    text = str(value or "").strip()
    if not text:
        return None
    # ``datetime.fromisoformat`` accepts a date-only string as midnight.  That
    # is not evidence that the event or boundary time is known.
    if len(text) == 10 and text[4] in "-/" and text[7] in "-/":
        try:
            parsed_date = date.fromisoformat(text.replace("/", "-"))
        except ValueError:
            return None
        return parsed_date, None, False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(text.replace("/", "-"))
        except ValueError:
            return None
        return parsed_date, None, False
    return parsed.date(), parsed, True


def _boundary(value: Any) -> VerifiedBalanceBoundary:
    if isinstance(value, VerifiedBalanceBoundary):
        parsed = _parse(value.boundary)
        if parsed is None or (value.time_known and not parsed[2]):
            raise ValueError("boundary time_known flag does not match boundary value")
        return value
    parsed = _parse(value)
    if parsed is None:
        raise ValueError("invalid boundary")
    day, timestamp, has_time = parsed
    return VerifiedBalanceBoundary(timestamp or day, time_known=has_time)


def evaluate_delayed_email_boundary(
    record: Any,
    boundaries: Mapping[str, VerifiedBalanceBoundary | date | datetime | str],
    *,
    transfer: bool = False,
) -> BoundaryDecision:
    """Evaluate one record without applying a boundary to unverified accounts."""

    accounts = _accounts(record, transfer=transfer)
    applicable = [account for account in accounts if account in boundaries]
    if not applicable:
        return BoundaryDecision("not_applicable")

    used = _parse(_usage_value(record, transfer=transfer))
    if used is None:
        return BoundaryDecision(NEEDS_REVIEW, MISSING_DATE_REASON)
    used_day, used_timestamp, used_time_known = used
    for account in applicable:
        try:
            verified = _boundary(boundaries[account])
        except (TypeError, ValueError):
            return BoundaryDecision(NEEDS_REVIEW, BOUNDARY_CONFIG_INVALID_REASON)
        boundary_day, boundary_timestamp, _ = _parse(verified.boundary) or (None, None, False)
        if boundary_day is None:
            return BoundaryDecision(NEEDS_REVIEW, BOUNDARY_CONFIG_INVALID_REASON)
        if used_day < boundary_day:
            return BoundaryDecision(NEEDS_REVIEW, STALE_REASON)
        if used_day > boundary_day:
            continue
        if not verified.time_known or not used_time_known:
            return BoundaryDecision(NEEDS_REVIEW, BOUNDARY_TIME_UNKNOWN_REASON)
        if boundary_timestamp is None or used_timestamp is None:
            return BoundaryDecision(NEEDS_REVIEW, BOUNDARY_CONFIG_INVALID_REASON)
        try:
            before_boundary = used_timestamp < boundary_timestamp
        except TypeError:
            # A timezone mismatch is not safe to order implicitly.
            return BoundaryDecision(NEEDS_REVIEW, BOUNDARY_CONFIG_INVALID_REASON)
        if before_boundary:
            return BoundaryDecision(NEEDS_REVIEW, STALE_REASON)
    return BoundaryDecision("allow")


def filter_moneyforward_sync_candidates(
    records: list[Any],
    boundaries: Mapping[str, VerifiedBalanceBoundary | date | datetime | str] | None,
    *,
    transfer: bool = False,
    index_mapping: Mapping[int, int] | None = None,
) -> SyncCandidateFilterResult:
    """Filter all existing pending paths before login/assets/writers.

    This is deliberately independent of ``review_allows_reconciliation``:
    stale or incomplete records are hard-excluded even when their persisted
    review reason would otherwise permit a bounded retry.
    """

    kept: list[Any] = []
    remapped: dict[int, int] | None = {} if index_mapping is not None else None
    excluded: list[str] = []
    for old_index, record in enumerate(records):
        status = str(_field(record, "sync_status") or "pending").strip().lower()
        if status in TERMINAL_STATUSES:
            excluded.append(ALREADY_TERMINAL_REASON)
            continue
        if status not in KNOWN_SYNC_STATUSES:
            excluded.append(UNKNOWN_STATUS_REASON)
            continue
        if not _has_explicit_identity(record):
            excluded.append(IDENTITY_UNKNOWN_REASON)
            continue
        decision = evaluate_delayed_email_boundary(
            record,
            boundaries or {},
            transfer=transfer,
        )
        if decision.status == NEEDS_REVIEW:
            excluded.append(decision.reason)
            continue
        new_index = len(kept)
        kept.append(record)
        if remapped is not None:
            remapped[new_index] = int(index_mapping[old_index])
    return SyncCandidateFilterResult(tuple(kept), remapped, tuple(excluded))


def quarantine_delayed_transactions(
    records: list[Any],
    boundaries: Mapping[str, VerifiedBalanceBoundary | date | datetime | str] | None,
) -> int:
    """Mark only provably unsafe records as needs_review and return the count."""

    if not boundaries:
        return 0
    quarantined = 0
    for record in records:
        current = str(_field(record, "sync_status") or "pending").strip().lower()
        if current in TERMINAL_STATUSES:
            continue
        decision = evaluate_delayed_email_boundary(record, boundaries)
        if decision.status != NEEDS_REVIEW:
            continue
        _set_field(record, "sync_status", NEEDS_REVIEW)
        if isinstance(record, MutableMapping):
            _set_field(record, "mf", NEEDS_REVIEW)
        if not str(_field(record, "review_reason") or "").strip():
            _set_field(record, "review_reason", decision.reason)
        quarantined += 1
    return quarantined
