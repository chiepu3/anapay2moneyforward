# Test inputs are entirely synthetic and do not encode a deployment boundary.
from datetime import date, datetime, timedelta, timezone, tzinfo

import pytest

from delayed_email_boundary import (
    BOUNDARY_TIME_UNKNOWN_REASON,
    BOUNDARY_CONFIG_INVALID_REASON,
    MISSING_DATE_REASON,
    USAGE_TIME_UNKNOWN_REASON,
    NEEDS_REVIEW,
    STALE_REASON,
    BoundaryDecision,
    VerifiedBalanceBoundary,
    evaluate_delayed_email_boundary,
    IDENTITY_UNKNOWN_REASON,
    ALREADY_TERMINAL_REASON,
    UNKNOWN_STATUS_REASON,
    filter_moneyforward_sync_candidates,
    quarantine_delayed_transactions,
)


def boundary():
    return {"fixture-wallet-a": VerifiedBalanceBoundary(datetime(2000, 1, 2, 12, 0), True)}


def test_unverified_account_is_not_given_another_account_boundary():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-b", "date_of_use": "1999-12-31"}, boundary()
    )
    assert decision.status == "not_applicable"


def test_old_usage_date_is_quarantined():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-01T23:59:00"}, boundary()
    )
    assert decision == BoundaryDecision(NEEDS_REVIEW, STALE_REASON)


def test_missing_usage_datetime_is_quarantined():
    decision = evaluate_delayed_email_boundary({"asset_name": "fixture-wallet-a"}, boundary())
    assert decision.status == NEEDS_REVIEW
    assert decision.reason == MISSING_DATE_REASON


def test_boundary_day_without_usage_time_is_quarantined():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": date(2000, 1, 2)}, boundary()
    )
    assert decision.status == NEEDS_REVIEW
    assert decision.reason == USAGE_TIME_UNKNOWN_REASON


def test_date_only_string_is_not_promoted_to_midnight_time():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-02"}, boundary()
    )
    assert decision.status == NEEDS_REVIEW
    assert decision.reason == USAGE_TIME_UNKNOWN_REASON


def test_boundary_date_only_with_time_known_true_is_rejected():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-02T12:00:00"},
        {"fixture-wallet-a": VerifiedBalanceBoundary("2000-01-02", True)},
    )
    assert decision.status == NEEDS_REVIEW
    assert decision.reason == BOUNDARY_CONFIG_INVALID_REASON


def test_boundary_day_with_unknown_boundary_time_is_quarantined():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-02T23:00:00"},
        {"fixture-wallet-a": VerifiedBalanceBoundary(date(2000, 1, 2), False)},
    )
    assert decision.status == NEEDS_REVIEW


def test_later_day_without_time_is_reviewed():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-03"}, boundary()
    )
    assert decision == BoundaryDecision(NEEDS_REVIEW, USAGE_TIME_UNKNOWN_REASON)


def test_boundary_timestamp_allows_at_or_after_event():
    assert evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-02T12:00:00"}, boundary()
    ).status == "allow"
    assert evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-02T11:59:59"}, boundary()
    ).reason == STALE_REASON


def test_timezone_mismatch_is_reviewed_instead_of_ordered_implicitly():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-02T12:00:00+09:00"},
        {"fixture-wallet-a": VerifiedBalanceBoundary(datetime(2000, 1, 2, 12, 0), True)},
    )
    assert decision.status == NEEDS_REVIEW


def test_same_jst_aware_boundary_and_usage_are_orderable():
    jst = timezone(timedelta(hours=9))
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": datetime(2000, 1, 2, 12, 0, tzinfo=jst)},
        {"fixture-wallet-a": VerifiedBalanceBoundary(datetime(2000, 1, 2, 12, 0, tzinfo=jst), True)},
    )
    assert decision.status == "allow"


def test_quarantine_preserves_existing_review_reason_and_skips_verified():
    records = [
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-01", "sync_status": "pending"},
        {
            "asset_name": "fixture-wallet-a",
            "date_of_use": "2000-01-01",
            "sync_status": "needs_review",
            "review_reason": "operator_reason",
        },
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-01", "sync_status": "verified"},
    ]
    assert quarantine_delayed_transactions(records, boundary()) == 2
    assert records[0]["mf"] == NEEDS_REVIEW
    assert records[1]["review_reason"] == "operator_reason"
    assert records[2]["sync_status"] == "verified"


