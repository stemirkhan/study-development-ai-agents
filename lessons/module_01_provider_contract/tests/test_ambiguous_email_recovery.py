from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

import pytest

from lessons.module_01_provider_contract.implementation.contracts import (
    ToolCallOutput,
)


class OperationState(str, Enum):
    SENDING = "sending"
    EFFECT_UNKNOWN = "effect_unknown"
    SUCCEEDED = "succeeded"


class LookupResult(str, Enum):
    FOUND = "found"
    TERMINALLY_ABSENT = "terminally_absent"
    UNKNOWN = "unknown"


class InjectedProcessCrash(RuntimeError):
    pass


@dataclass(slots=True)
class StoredOperation:
    operation_id: str
    tool_call: ToolCallOutput
    state: OperationState


class DurableOperationStore:
    """A deterministic stand-in for state that survives a process restart."""

    def __init__(self) -> None:
        self._operations: dict[str, StoredOperation] = {}
        self.transitions: list[tuple[str, OperationState]] = []

    def create(self, operation_id: str, tool_call: ToolCallOutput) -> None:
        if operation_id in self._operations:
            raise ValueError(f"operation already exists: {operation_id}")
        self._operations[operation_id] = StoredOperation(
            operation_id=operation_id,
            tool_call=tool_call,
            state=OperationState.SENDING,
        )
        self.transitions.append((operation_id, OperationState.SENDING))

    def load(self, operation_id: str) -> StoredOperation:
        return self._operations[operation_id]

    def mark(self, operation_id: str, state: OperationState) -> None:
        self.load(operation_id).state = state
        self.transitions.append((operation_id, state))


@dataclass(frozen=True, slots=True)
class SendOutcome:
    accepted: bool
    crash_after_attempt: bool


class RecordingMailService:
    """An external service with a scripted terminal reconciliation oracle."""

    def __init__(
        self,
        outcomes: tuple[SendOutcome, ...],
        *,
        lookup_override: LookupResult | None = None,
    ) -> None:
        self._outcomes = deque(outcomes)
        self._lookup_override = lookup_override
        self.actions: list[str] = []
        self.accepted_operation_ids: list[str] = []

    @property
    def send_attempts(self) -> int:
        return sum(action.startswith("send:") for action in self.actions)

    async def send(self, operation: StoredOperation) -> None:
        self.actions.append(f"send:{operation.operation_id}")
        outcome = self._outcomes.popleft()
        if outcome.accepted:
            self.accepted_operation_ids.append(operation.operation_id)
        if outcome.crash_after_attempt:
            raise InjectedProcessCrash(
                "process crashed before it persisted the external outcome"
            )

    async def lookup(self, operation_id: str) -> LookupResult:
        self.actions.append(f"lookup:{operation_id}")
        if self._lookup_override is not None:
            return self._lookup_override
        if operation_id in self.accepted_operation_ids:
            return LookupResult.FOUND
        return LookupResult.TERMINALLY_ABSENT


class EmailRecoveryRuntime:
    """Recover an ambiguous side effect without asking the LLM what happened."""

    def __init__(
        self,
        store: DurableOperationStore,
        mail_service: RecordingMailService,
    ) -> None:
        self._store = store
        self._mail_service = mail_service

    async def start(
        self,
        operation_id: str,
        tool_call: ToolCallOutput,
    ) -> None:
        self._store.create(operation_id, tool_call)
        await self._attempt(operation_id)

    async def recover(self, operation_id: str) -> None:
        operation = self._store.load(operation_id)
        if operation.state not in {
            OperationState.SENDING,
            OperationState.EFFECT_UNKNOWN,
        }:
            raise ValueError("only an unfinished operation can be recovered")

        self._store.mark(operation_id, OperationState.EFFECT_UNKNOWN)
        lookup = await self._mail_service.lookup(operation_id)

        if lookup is LookupResult.FOUND:
            self._store.mark(operation_id, OperationState.SUCCEEDED)
            return
        if lookup is LookupResult.UNKNOWN:
            return
        if lookup is not LookupResult.TERMINALLY_ABSENT:
            raise AssertionError(f"unhandled lookup result: {lookup}")

        self._store.mark(operation_id, OperationState.SENDING)
        await self._attempt(operation_id)

    async def _attempt(self, operation_id: str) -> None:
        operation = self._store.load(operation_id)
        await self._mail_service.send(operation)
        self._store.mark(operation_id, OperationState.SUCCEEDED)


@pytest.mark.parametrize(
    (
        "outcomes",
        "lookup_override",
        "expected_send_attempts",
        "expected_state",
        "expected_transitions",
        "expected_actions",
    ),
    [
        pytest.param(
            (SendOutcome(accepted=True, crash_after_attempt=True),),
            None,
            1,
            OperationState.SUCCEEDED,
            (
                OperationState.SENDING,
                OperationState.EFFECT_UNKNOWN,
                OperationState.SUCCEEDED,
            ),
            ("send:email-op-1", "lookup:email-op-1"),
            id="accepted-before-crash",
        ),
        pytest.param(
            (
                SendOutcome(accepted=False, crash_after_attempt=True),
                SendOutcome(accepted=True, crash_after_attempt=False),
            ),
            None,
            2,
            OperationState.SUCCEEDED,
            (
                OperationState.SENDING,
                OperationState.EFFECT_UNKNOWN,
                OperationState.SENDING,
                OperationState.SUCCEEDED,
            ),
            (
                "send:email-op-1",
                "lookup:email-op-1",
                "send:email-op-1",
            ),
            id="not-accepted-before-crash",
        ),
        pytest.param(
            (SendOutcome(accepted=True, crash_after_attempt=True),),
            LookupResult.UNKNOWN,
            1,
            OperationState.EFFECT_UNKNOWN,
            (
                OperationState.SENDING,
                OperationState.EFFECT_UNKNOWN,
            ),
            ("send:email-op-1", "lookup:email-op-1"),
            id="lookup-cannot-prove-outcome",
        ),
    ],
)
async def test_recovery_reconciles_before_retrying_ambiguous_email(
    outcomes: tuple[SendOutcome, ...],
    lookup_override: LookupResult | None,
    expected_send_attempts: int,
    expected_state: OperationState,
    expected_transitions: tuple[OperationState, ...],
    expected_actions: tuple[str, ...],
) -> None:
    proposal_without_provider_id = ToolCallOutput(
        call_id=None,
        name="send_email",
        arguments={
            "to": "student@example.com",
            "subject": "Lesson result",
        },
        raw_arguments=None,
    )
    store = DurableOperationStore()
    mail_service = RecordingMailService(
        outcomes,
        lookup_override=lookup_override,
    )

    first_process = EmailRecoveryRuntime(store, mail_service)
    with pytest.raises(InjectedProcessCrash):
        await first_process.start("email-op-1", proposal_without_provider_id)

    assert store.load("email-op-1").state is OperationState.SENDING

    restarted_process = EmailRecoveryRuntime(store, mail_service)
    await restarted_process.recover("email-op-1")

    operation = store.load("email-op-1")
    assert operation.tool_call.call_id is None
    assert operation.operation_id == "email-op-1"
    assert operation.state is expected_state
    assert tuple(state for _, state in store.transitions) == expected_transitions
    assert mail_service.send_attempts == expected_send_attempts
    assert mail_service.actions == list(expected_actions)
    assert mail_service.accepted_operation_ids == ["email-op-1"]
