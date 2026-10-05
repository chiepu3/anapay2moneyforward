# All dates, values, identities, and source labels below are synthetic fixtures.
from __future__ import annotations

import json

import pytest

from correction_plan import (
    CorrectionActionKind,
    CorrectionEvidence,
    CorrectionExecutor,
    CorrectionPlanStatus,
    CorrectionRunStatus,
    JsonlCorrectionAuditLog,
    MemoryCorrectionAuditLog,
    build_correction_plan,
    exact_pair_matches,
)


def evidence(*, income: str = "", suffix: str = "", target: str = "JAL Pay") -> CorrectionEvidence:
    return CorrectionEvidence(
        provider="fixture-provider",
        source_asset="fixture-source-card",
        target_asset=target,
        date="2000-01-01T12:00:00+09:00",
        amount=7,
        source_message_id=f"source-message-{suffix}",
        target_message_id=f"target-message-{suffix}",
        source_remote_id=f"source-row-{suffix}",
        target_remote_id=f"receive-row-{suffix}",
        wrong_expense_remote_id=f"wrong-row-{suffix}",
        compensation_remote_id="" if income else f"comp-row-{suffix}",
        compensation_mode="aggregate_audit" if not income else "per_item",
        compensation_expected_date="2000-01-01T12:00:00+09:00" if not income else "",
        compensation_expected_amount=14 if not income else 0,
        compensation_expected_signed_amount=14 if not income else 0,
        compensation_expected_kind="income" if not income else "",
        compensation_expected_content="fixture-compensation" if not income else "",
        compensation_expected_count_state="ON" if not income else "",
        ordinary_income_remote_id=income,
        ordinary_income_confirmed_duplicate=bool(income),
        target_receive_count=1,
        source_count_state="OFF",
        source_evidence={
            "message_id": f"source-message-{suffix}",
            "asset_name": "fixture-source-card",
        },
        target_evidence={
            "message_id": f"target-message-{suffix}",
            "receive_remote_ids": [f"receive-row-{suffix}"],
            "receive_rows": [
                {
                    "remote_id": f"receive-row-{suffix}",
                    "date": "2000-01-01T12:00:00+09:00",
                    "amount": 7,
                    "asset_name": target,
                }
            ],
            **(
                {
                    "compensation_row": {
                        "remote_id": f"comp-row-{suffix}",
                        "date": "2000-01-01T12:00:00+09:00",
                    "amount": 14,
                    "signed_amount": 14,
                    "asset_name": target,
                    "kind": "income",
                    "content": "fixture-compensation",
                    "count_state": "ON",
                    }
                }
                if not income
                else {}
            ),
        },
    )


def live_evidence(*, suffix: str = "") -> CorrectionEvidence:
    base = evidence(suffix=suffix)
    payload = base.to_dict()
    payload.update(
        ordinary_income_remote_id=f"income-row-{suffix}",
        ordinary_income_confirmed_duplicate=True,
        compensation_remote_id="",
        compensation_mode="per_item",
        compensation_expected_date="",
        compensation_expected_amount=0,
        compensation_expected_signed_amount=0,
        compensation_expected_kind="",
        compensation_expected_content="",
        compensation_expected_count_state="",
        target_evidence={
            key: value for key, value in base.target_evidence.items() if key != "compensation_row"
        },
    )
    return CorrectionEvidence(**payload)


class FakeAdapter:
    def __init__(self, plan):
        self.states = {action.action_id: "before" for action in plan.actions}
        self.balances = {"JAL Pay": 101}
        self.apply_calls = 0
        self.fail_before: set[str] = set()
        self.fail_after: set[str] = set()
        self.corrupt_after = False

    def read_action(self, action):
        return {"state": self.states[action.action_id]}

    def matches(self, action, snapshot, phase):
        return snapshot.get("state") == phase

    def apply_action(self, action):
        self.apply_calls += 1
        if action.action_id in self.fail_before:
            raise RuntimeError("failed before save")
        self.states[action.action_id] = "after"
        for asset, delta in action.balance_delta.items():
            self.balances[asset] = self.balances.get(asset, 0) + delta
        if self.corrupt_after:
            self.balances["JAL Pay"] += 1
            self.corrupt_after = False
        if action.action_id in self.fail_after:
            raise TimeoutError("save outcome unknown")

    def read_balance(self, asset):
        return self.balances.get(asset, 0)