def test_existing_pending_transaction_with_reconciliation_reason_is_hard_excluded():
    result = filter_moneyforward_sync_candidates(
        [
            {
                "asset_name": "fixture-wallet-a",
                "date_of_use": "2000-01-01T23:59:00",
                "provider": "fixture-provider",
                "message_id": "fixture-message-a",
                "sync_status": NEEDS_REVIEW,
                "review_reason": "remote_reconciliation_missing",
            }
        ],
        boundary(),
    )
    assert result.records == ()
    assert result.excluded_reasons == (STALE_REASON,)


def test_existing_pending_transfer_is_hard_excluded_before_writer():
    result = filter_moneyforward_sync_candidates(
        [
            {
                "date": "2000-01-01T23:59:00",
                "source_asset": "fixture-wallet-a",
                "target_asset": "fixture-wallet-b",
                "provider": "transfer",
                "message_id": "transfer-1",
                "sync_status": "pending",
            }
        ],
        boundary(),
        transfer=True,
    )
    assert result.records == ()
    assert result.excluded_reasons == (STALE_REASON,)


def test_unknown_identity_is_excluded_without_fingerprint_reconstruction():
    result = filter_moneyforward_sync_candidates(
        [
            {
                "asset_name": "fixture-wallet-a",
                "date_of_use": "2000-01-03",
                "provider": "fixture-provider",
                "amount": 7,
                "store": "fixture-merchant",
                "sync_status": "pending",
            }
        ],
        boundary(),
    )
    assert result.records == ()
    assert result.excluded_reasons == (IDENTITY_UNKNOWN_REASON,)


def test_unknown_transfer_identity_is_excluded_without_route_guessing():
    result = filter_moneyforward_sync_candidates(
        [
            {
                "date": "2000-01-03",
                "source_asset": "fixture-wallet-a",
                "target_asset": "fixture-wallet-b",
                "provider": "transfer",
                "sync_status": "pending",
            }
        ],
        boundary(),
        transfer=True,
    )
    assert result.records == ()
    assert result.excluded_reasons == (IDENTITY_UNKNOWN_REASON,)


def test_unknown_future_status_is_excluded_even_with_explicit_identity_and_empty_boundary_map():
    result = filter_moneyforward_sync_candidates(
        [
            {
                "asset_name": "fixture-wallet-a",
                "date_of_use": "2000-01-03",
                "provider": "fixture-provider",
                "message_id": "future-status",
                "sync_status": "future_status",
            }
        ],
        {},
    )
    assert result.records == ()
    assert result.excluded_reasons == (UNKNOWN_STATUS_REASON,)


def test_done_status_is_excluded_even_with_explicit_identity_and_empty_boundary_map():
    result = filter_moneyforward_sync_candidates(
        [
            {
                "asset_name": "fixture-wallet-a",
                "date_of_use": "2000-01-03",
                "provider": "fixture-provider",
                "message_id": "done-status",
                "sync_status": "done",
            }
        ],
        {},
    )
    assert result.records == ()
    assert result.excluded_reasons == (ALREADY_TERMINAL_REASON,)


def test_only_known_sync_statuses_pass_when_boundary_map_is_empty():
    for status in ("pending", "applying", "applied", NEEDS_REVIEW):
        result = filter_moneyforward_sync_candidates(
            [
                {
                    "asset_name": "fixture-wallet-a",
                    "date_of_use": "2000-01-03",
                    "provider": "fixture-provider",
                    "message_id": f"known-{status}",
                    "sync_status": status,
                }
            ],
            {},
        )
        assert len(result.records) == 1


def test_merged_verified_state_is_excluded_even_if_source_row_says_pending():
    result = filter_moneyforward_sync_candidates(
        [
            {
                "asset_name": "fixture-wallet-a",
                "date_of_use": "2000-01-01T23:59:00",
                "provider": "fixture-provider",
                "message_id": "fixture-message-a",
                "sync_status": "verified",
                "mf_transaction_id": "remote-1",
            }
        ],
        boundary(),
    )
    assert result.records == ()
    assert result.excluded_reasons == (ALREADY_TERMINAL_REASON,)


