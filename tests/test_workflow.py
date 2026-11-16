import pytest

from nexusguard.domain.workflow import WorkflowState as S
from nexusguard.domain.workflow import validate_transition


@pytest.mark.parametrize("target", [S.READY_TO_EXECUTE, S.EXECUTING, S.SUCCEEDED])
def test_pending_approval_cannot_jump_to_execution(target):
    with pytest.raises(ValueError, match="Invalid workflow transition"):
        validate_transition(S.WAITING_APPROVAL, target)


def test_approval_resumes_through_revalidation():
    validate_transition(S.WAITING_APPROVAL, S.REVALIDATING)
    validate_transition(S.REVALIDATING, S.READY_TO_EXECUTE)


@pytest.mark.parametrize("terminal", [S.SUCCEEDED, S.REJECTED, S.EXPIRED, S.FAILED])
def test_terminal_workflows_cannot_execute_again(terminal):
    with pytest.raises(ValueError):
        validate_transition(terminal, S.EXECUTING)