def test_exact_pair_matching_is_strict_and_blocks_ambiguous_same_amount():
    source = [
        {"id": "s1", "date": "2000-01-01T00:00:00+00:00", "amount": 7},
        {"id": "s2", "date": "2000-01-02T00:00:00+00:00", "amount": 7},
    ]
    target = [
        {"id": "t1", "date": "2000-01-01T00:00:00+00:00", "amount": 7},
        {"id": "t2", "date": "2000-01-01T00:00:00+00:00", "amount": 7},
    ]
    matches = exact_pair_matches(source, target)
    assert matches[0].status == "ambiguous"
    assert matches[1].status == "unmatched"


def test_explicit_compensation_plan_preserves_count_off():
    plan = build_correction_plan([evidence(suffix="fixture")])
    assert plan.status is CorrectionPlanStatus.READY
    assert [a.kind for a in plan.actions] == [
        CorrectionActionKind.REASSIGN_NATIVE_TRANSFER,
        CorrectionActionKind.QUARANTINE_WRONG_EXPENSE,
        CorrectionActionKind.BALANCE_COMPENSATION,
    ]
    assert plan.actions[-1].desired_after["amount"] == 14
    assert plan.actions[-1].audit_only is True
    assert plan.balance_invariants == {}
    assert plan.preserve_source_count_flags is True
    assert plan.actions[1].desired_after["count"] == "OFF"


def test_aggregate_compensation_is_audit_only_and_not_a_per_item_formula():
    base = evidence(suffix="aggregate")
    row = dict(base.target_evidence["compensation_row"])
    row.update(
        remote_id="aggregate-comp",
        date="2000-01-03T00:00:00+09:00",
        amount=23,
        signed_amount=23,
        asset_name="JAL Pay",
        kind="income",
        content="fixture-aggregate-compensation",
        count_state="ON",
    )
    item = CorrectionEvidence(
        **{
            **base.to_dict(),
            "compensation_remote_id": "aggregate-comp",
            "compensation_mode": "aggregate_audit",
            "compensation_expected_date": "2000-01-03T00:00:00+09:00",
            "compensation_expected_amount": 23,
            "compensation_expected_signed_amount": 23,
            "compensation_expected_kind": "income",
            "compensation_expected_content": "fixture-aggregate-compensation",
            "compensation_expected_count_state": "ON",
            "target_evidence": {**base.target_evidence, "compensation_row": row},
        }
    )
    plan = build_correction_plan([item])
    assert plan.status is CorrectionPlanStatus.READY
    action = plan.actions[-1]
    assert action.audit_only is True
    assert action.desired_after["amount"] == 23
    assert action.balance_delta == {}


def test_unsupported_per_item_compensation_is_explicitly_blocked():
    base = evidence(suffix="unsupported-per-item")
    item = CorrectionEvidence(**{**base.to_dict(), "compensation_mode": "per_item"})
    plan = build_correction_plan([item])
    assert plan.status is CorrectionPlanStatus.BLOCKED
    assert any("per-item compensation is unsupported" in error for error in plan.errors)


def test_three_row_duplicate_plan_removes_income_and_has_zero_net_delta():
    plan = build_correction_plan([evidence(income="income-row", suffix="three")])
    assert plan.status is CorrectionPlanStatus.READY
    assert [a.kind for a in plan.actions] == [
        CorrectionActionKind.REASSIGN_NATIVE_TRANSFER,
        CorrectionActionKind.QUARANTINE_WRONG_EXPENSE,
        CorrectionActionKind.REMOVE_DUPLICATE_INCOME,
    ]
    assert sum(action.balance_delta.get("JAL Pay", 0) for action in plan.actions) == 0
    assert plan.actions[2].remote_id == "income-row"


def test_ana_pay_is_supported_by_the_same_evidence_model():
    plan = build_correction_plan([evidence(suffix="ana", target="ANA Pay")])
    assert plan.status is CorrectionPlanStatus.READY
    assert {action.target_asset for action in plan.actions} == {"ANA Pay"}
    assert plan.balance_invariants == {}


