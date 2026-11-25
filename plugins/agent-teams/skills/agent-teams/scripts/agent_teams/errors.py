"""Engine errors and process exit codes.

Every error the engine reports to a caller is an EngineError subclass with a
stable ``code``. The CLI maps ``code`` to the process exit status, so scripts
and agents can branch on it without parsing message text.
"""
from __future__ import annotations


class EngineError(Exception):
    """Base class for every error reported to the caller."""

    code = 1
    label = "usage_error"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error": self.label, "code": self.code, "message": self.message,
                "detail": self.detail}


class UsageError(EngineError):
    """Invalid arguments or an invalid team specification."""

    code = 1
    label = "usage_error"


class StateRejected(EngineError):
    """Rejected by the state machine or by an engine policy."""

    code = 2
    label = "state_rejected"


class BudgetExceeded(StateRejected):
    """The agreed budget is spent: no new work starts (v2.1 spec 11.2).

    Running executors are never stopped by it; the lead decides.
    """

    code = 2
    label = "budget_exceeded"


class LockTimeoutError(EngineError):
    """The state lock could not be acquired within the wait limit."""

    code = 3
    label = "lock_timeout"


class NotFoundError(EngineError):
    """Team, task or agent does not exist."""

    code = 4
    label = "not_found"


class PermissionDenied(EngineError):
    """A role or lead-token check failed."""

    code = 5
    label = "permission_denied"


class ExternalError(EngineError):
    """An external tool failed: git, a test command, the network, Langfuse."""

    code = 6
    label = "external_failure"


class CorruptStateError(EngineError):
    """An integrity check or a schema migration failed."""

    code = 7
    label = "corrupt_state"
