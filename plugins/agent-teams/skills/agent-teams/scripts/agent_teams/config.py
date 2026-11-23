"""Team limits and options: defaults, merging and checks.

A team's config is the optional ``config`` object of the specification
merged over DEFAULTS. It is stored in ``teams.config_json``. Every limit
here is enforced by the engine and never left to the discipline of an
agent. The keys are the same ones validation.py accepts in a spec.
"""
from __future__ import annotations

from typing import Any

from .errors import UsageError

DEFAULTS: dict[str, Any] = {
    # Failed verification verdicts before a task goes to blocked.
    "max_verification_failures": 2,
    # Extra execution attempts the lead may grant after a block (retry, release).
    "max_execution_retries": 1,
    # Automatic returns to pending after a lost or timed-out executor.
    "max_agent_restarts": 2,
    "execution_timeout_s": 3600,
    "heartbeat_timeout_s": 900,
    "lock_timeout_s": 10.0,
    # auto | worktree | shared
    "workspace": "auto",
    "allow_write_overlaps": False,
    "allow_write_overlaps_reason": "",
}

WORKSPACE_MODES = ("auto", "worktree", "shared")

_INT_KEYS = ("max_verification_failures", "max_execution_retries", "max_agent_restarts")
_NUMBER_KEYS = ("execution_timeout_s", "heartbeat_timeout_s", "lock_timeout_s")


def _check_config(config: dict[str, Any]) -> None:
    unknown = sorted(set(config) - set(DEFAULTS))
    if unknown:
        raise UsageError(f"unknown config keys: {', '.join(unknown)}")
    for key in _INT_KEYS:
        minimum = 1 if key == "max_verification_failures" else 0
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise UsageError(f"config.{key} must be an integer >= {minimum}, got {value!r}")
    for key in _NUMBER_KEYS:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise UsageError(f"config.{key} must be a number > 0, got {value!r}")
    if config["workspace"] not in WORKSPACE_MODES:
        raise UsageError(
            f"config.workspace must be one of {', '.join(WORKSPACE_MODES)}, got {config['workspace']!r}"
        )
    if not isinstance(config["allow_write_overlaps"], bool):
        raise UsageError("config.allow_write_overlaps must be true or false")
    if not isinstance(config["allow_write_overlaps_reason"], str):
        raise UsageError("config.allow_write_overlaps_reason must be a string")
    if config["allow_write_overlaps"] and not config["allow_write_overlaps_reason"].strip():
        raise UsageError(
            "config.allow_write_overlaps_reason is required when allow_write_overlaps is true: "
            "the lead must explain why two tasks may write the same files"
        )


def merge_config(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return DEFAULTS updated with ``overrides``; raises UsageError on bad values."""
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise UsageError("config must be a JSON object")
    config = {**DEFAULTS, **overrides}
    _check_config(config)
    return config