def test_unknown_income_or_multiple_receives_fail_closed():
    unknown = evidence(suffix="unknown")
    unknown = CorrectionEvidence(**{**unknown.to_dict(), "ordinary_income_remote_id": "income-row"})
    multiple = CorrectionEvidence(**{**evidence(suffix="multiple").to_dict(), "target_receive_count": 2})
    assert build_correction_plan([unknown]).status is CorrectionPlanStatus.BLOCKED
    assert build_correction_plan([multiple]).status is CorrectionPlanStatus.BLOCKED


def test_target_receive_identity_count_and_source_asset_are_not_inferred():
    base = evidence(suffix="receive-count")
    target_evidence = dict(base.target_evidence)
    target_evidence["receive_remote_ids"] = ["receive-row-receive-count", "receive-row-2"]
    target_evidence["receive_rows"] = [
        *target_evidence["receive_rows"],
        {
            "remote_id": "receive-row-2",
            "date": base.date,
            "amount": base.amount,
            "asset_name": base.target_asset,
        },
    ]
    multiple_receives = CorrectionEvidence(
        **{**base.to_dict(), "target_receive_count": 2, "target_evidence": target_evidence}
    )
    missing_source = CorrectionEvidence(**{**base.to_dict(), "source_asset": ""})
    assert build_correction_plan([multiple_receives]).status is CorrectionPlanStatus.BLOCKED
    assert build_correction_plan([missing_source]).status is CorrectionPlanStatus.BLOCKED


def test_compensation_without_its_own_remote_identity_is_blocked():
    base = evidence(suffix="missing-compensation")
    missing_compensation = CorrectionEvidence(
        **{**base.to_dict(), "compensation_remote_id": ""}
    )
    assert build_correction_plan([missing_compensation]).status is CorrectionPlanStatus.BLOCKED


def test_dry_run_is_read_only_and_jsonl_audit_is_append_only(tmp_path):
    plan = build_correction_plan([evidence(suffix="dry")])
    adapter = FakeAdapter(plan)
    audit_path = tmp_path / "correction.jsonl"
    report = CorrectionExecutor(adapter, JsonlCorrectionAuditLog(audit_path)).run(plan)
    assert report.status is CorrectionRunStatus.DRY_RUN
    assert adapter.apply_calls == 0
    assert adapter.balances == {"JAL Pay": 101}
    assert all(json.loads(line)["status"] == "dry_run" for line in audit_path.read_text().splitlines())


def test_dry_run_does_not_require_balance_reads():
    plan = build_correction_plan([evidence(suffix="no-balance")])
    adapter = FakeAdapter(plan)

    def fail_balance_read(asset):
        raise AssertionError(f"dry-run read balance unexpectedly: {asset}")

    adapter.read_balance = fail_balance_read
    report = CorrectionExecutor(adapter).run(plan)
    assert report.status is CorrectionRunStatus.DRY_RUN
    assert adapter.apply_calls == 0


@pytest.mark.parametrize("uncertain_phase, expected_status", [("read_error", "read_error"), ("unknown", "unknown")])
def test_uncertain_readback_blocks_plan_and_compensation_is_not_executable(
    uncertain_phase, expected_status
):
    plan = build_correction_plan([evidence(suffix=f"uncertain-{uncertain_phase}")])
    adapter = FakeAdapter(plan)
    uncertain_action = plan.actions[1].action_id

    def uncertain_read(action):
        if action.action_id == uncertain_action:
            return {"phase": uncertain_phase}
        return {"state": adapter.states[action.action_id]}

    adapter.read_action = uncertain_read
    report = CorrectionExecutor(adapter).run(plan)
    assert report.status is CorrectionRunStatus.BLOCKED
    assert report.action_statuses[uncertain_action] == expected_status
    assert report.action_statuses[plan.actions[2].action_id] == "blocked"
    assert adapter.apply_calls == 0


