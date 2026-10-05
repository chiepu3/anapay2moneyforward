"""Evidence-backed, offline-first correction planning and recovery.

The module is intentionally independent of the Money Forward client.  A
caller must inject an adapter to perform a live write; the default executor
mode is a read-only dry run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


CORRECTION_PLAN_SCHEMA_VERSION = 1
SUPPORTED_CORRECTION_TARGETS = frozenset({"JAL Pay", "ANA Pay"})


class CorrectionPlanStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"


class CorrectionActionKind(str, Enum):
    REASSIGN_NATIVE_TRANSFER = "reassign_native_transfer"
    QUARANTINE_WRONG_EXPENSE = "quarantine_wrong_expense"
    REMOVE_DUPLICATE_INCOME = "remove_duplicate_income"
    BALANCE_COMPENSATION = "balance_compensation"


class CorrectionRunStatus(str, Enum):
    DRY_RUN = "dry_run"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _amount(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("amount must be an integer")
    result = int(str(value).replace(",", "").strip())
    if result <= 0:
        raise ValueError("amount must be positive")
    return result


def _date(value: Any) -> str:
    text = _text(value)
    if not text:
        raise ValueError("date is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _row_id(row: Mapping[str, Any]) -> str:
    return _text(row.get("id") or row.get("remote_id") or row.get("row_id"))


def _row_date(row: Mapping[str, Any]) -> str:
    return _date(row.get("date") or row.get("date_of_use") or row.get("used_at"))


def _row_amount(row: Mapping[str, Any]) -> int:
    return _amount(row.get("amount"))


@dataclass(frozen=True)
class PairMatch:
    source_id: str
    target_ids: tuple[str, ...]
    date: str
    amount: int
    status: str


def exact_pair_matches(
    source_rows: Iterable[Mapping[str, Any]],
    target_rows: Iterable[Mapping[str, Any]],
) -> tuple[PairMatch, ...]:
    """Match only exact date-and-amount pairs; never guess by amount alone."""

    targets: dict[tuple[str, int], list[str]] = {}
    for row in target_rows:
        row_id = _row_id(row)
        if not row_id:
            continue
        key = (_row_date(row), _row_amount(row))
        targets.setdefault(key, []).append(row_id)

    source_list = list(source_rows)
    target_use: dict[str, int] = {}
    preliminary: list[PairMatch] = []
    for row in source_list:
        source_id = _row_id(row)
        try:
            date = _row_date(row)
            amount = _row_amount(row)
        except (TypeError, ValueError):
            preliminary.append(PairMatch(source_id, (), "", 0, "unmatched"))
            continue
        candidates = tuple(targets.get((date, amount), ()))
        if len(candidates) == 1:
            target_use[candidates[0]] = target_use.get(candidates[0], 0) + 1
        preliminary.append(PairMatch(source_id, candidates, date, amount, ""))

    result: list[PairMatch] = []
    for match in preliminary:
        if not match.target_ids:
            status = "unmatched"
        elif len(match.target_ids) != 1:
            status = "ambiguous"
        elif target_use.get(match.target_ids[0], 0) != 1:
            status = "ambiguous"
        else:
            status = "confirmed"
        result.append(
            PairMatch(match.source_id, match.target_ids, match.date, match.amount, status)
        )
    return tuple(result)


@dataclass(frozen=True)
class CorrectionEvidence:
    provider: str
    source_asset: str
    target_asset: str
    date: str
    amount: int
    source_message_id: str
    target_message_id: str
    source_remote_id: str
    target_remote_id: str
    wrong_expense_remote_id: str
    compensation_remote_id: str = ""
    compensation_mode: str = "per_item"
    compensation_expected_date: str = ""
    compensation_expected_amount: int = 0
    compensation_expected_signed_amount: int = 0
    compensation_expected_kind: str = ""
    compensation_expected_content: str = ""
    compensation_expected_count_state: str = ""
    ordinary_income_remote_id: str = ""
    ordinary_income_confirmed_duplicate: bool = False
    target_receive_count: int = 1
    source_count_state: str = "OFF"
    source_evidence: Mapping[str, Any] = field(default_factory=dict)
    target_evidence: Mapping[str, Any] = field(default_factory=dict)

    def normalized(self) -> "CorrectionEvidence":
        return CorrectionEvidence(
            provider=_text(self.provider).casefold(),
            source_asset=_text(self.source_asset),
            target_asset=_text(self.target_asset),
            date=_date(self.date),
            amount=_amount(self.amount),
            source_message_id=_text(self.source_message_id),
            target_message_id=_text(self.target_message_id),
            source_remote_id=_text(self.source_remote_id),
            target_remote_id=_text(self.target_remote_id),
            wrong_expense_remote_id=_text(self.wrong_expense_remote_id),
            compensation_remote_id=_text(self.compensation_remote_id),
            compensation_mode=_text(self.compensation_mode).casefold() or "per_item",
            compensation_expected_date=_date(self.compensation_expected_date) if _text(self.compensation_expected_date) else "",
            compensation_expected_amount=(
                int(self.compensation_expected_amount)
                if self.compensation_expected_amount not in (None, "")
                else 0
            ),
            compensation_expected_signed_amount=(
                int(self.compensation_expected_signed_amount)
                if self.compensation_expected_signed_amount not in (None, "")
                else 0
            ),
            compensation_expected_kind=_text(self.compensation_expected_kind).casefold(),
            compensation_expected_content=_text(self.compensation_expected_content),
            compensation_expected_count_state=_text(self.compensation_expected_count_state).upper(),
            ordinary_income_remote_id=_text(self.ordinary_income_remote_id),
            ordinary_income_confirmed_duplicate=bool(self.ordinary_income_confirmed_duplicate),
            target_receive_count=int(self.target_receive_count),
            source_count_state=_text(self.source_count_state).upper(),
            source_evidence=dict(self.source_evidence),
            target_evidence=dict(self.target_evidence),
        )

    @property
    def evidence_id(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source_asset": self.source_asset,
            "target_asset": self.target_asset,
            "date": self.date,
            "amount": self.amount,
            "source_message_id": self.source_message_id,
            "target_message_id": self.target_message_id,
            "source_remote_id": self.source_remote_id,
            "target_remote_id": self.target_remote_id,
            "wrong_expense_remote_id": self.wrong_expense_remote_id,
            "compensation_remote_id": self.compensation_remote_id,
            "compensation_mode": self.compensation_mode,
            "compensation_expected_date": self.compensation_expected_date,
            "compensation_expected_amount": self.compensation_expected_amount,
            "compensation_expected_signed_amount": self.compensation_expected_signed_amount,
            "compensation_expected_kind": self.compensation_expected_kind,
            "compensation_expected_content": self.compensation_expected_content,
            "compensation_expected_count_state": self.compensation_expected_count_state,
            "ordinary_income_remote_id": self.ordinary_income_remote_id,
            "ordinary_income_confirmed_duplicate": self.ordinary_income_confirmed_duplicate,
            "target_receive_count": self.target_receive_count,
            "source_count_state": self.source_count_state,
            "source_evidence": dict(self.source_evidence),
            "target_evidence": dict(self.target_evidence),
        }


def _coerce_evidence(item: CorrectionEvidence | Mapping[str, Any]) -> CorrectionEvidence:
    if isinstance(item, CorrectionEvidence):
        return item.normalized()
    return CorrectionEvidence(**dict(item)).normalized()


@dataclass(frozen=True)
class CorrectionAction:
    action_id: str
    kind: CorrectionActionKind
    target_asset: str
    amount: int
    remote_id: str
    expected_before: Mapping[str, Any]
    desired_after: Mapping[str, Any]
    balance_delta: Mapping[str, int]
    reason: str
    audit_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "target_asset": self.target_asset,
            "amount": self.amount,
            "remote_id": self.remote_id,
            "expected_before": dict(self.expected_before),
            "desired_after": dict(self.desired_after),
            "balance_delta": dict(self.balance_delta),
            "reason": self.reason,
            "audit_only": self.audit_only,
        }


@dataclass(frozen=True)
class CorrectionPlan:
    plan_id: str
    status: CorrectionPlanStatus
    evidence: tuple[CorrectionEvidence, ...]
    actions: tuple[CorrectionAction, ...]
    errors: tuple[str, ...]
    balance_invariants: Mapping[str, int]
    preserve_source_count_flags: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CORRECTION_PLAN_SCHEMA_VERSION,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "actions": [item.to_dict() for item in self.actions],
            "errors": list(self.errors),
            "balance_invariants": dict(self.balance_invariants),
            "preserve_source_count_flags": self.preserve_source_count_flags,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)


def _action(
    evidence_id: str,
    index: int,
    kind: CorrectionActionKind,
    item: CorrectionEvidence,
    *,
    remote_id: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    delta: Mapping[str, int],
    reason: str,
    audit_only: bool = False,
) -> CorrectionAction:
    return CorrectionAction(
        action_id=_digest({"evidence_id": evidence_id, "index": index, "kind": kind.value}),
        kind=kind,
        target_asset=item.target_asset,
        amount=item.amount,
        remote_id=remote_id,
        expected_before=dict(before),
        desired_after=dict(after),
        balance_delta={key: int(value) for key, value in delta.items()},
        reason=reason,
        audit_only=audit_only,
    )


def build_correction_plan(
    items: Iterable[CorrectionEvidence | Mapping[str, Any]],
) -> CorrectionPlan:
    normalized: list[CorrectionEvidence] = []
    errors: list[str] = []
    for index, raw in enumerate(items):
        try:
            item = _coerce_evidence(raw)
        except (TypeError, ValueError, KeyError) as error:
            errors.append(f"item {index}: invalid evidence ({error})")
            continue
        normalized.append(item)
        if item.target_asset not in SUPPORTED_CORRECTION_TARGETS:
            errors.append(f"item {index}: unsupported target asset")
        if not item.source_asset:
            errors.append(f"item {index}: source asset is required")
        if item.target_receive_count != 1:
            errors.append(f"item {index}: target receive count must be exactly one")
        if item.source_count_state not in {"ON", "OFF"}:
            errors.append(f"item {index}: source count state must be ON or OFF")
        if item.compensation_mode not in {"per_item", "aggregate_audit"}:
            errors.append(f"item {index}: unsupported compensation mode")
        if item.compensation_expected_count_state and item.compensation_expected_count_state not in {"ON", "OFF"}:
            errors.append(f"item {index}: compensation count state must be ON or OFF")
        required = {
            "source_message_id": item.source_message_id,
            "target_message_id": item.target_message_id,
            "source_remote_id": item.source_remote_id,
            "target_remote_id": item.target_remote_id,
            "wrong_expense_remote_id": item.wrong_expense_remote_id,
        }
        for name, value in required.items():
            if not value:
                errors.append(f"item {index}: missing {name}")
        if not item.source_evidence or not item.target_evidence:
            errors.append(f"item {index}: source and target evidence are required")
        receive_ids = item.target_evidence.get("receive_remote_ids")
        if not isinstance(receive_ids, (list, tuple)):
            errors.append(f"item {index}: target receive remote identities are required")
        else:
            normalized_receive_ids = tuple(_text(value) for value in receive_ids if _text(value))
            if len(normalized_receive_ids) != item.target_receive_count:
                errors.append(f"item {index}: target receive identity count does not match evidence")
            if item.target_remote_id not in normalized_receive_ids:
                errors.append(f"item {index}: target remote identity is not one of the proven receives")
        receive_rows = item.target_evidence.get("receive_rows")
        if not isinstance(receive_rows, (list, tuple)) or len(receive_rows) != item.target_receive_count:
            errors.append(f"item {index}: target receive row evidence is required for every receive")
        elif item.target_remote_id not in {
            _text(row.get("remote_id") or row.get("id"))
            for row in receive_rows
            if isinstance(row, Mapping)
        }:
            errors.append(f"item {index}: target receive row evidence identity is missing")
        source_proof = item.source_evidence
        if _text(source_proof.get("asset_name")) != item.source_asset:
            errors.append(f"item {index}: source asset evidence does not match source asset")
        if _text(source_proof.get("message_id")) != item.source_message_id:
            errors.append(f"item {index}: source identity evidence does not match source message")
        if not item.ordinary_income_remote_id and not item.compensation_remote_id:
            errors.append(f"item {index}: compensation remote identity is required")
        if not item.ordinary_income_remote_id and item.compensation_mode != "aggregate_audit":
            errors.append(f"item {index}: per-item compensation is unsupported; use explicit aggregate audit evidence")
        if not item.ordinary_income_remote_id and not isinstance(item.target_evidence.get("compensation_row"), Mapping):
            errors.append(f"item {index}: compensation row evidence is required")
        if not item.ordinary_income_remote_id and item.compensation_mode == "aggregate_audit":
            compensation_row = item.target_evidence.get("compensation_row")
            if not isinstance(compensation_row, Mapping):
                errors.append(f"item {index}: aggregate compensation row evidence is required")
            else:
                row_id = _text(compensation_row.get("remote_id") or compensation_row.get("id"))
                if row_id != item.compensation_remote_id:
                    errors.append(f"item {index}: aggregate compensation identity does not match evidence")
                if not item.compensation_expected_date:
                    errors.append(f"item {index}: aggregate compensation date evidence is required")
                if item.compensation_expected_amount <= 0:
                    errors.append(f"item {index}: aggregate compensation amount evidence is required")
                if item.compensation_expected_signed_amount == 0:
                    errors.append(f"item {index}: aggregate compensation sign evidence is required")
                if not item.compensation_expected_kind:
                    errors.append(f"item {index}: aggregate compensation kind evidence is required")
                if not item.compensation_expected_content:
                    errors.append(f"item {index}: aggregate compensation content evidence is required")
                if not item.compensation_expected_count_state:
                    errors.append(f"item {index}: aggregate compensation count evidence is required")
                try:
                    if _row_date(compensation_row) != item.compensation_expected_date:
                        errors.append(f"item {index}: aggregate compensation date does not match evidence")
                    if _row_amount(compensation_row) != item.compensation_expected_amount:
                        errors.append(f"item {index}: aggregate compensation amount does not match evidence")
                    if int(compensation_row.get("signed_amount")) != item.compensation_expected_signed_amount:
                        errors.append(f"item {index}: aggregate compensation sign does not match evidence")
                except (TypeError, ValueError):
                    errors.append(f"item {index}: aggregate compensation identity fields are invalid")
                if _text(compensation_row.get("asset_name")) != item.target_asset:
                    errors.append(f"item {index}: aggregate compensation asset does not match evidence")
                if _text(compensation_row.get("kind")).casefold() != item.compensation_expected_kind:
                    errors.append(f"item {index}: aggregate compensation kind evidence is required")
                if _text(compensation_row.get("content")) != item.compensation_expected_content:
                    errors.append(f"item {index}: aggregate compensation content evidence is required")
                if item.compensation_expected_count_state and _text(compensation_row.get("count_state")).upper() != item.compensation_expected_count_state:
                    errors.append(f"item {index}: aggregate compensation count evidence does not match")
        if item.ordinary_income_remote_id and not item.ordinary_income_confirmed_duplicate:
            errors.append(f"item {index}: ordinary income duplicate is unproven")

    if not normalized and not errors:
        errors.append("no correction evidence")

    seen_remote_ids: set[str] = set()
    for index, item in enumerate(normalized):
        ids = (
            item.source_remote_id,
            item.target_remote_id,
            item.wrong_expense_remote_id,
            item.compensation_remote_id,
            item.ordinary_income_remote_id,
        )
        for remote_id in ids:
            if remote_id and remote_id in seen_remote_ids:
                errors.append(f"item {index}: remote row is reused")
            if remote_id:
                seen_remote_ids.add(remote_id)

    actions: list[CorrectionAction] = []
    balance_invariants: dict[str, int] = {}
    for item in normalized:
        evidence_id = item.evidence_id
        # A legacy ordinary income row proves that a native receive already
        # represents the balance increase, so reassignment has no new delta.
        aggregate_audit = item.compensation_mode == "aggregate_audit" and not item.ordinary_income_remote_id
        native_delta = {} if item.ordinary_income_remote_id or aggregate_audit else {item.target_asset: item.amount}
        actions.append(
            _action(
                evidence_id,
                len(actions),
                CorrectionActionKind.REASSIGN_NATIVE_TRANSFER,
                item,
                remote_id=item.source_remote_id,
                before={"kind": "source_card_expense", "count": item.source_count_state},
                after={
                    "kind": "native_transfer",
                    "source": item.source_asset,
                    "target": item.target_asset,
                    "target_remote_id": item.target_remote_id,
                    "target_receive_count": item.target_receive_count,
                },
                delta=native_delta,
                reason="evidence-backed source-to-target native transfer",
                audit_only=aggregate_audit,
            )
        )
        actions.append(
            _action(
                evidence_id,
                len(actions),
                CorrectionActionKind.QUARANTINE_WRONG_EXPENSE,
                item,
                remote_id=item.wrong_expense_remote_id,
                before={"date": item.date, "amount": item.amount, "asset_name": item.target_asset},
                after={"asset_name": "なし", "count": "OFF", "date": item.date, "amount": item.amount},
                delta={} if aggregate_audit else {item.target_asset: item.amount},
                reason="retain original date and amount; quarantine unsupported expense",
                audit_only=True,
            )
        )
        if item.ordinary_income_remote_id:
            actions.append(
                _action(
                    evidence_id,
                    len(actions),
                    CorrectionActionKind.REMOVE_DUPLICATE_INCOME,
                    item,
                    remote_id=item.ordinary_income_remote_id,
                    before={"kind": "ordinary_income", "amount": item.amount, "date": item.date, "asset_name": item.target_asset},
                    after={"count": "OFF", "remote_id": item.ordinary_income_remote_id},
                    delta={item.target_asset: -item.amount},
                    reason="remove only an explicitly proven duplicate income",
                    audit_only=True,
                )
            )
        elif item.compensation_mode == "aggregate_audit":
            actions.append(
                _action(
                    evidence_id,
                    len(actions),
                    CorrectionActionKind.BALANCE_COMPENSATION,
                    item,
                    remote_id=item.compensation_remote_id,
                    before={"kind": "aggregate_compensation_audit"},
                    after={
                        "kind": item.compensation_expected_kind,
                        "content": item.compensation_expected_content,
                        "signed_amount": item.compensation_expected_signed_amount,
                        "amount": item.compensation_expected_amount,
                        "count": item.compensation_expected_count_state,
                        "remote_id": item.compensation_remote_id,
                    },
                    delta={},
                    reason="verify explicit aggregate compensation; do not recalculate a per-item formula",
                    audit_only=True,
                )
            )
        else:
            actions.append(
                _action(
                    evidence_id,
                    len(actions),
                    CorrectionActionKind.BALANCE_COMPENSATION,
                    item,
                    remote_id=item.compensation_remote_id,
                    before={"kind": "no_compensation"},
                    after={
                        "kind": "explicit_compensation",
                        "amount": -2 * item.amount,
                        "remote_id": item.compensation_remote_id,
                    },
                    delta={item.target_asset: -2 * item.amount},
                    reason="offset native receive and quarantined expense without guessing income",
                )
            )

    for action in actions:
        for asset, delta in action.balance_delta.items():
            balance_invariants[asset] = balance_invariants.get(asset, 0) + delta

    plan_payload = {
        "schema_version": CORRECTION_PLAN_SCHEMA_VERSION,
        "evidence": [item.to_dict() for item in normalized],
        "actions": [action.to_dict() for action in actions],
        "errors": errors,
    }
    return CorrectionPlan(
        plan_id=_digest(plan_payload),
        status=CorrectionPlanStatus.BLOCKED if errors else CorrectionPlanStatus.READY,
        evidence=tuple(normalized),
        actions=tuple(actions if not errors else ()),
        errors=tuple(dict.fromkeys(errors)),
        balance_invariants=balance_invariants,
    )


class CorrectionAdapter(Protocol):
    def read_action(self, action: CorrectionAction) -> Mapping[str, Any]: ...
    def apply_action(self, action: CorrectionAction) -> None: ...
    def matches(self, action: CorrectionAction, snapshot: Mapping[str, Any], phase: str) -> bool: ...
    def read_balance(self, asset: str) -> int: ...


class CorrectionAuditLog(Protocol):
    def append(self, event: Mapping[str, Any]) -> None: ...


class MemoryCorrectionAuditLog:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


class JsonlCorrectionAuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n")


@dataclass(frozen=True)
class CorrectionRunReport:
    status: CorrectionRunStatus
    plan_id: str
    action_statuses: Mapping[str, str]
    blocked_reason: str
    before_balances: Mapping[str, int]
    after_balances: Mapping[str, int]
    writes_count: int = 0

    @property
    def writes_attempted(self) -> int:
        return self.writes_count


class CorrectionExecutor:
    def __init__(self, adapter: CorrectionAdapter, audit: CorrectionAuditLog | None = None, *, dry_run: bool = True) -> None:
        self.adapter = adapter
        self.audit = audit or MemoryCorrectionAuditLog()
        self.dry_run = bool(dry_run)

    @staticmethod
    def _event(plan_id: str, action: CorrectionAction, status: str, **extra: Any) -> dict[str, Any]:
        event = {
            "schema_version": CORRECTION_PLAN_SCHEMA_VERSION,
            "plan_id": plan_id,
            "action_id": action.action_id,
            "kind": action.kind.value,
            "remote_id": action.remote_id,
            "status": status,
        }
        event.update(extra)
        return event

    def _read_action_safely(self, action: CorrectionAction) -> Mapping[str, Any]:
        try:
            return self.adapter.read_action(action)
        except Exception as error:
            return {
                "phase": "unknown",
                "reason": "read_action_exception",
                "error_type": type(error).__name__,
            }

    def _read_balances_safely(self, assets: set[str]) -> tuple[dict[str, int], str]:
        try:
            return {asset: int(self.adapter.read_balance(asset)) for asset in assets}, ""
        except Exception as error:
            return {}, f"balance read failed ({type(error).__name__})"

    def run(self, plan: CorrectionPlan, *, dry_run: bool | None = None) -> CorrectionRunReport:
        execute_live = not (self.dry_run if dry_run is None else bool(dry_run))
        assets = set(plan.balance_invariants)
        # A dry-run must remain a browser readback only. Balance reads are
        # part of the live invariant check and would otherwise force a
        # separate account-balance UI read before any action status is known.
        if plan.status is CorrectionPlanStatus.BLOCKED:
            return CorrectionRunReport(CorrectionRunStatus.BLOCKED, plan.plan_id, {}, "; ".join(plan.errors), {}, {})
        if execute_live and any(
            action.kind is CorrectionActionKind.BALANCE_COMPENSATION and action.audit_only
            for action in plan.actions
        ):
            return CorrectionRunReport(
                CorrectionRunStatus.BLOCKED,
                plan.plan_id,
                {action.action_id: "blocked" for action in plan.actions},
                "aggregate compensation evidence is audit-only; live apply is unsupported",
                {},
                {},
                0,
            )
        if execute_live:
            before, balance_error = self._read_balances_safely(assets)
            if balance_error:
                return CorrectionRunReport(
                    CorrectionRunStatus.UNKNOWN,
                    plan.plan_id,
                    {},
                    f"{balance_error} before live correction",
                    {},
                    {},
                    0,
                )
        else:
            before = {}

        statuses: dict[str, str] = {}
        writes = 0
        if not execute_live:
            readback_gate_failed = False
            for action in plan.actions:
                if action.kind is CorrectionActionKind.BALANCE_COMPENSATION and readback_gate_failed:
                    # Do not even treat the compensation's own row as an
                    # executable candidate after another required readback
                    # is uncertain.
                    status = "blocked"
                else:
                    snapshot = self._read_action_safely(action)
                    if snapshot.get("phase") == "read_error":
                        status = "read_error"
                        readback_gate_failed = True
                    elif snapshot.get("phase") == "unknown":
                        status = "unknown"
                        readback_gate_failed = True
                    else:
                        status = "already_after" if self.adapter.matches(action, snapshot, "after") else "would_apply"
                statuses[action.action_id] = status
                audit_status = "blocked" if status in {"read_error", "unknown", "blocked"} else "dry_run"
                self.audit.append(self._event(plan.plan_id, action, audit_status, would_apply=status == "would_apply", read_error=status == "read_error"))
            if readback_gate_failed:
                return CorrectionRunReport(
                    CorrectionRunStatus.BLOCKED,
                    plan.plan_id,
                    statuses,
                    "readback uncertainty blocks the complete correction plan",
                    before,
                    dict(before),
                    0,
                )
            return CorrectionRunReport(CorrectionRunStatus.DRY_RUN, plan.plan_id, statuses, "", before, dict(before), 0)

        # Live execution has one complete read preflight.  Every action is
        # read before the first write; a later action's read error can never
        # arrive after an earlier action has already been applied.
        pending_deltas: dict[str, int] = {}
        preflight_snapshots: dict[str, Mapping[str, Any]] = {}
        preflight_failures: list[tuple[str, str, str]] = []
        for action in plan.actions:
            if action.kind is CorrectionActionKind.BALANCE_COMPENSATION and action.audit_only:
                statuses[action.action_id] = "blocked"
                reason = f"audit-only action cannot be executed live: {action.action_id}"
                self.audit.append(self._event(plan.plan_id, action, "blocked", reason=reason))
                return CorrectionRunReport(CorrectionRunStatus.BLOCKED, plan.plan_id, statuses, reason, before, dict(before), writes)
            snapshot = self._read_action_safely(action)
            preflight_snapshots[action.action_id] = snapshot
            phase = snapshot.get("phase")
            if phase == "unknown":
                statuses[action.action_id] = "unknown"
                preflight_failures.append(
                    (action.action_id, "unknown", f"read-back outcome unknown for {action.action_id}")
                )
                continue
            if phase == "read_error":
                statuses[action.action_id] = "read_error"
                preflight_failures.append(
                    (action.action_id, "read_error", f"read-back failed for {action.action_id}")
                )
                continue
            if self.adapter.matches(action, snapshot, "after"):
                statuses[action.action_id] = "already_after"
                continue
            if not self.adapter.matches(action, snapshot, "before"):
                statuses[action.action_id] = "blocked"
                preflight_failures.append(
                    (action.action_id, "blocked", f"unexpected pre-state for {action.action_id}")
                )
                continue
            for asset, delta in action.balance_delta.items():
                pending_deltas[asset] = pending_deltas.get(asset, 0) + int(delta)

        if preflight_failures:
            _failed_action_id, _failed_kind, reason = preflight_failures[0]
            for failed_action_id, failed_kind, failed_reason in preflight_failures:
                self.audit.append(
                    self._event(
                        plan.plan_id,
                        next(action for action in plan.actions if action.action_id == failed_action_id),
                        "blocked" if failed_kind != "unknown" else "unknown",
                        reason=failed_reason,
                    )
                )
            for action in plan.actions:
                statuses.setdefault(action.action_id, "preflight_ok")
            report_status = (
                CorrectionRunStatus.UNKNOWN
                if any(kind == "unknown" for _id, kind, _reason in preflight_failures)
                else CorrectionRunStatus.BLOCKED
            )
            return CorrectionRunReport(report_status, plan.plan_id, statuses, reason, before, dict(before), 0)

        for action in plan.actions:
            snapshot = preflight_snapshots[action.action_id]
            if self.adapter.matches(action, snapshot, "after"):
                statuses[action.action_id] = "verified"
                self.audit.append(self._event(plan.plan_id, action, "verified", resumed=True))
                continue
            if not self.adapter.matches(action, snapshot, "before"):
                reason = f"unexpected pre-state for {action.action_id}"
                statuses[action.action_id] = "blocked"
                self.audit.append(self._event(plan.plan_id, action, "blocked", reason=reason))
                after, balance_error = self._read_balances_safely(assets)
                return CorrectionRunReport(CorrectionRunStatus.BLOCKED, plan.plan_id, statuses, f"{reason}; {balance_error}" if balance_error else reason, before, after, writes)

            self.audit.append(self._event(plan.plan_id, action, "started"))
            writes += 1
            try:
                self.adapter.apply_action(action)
            except Exception as error:
                reread = self._read_action_safely(action)
                self.audit.append(self._event(plan.plan_id, action, "unknown", error=type(error).__name__))
                if self.adapter.matches(action, reread, "after"):
                    statuses[action.action_id] = "verified"
                    self.audit.append(self._event(plan.plan_id, action, "verified_after_unknown_readback"))
                    continue
                statuses[action.action_id] = "unknown"
                reason = f"save outcome unknown for {action.action_id}"
                after, balance_error = self._read_balances_safely(assets)
                return CorrectionRunReport(CorrectionRunStatus.UNKNOWN, plan.plan_id, statuses, f"{reason}; {balance_error}" if balance_error else reason, before, after, writes)

            reread = self._read_action_safely(action)
            if reread.get("phase") == "unknown":
                statuses[action.action_id] = "unknown"
                reason = f"post-write read-back outcome unknown for {action.action_id}"
                self.audit.append(self._event(plan.plan_id, action, "unknown", reason=reason))
                after, balance_error = self._read_balances_safely(assets)
                return CorrectionRunReport(CorrectionRunStatus.UNKNOWN, plan.plan_id, statuses, f"{reason}; {balance_error}" if balance_error else reason, before, after, writes)
            if not self.adapter.matches(action, reread, "after"):
                statuses[action.action_id] = "unknown"
                reason = f"post-write readback did not prove desired state for {action.action_id}"
                self.audit.append(self._event(plan.plan_id, action, "unknown", reason=reason))
                after, balance_error = self._read_balances_safely(assets)
                return CorrectionRunReport(CorrectionRunStatus.UNKNOWN, plan.plan_id, statuses, f"{reason}; {balance_error}" if balance_error else reason, before, after, writes)
            statuses[action.action_id] = "verified"
            self.audit.append(self._event(plan.plan_id, action, "verified"))

        after, balance_error = self._read_balances_safely(assets)
        if balance_error:
            return CorrectionRunReport(CorrectionRunStatus.UNKNOWN, plan.plan_id, statuses, balance_error, before, after, writes)
        for asset, expected_delta in pending_deltas.items():
            if after[asset] - before[asset] != expected_delta:
                reason = f"balance invariant failed for {asset}"
                self.audit.append({"schema_version": CORRECTION_PLAN_SCHEMA_VERSION, "plan_id": plan.plan_id, "action_id": "", "kind": "balance_invariant", "status": "blocked", "asset": asset, "reason": reason})
                return CorrectionRunReport(CorrectionRunStatus.BLOCKED, plan.plan_id, statuses, reason, before, after, writes)
        return CorrectionRunReport(CorrectionRunStatus.COMPLETED, plan.plan_id, statuses, "", before, after, writes)
