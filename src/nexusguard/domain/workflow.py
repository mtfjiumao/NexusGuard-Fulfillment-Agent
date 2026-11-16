"""Domain transition contract; database CAS and recovery workers come later."""

from enum import StrEnum
from types import MappingProxyType
from typing import Final


class WorkflowState(StrEnum):
    RECEIVED = "received"
    FACT_CHECKING = "fact_checking"
    WAITING_INFORMATION = "waiting_information"
    POLICY_EVALUATING = "policy_evaluating"
    RISK_ASSESSING = "risk_assessing"
    WAITING_APPROVAL = "waiting_approval"
    REVALIDATING = "revalidating"
    READY_TO_EXECUTE = "ready_to_execute"
    EXECUTING = "executing"
    RECONCILING = "reconciling"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


S = WorkflowState
ALLOWED_TRANSITIONS: Final = MappingProxyType(
    {
        S.RECEIVED: frozenset({S.FACT_CHECKING}),
        S.FACT_CHECKING: frozenset({S.POLICY_EVALUATING, S.WAITING_INFORMATION}),
        S.WAITING_INFORMATION: frozenset({S.FACT_CHECKING}),
        S.POLICY_EVALUATING: frozenset({S.RISK_ASSESSING, S.REJECTED, S.WAITING_INFORMATION}),
        S.RISK_ASSESSING: frozenset({S.WAITING_APPROVAL, S.READY_TO_EXECUTE}),
        S.WAITING_APPROVAL: frozenset({S.REVALIDATING, S.REJECTED, S.EXPIRED}),
        S.REVALIDATING: frozenset({S.FACT_CHECKING, S.READY_TO_EXECUTE}),
        S.READY_TO_EXECUTE: frozenset({S.EXECUTING, S.FACT_CHECKING}),
        S.EXECUTING: frozenset({S.SUCCEEDED, S.RECONCILING, S.FAILED}),
        S.RECONCILING: frozenset({S.SUCCEEDED, S.FAILED, S.EXECUTING}),
        S.SUCCEEDED: frozenset(),
        S.REJECTED: frozenset(),
        S.EXPIRED: frozenset(),
        S.FAILED: frozenset(),
    }
)


def validate_transition(current: WorkflowState, target: WorkflowState) -> None:
    """Check topology only. This does not authorize an action or mutate a workflow."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid workflow transition: {current.value} -> {target.value}")