def test_live_preflight_reads_all_actions_before_any_apply_on_later_read_error():
    plan = build_correction_plan([live_evidence(suffix="live-preflight-error")])
    adapter = FakeAdapter(plan)
    failing_action = plan.actions[1].action_id
    read_ids = []
    original_read = adapter.read_action

    def read_action(action):
        read_ids.append(action.action_id)
        if action.action_id == failing_action:
            return {"phase": "read_error"}
        return original_read(action)

    adapter.read_action = read_action
    report = CorrectionExecutor(adapter, dry_run=False).run(plan)
    assert report.status is CorrectionRunStatus.BLOCKED
    assert report.action_statuses[failing_action] == "read_error"
    assert set(read_ids) == {action.action_id for action in plan.actions}
    assert report.writes_attempted == 0
    assert adapter.apply_calls == 0


def test_live_run_is_idempotent_and_rereads_after_unknown_save_outcome():
    plan = build_correction_plan([live_evidence(suffix="live")])
    adapter = FakeAdapter(plan)
    first_action = plan.actions[0].action_id
    adapter.fail_after.add(first_action)
    audit = MemoryCorrectionAuditLog()
    first = CorrectionExecutor(adapter, audit, dry_run=False).run(plan)
    assert first.status is CorrectionRunStatus.COMPLETED
    assert first.writes_attempted == 3
    assert any(event["status"] == "unknown" for event in audit.events)
    calls = adapter.apply_calls
    second = CorrectionExecutor(adapter, audit, dry_run=False).run(plan)
    assert second.status is CorrectionRunStatus.COMPLETED
    assert adapter.apply_calls == calls


def test_live_run_converts_save_readback_exception_to_unknown():
    plan = build_correction_plan([live_evidence(suffix="read-exception")])
    adapter = FakeAdapter(plan)
    target_action = plan.actions[0].action_id
    original_read = adapter.read_action

    def read_action(action):
        if action.action_id == target_action and adapter.states[action.action_id] == "after":
            raise RuntimeError("readback unavailable")
        return original_read(action)

    adapter.read_action = read_action
    report = CorrectionExecutor(adapter, dry_run=False).run(plan)
    assert report.status is CorrectionRunStatus.UNKNOWN
    assert report.action_statuses[target_action] == "unknown"
    assert report.writes_attempted == 1


def test_partial_failure_can_resume_without_reapplying_completed_actions():
    plan = build_correction_plan([live_evidence(suffix="resume")])
    adapter = FakeAdapter(plan)
    adapter.fail_before.add(plan.actions[1].action_id)
    first = CorrectionExecutor(adapter, dry_run=False).run(plan)
    assert first.status is CorrectionRunStatus.UNKNOWN
    assert adapter.apply_calls == 2
    adapter.fail_before.clear()
    second = CorrectionExecutor(adapter, dry_run=False).run(plan)
    assert second.status is CorrectionRunStatus.COMPLETED
    assert adapter.apply_calls == 4


def test_balance_invariant_blocks_on_unexpected_balance_change():
    plan = build_correction_plan([live_evidence(suffix="balance")])
    adapter = FakeAdapter(plan)
    adapter.corrupt_after = True
    report = CorrectionExecutor(adapter, dry_run=False).run(plan)
    assert report.status is CorrectionRunStatus.BLOCKED
    assert "balance invariant" in report.blocked_reason


def test_live_balance_read_exception_is_unknown_without_writes():
    plan = build_correction_plan([live_evidence(suffix="balance-read-error")])
    adapter = FakeAdapter(plan)

    def fail_balance_read(asset):
        raise RuntimeError(f"balance unavailable for {asset}")

    adapter.read_balance = fail_balance_read
    report = CorrectionExecutor(adapter, dry_run=False).run(plan)
    assert report.status is CorrectionRunStatus.UNKNOWN
    assert "balance read" in report.blocked_reason
    assert report.writes_attempted == 0
    assert adapter.apply_calls == 0


def test_live_postwrite_balance_read_exception_is_unknown_after_verified_rows():
    plan = build_correction_plan([live_evidence(suffix="postwrite-balance")])
    adapter = FakeAdapter(plan)
    calls = 0
    original_read_balance = adapter.read_balance

    def fail_after_initial_balance(asset):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise RuntimeError("post-write balance unavailable")
        return original_read_balance(asset)

    adapter.read_balance = fail_after_initial_balance
    report = CorrectionExecutor(adapter, dry_run=False).run(plan)
    assert report.status is CorrectionRunStatus.UNKNOWN
    assert "balance read failed" in report.blocked_reason
    assert report.writes_attempted == 3
