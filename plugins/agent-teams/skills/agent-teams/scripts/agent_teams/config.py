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
    # Money, in USD (the currency of the price table). None means no
    # limit. At the hard limit the engine starts no new attempt; it never
    # stops one that is running (v2.1 spec 11.2).
    "budget_usd": None,
    "budget_per_task_usd": None,
    # The share of the budget at which the board warns.
    "budget_warn_ratio": 0.8,
    # A declared comparable run (v2.1 spec 19): {"name": str, "executors": int}.
    # Teams that declare one benchmark name may be compared; no other
    # comparison is made.
    "benchmark": None,
}

WORKSPACE_MODES = ("auto", "worktree", "shared")

# The keys that came with v2.1 (spec 11.2 and 19); checked in one place.
BUDGET_KEYS = frozenset({"budget_usd", "budget_per_task_usd", "budget_warn_ratio",
                         "benchmark"})

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
    for key in ("budget_usd", "budget_per_task_usd"):
        value = config[key]
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise UsageError(f"config.{key} must be a number > 0 or null, got {value!r}")
    ratio = config["budget_warn_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) \
            or not 0 < ratio <= 1:
        raise UsageError(
            f"config.budget_warn_ratio must be a number above 0 and at most 1, got {ratio!r}")
    benchmark = config["benchmark"]
    if benchmark is not None:
        if not isinstance(benchmark, dict) or set(benchmark) != {"name", "executors"} \
                or not isinstance(benchmark["name"], str) or not benchmark["name"].strip() \
                or isinstance(benchmark["executors"], bool) \
                or not isinstance(benchmark["executors"], int) or benchmark["executors"] < 1:
            raise UsageError(
                'config.benchmark must be an object {"name": "<text>", "executors": <int >= 1>}')


def merge_config(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return DEFAULTS updated with ``overrides``; raises UsageError on bad values."""
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise UsageError("config must be a JSON object")
    config = {**DEFAULTS, **overrides}
    _check_config(config)
    return config