def test_index_mapping_is_compacted_after_exclusions():
    result = filter_moneyforward_sync_candidates(
        [
            {
                "asset_name": "fixture-wallet-a",
                "date_of_use": "2000-01-01",
                "provider": "fixture-provider",
                "message_id": "stale",
                "sync_status": "pending",
            },
            {
                "asset_name": "fixture-wallet-a",
                "date_of_use": "2000-01-03T12:00:00",
                "provider": "fixture-provider",
                "message_id": "safe",
                "sync_status": "pending",
            },
        ],
        boundary(),
        index_mapping={0: 11, 1: 12},
    )
    assert [row["message_id"] for row in result.records] == ["safe"]
    assert result.index_mapping == {0: 12}


@pytest.mark.parametrize("value", ["20000102", "2000-01-02", "2000/01/02", "1999-W52-7", "1999W527", date(2000, 1, 2)])
def test_date_only_formats_are_not_known_midnight(value):
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": value},
        {"fixture-wallet-a": VerifiedBalanceBoundary(datetime(2000, 1, 2), True)},
    )
    assert decision.status == NEEDS_REVIEW


def test_later_date_without_usage_time_still_requires_review():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "20000103"},
        {"fixture-wallet-a": VerifiedBalanceBoundary(datetime(2000, 1, 2), True)},
    )
    assert decision.status == NEEDS_REVIEW


@pytest.mark.parametrize("value", ["20000102T000000", "2000-01-02T00:00:00", datetime(2000, 1, 2)])
def test_explicit_midnight_is_known_time(value):
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": value},
        {"fixture-wallet-a": VerifiedBalanceBoundary(datetime(2000, 1, 2), True)},
    )
    assert decision.status == "allow"


@pytest.mark.parametrize("used, boundary_value, expected", [
    ("2000-01-03T00:00:00+14:00", "2000-01-02T12:00:00+00:00", NEEDS_REVIEW),
    ("2000-01-01T23:30:00-02:00", "2000-01-02T00:00:00+00:00", "allow"),
    ("2000-01-03T12:00:00", "2000-01-02T12:00:00+00:00", NEEDS_REVIEW),
    ("2000-01-03T12:00:00+00:00", "2000-01-02T12:00:00", NEEDS_REVIEW),
])
def test_cross_day_timezone_ordering_uses_instants_or_fails_closed(used, boundary_value, expected):
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": used},
        {"fixture-wallet-a": VerifiedBalanceBoundary(boundary_value, True)},
    )
    assert decision.status == expected


class FixtureFoldZone(tzinfo):
    def utcoffset(self, value):
        return timedelta(hours=-5 if value.fold else -4)

    def dst(self, value):
        return timedelta(0)

    def tzname(self, value):
        return "fixture-fold-zone"


def test_same_timezone_fold_is_compared_in_utc():
    zone = FixtureFoldZone()
    used = datetime(2000, 1, 2, 1, 30, tzinfo=zone, fold=0)
    later_boundary = datetime(2000, 1, 2, 1, 30, tzinfo=zone, fold=1)
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": used},
        {"fixture-wallet-a": VerifiedBalanceBoundary(later_boundary, True)},
    )
    assert decision == BoundaryDecision(NEEDS_REVIEW, STALE_REASON)


@pytest.mark.parametrize("status", ["pending", "needs_review", "applying", "applied"])
def test_date_only_pending_states_are_excluded_when_boundary_applies(status):
    result = filter_moneyforward_sync_candidates(
        [{
            "asset_name": "fixture-wallet-a",
            "date_of_use": "20000102",
            "provider": "fixture-provider",
            "message_id": "fixture-candidate",
            "sync_status": status,
        }],
        {"fixture-wallet-a": VerifiedBalanceBoundary(datetime(2000, 1, 2), True)},
    )
    assert result.records == ()
    assert result.excluded_reasons == (USAGE_TIME_UNKNOWN_REASON,)


def test_compact_date_only_boundary_is_not_a_known_midnight():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-02T00:00:00"},
        {"fixture-wallet-a": "20000102"},
    )
    assert decision == BoundaryDecision(NEEDS_REVIEW, BOUNDARY_TIME_UNKNOWN_REASON)


def test_compact_date_only_boundary_cannot_claim_known_time():
    decision = evaluate_delayed_email_boundary(
        {"asset_name": "fixture-wallet-a", "date_of_use": "2000-01-02T00:00:00"},
        {"fixture-wallet-a": VerifiedBalanceBoundary("20000102", True)},
    )
    assert decision == BoundaryDecision(NEEDS_REVIEW, BOUNDARY_CONFIG_INVALID_REASON)
