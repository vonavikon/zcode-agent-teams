"""The JSON schema and validate_team_spec must agree on structural rules."""
from __future__ import annotations

import json
import pathlib
from functools import lru_cache

import pytest

from agent_teams.validation import validate_team_spec

REPO = pathlib.Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def schema():
    jsonschema = pytest.importorskip("jsonschema")
    raw = json.loads((REPO / "schemas" / "tasks.schema.json").read_text(encoding="utf-8"))
    return jsonschema, jsonschema.Draft202012Validator(
        raw, format_checker=jsonschema.FormatChecker())


def schema_ok(spec) -> bool:
    _, validator = schema()
    return not list(validator.iter_errors(spec))


def engine_structural_ok(spec, root) -> bool:
    """Only the rules the schema can express: types, required, unknown keys."""
    from agent_teams.validation import (
        NAME_RE, normalize_acceptance, normalize_path,
    )
    try:
        if not isinstance(spec, dict):
            return False
        if set(spec) - {"tasks", "config", "agents", "title", "goal"}:
            return False
        for key, limit in (("title", 120), ("goal", 1000)):
            if key in spec and (not isinstance(spec[key], str) or len(spec[key]) > limit):
                return False
        config = spec.get("config")
        if config is not None:
            if not isinstance(config, dict) or set(config) - {
                    "workspace", "allow_write_overlaps", "allow_write_overlaps_reason",
                    "max_verification_failures", "max_execution_retries",
                    "max_agent_restarts", "execution_timeout_s", "heartbeat_timeout_s",
                    "lock_timeout_s", "budget_usd", "budget_per_task_usd",
                    "budget_warn_ratio", "benchmark"}:
                return False
            int_limits = {"max_verification_failures": 1,
                          "max_execution_retries": 0, "max_agent_restarts": 0}
            for key, minimum in int_limits.items():
                value = config.get(key)
                if key in config and (isinstance(value, bool)
                                      or not isinstance(value, int) or value < minimum):
                    return False
            for key in ("execution_timeout_s", "heartbeat_timeout_s", "lock_timeout_s"):
                value = config.get(key)
                if key in config and (isinstance(value, bool)
                                      or not isinstance(value, (int, float)) or value <= 0):
                    return False
            for key in ("budget_usd", "budget_per_task_usd"):
                value = config.get(key)
                if key in config and value is not None and (
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        or value <= 0):
                    return False
            ratio = config.get("budget_warn_ratio")
            if "budget_warn_ratio" in config and (
                    isinstance(ratio, bool) or not isinstance(ratio, (int, float))
                    or not 0 < ratio <= 1):
                return False
            benchmark = config.get("benchmark")
            if "benchmark" in config and benchmark is not None and (
                    not isinstance(benchmark, dict)
                    or set(benchmark) != {"name", "executors"}
                    or not isinstance(benchmark["name"], str) or not benchmark["name"]
                    or isinstance(benchmark["executors"], bool)
                    or not isinstance(benchmark["executors"], int)
                    or benchmark["executors"] < 1):
                return False
            if "workspace" in config and config["workspace"] not in (
                    "auto", "worktree", "shared"):
                return False
            if "allow_write_overlaps" in config and not isinstance(
                    config["allow_write_overlaps"], bool):
                return False
        agents = spec.get("agents")
        if agents is not None:
            if not isinstance(agents, list):
                return False
            for agent in agents:
                if not isinstance(agent, dict) or set(agent) - {"id", "role"}:
                    return False
                if not NAME_RE.match(agent.get("id") or ""):
                    return False
        tasks = spec.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return False
        for task in tasks:
            if not isinstance(task, dict):
                return False
            if set(task) - {"id", "title", "assignee", "lens", "files_read",
                            "files_write", "acceptance", "deps", "description"}:
                return False
            if "description" in task and (
                    not isinstance(task["description"], str)
                    or len(task["description"]) > 500):
                return False
            for must in ("id", "title", "assignee", "acceptance"):
                if must not in task:
                    return False
            if not NAME_RE.match(task["id"] or ""):
                return False
            if not NAME_RE.match(task["assignee"] or ""):
                return False
            if not isinstance(task["title"], str) or not task["title"].strip():
                return False
            if "status" in task:
                return False
            for key in ("files_read", "files_write", "deps"):
                if key in task and (not isinstance(task[key], list) or not all(
                        isinstance(p, str) and p for p in task[key])):
                    return False
            for key in ("files_read", "files_write"):
                for raw in task.get(key, []):
                    normalize_path(root, raw, check_fs=False)
            try:
                normalize_acceptance(task["acceptance"])
            except Exception:
                return False
        return True
    except Exception:
        return False


SPECS = {
    "minimal-ok": {"tasks": [{"id": "t1", "title": "d", "assignee": "w1",
                              "acceptance": ["x"]}]},
    "with-everything": {
        "config": {"workspace": "worktree"},
        "agents": [{"id": "w1", "role": "executor"}],
        "tasks": [{"id": "t1", "title": "d", "assignee": "w1", "lens": "code",
                   "files_read": ["a.py"], "files_write": ["out.md"],
                   "deps": [], "acceptance": ["x", {"id": "AC-1",
                   "description": "y", "type": "test", "required": True,
                   "verification": "pytest a.py"}]}]},
    "with-words": {
        "title": "Market study",
        "goal": "Compare the competitors and prepare recommendations",
        "tasks": [{"id": "t1", "title": "d", "description": "what this is about",
                   "assignee": "w1", "acceptance": ["x"]}]},
    "number-title": {"title": 5, "tasks": [{"id": "t1", "title": "d",
                                            "assignee": "w1", "acceptance": ["x"]}]},
    "long-title": {"title": "x" * 121, "tasks": [{"id": "t1", "title": "d",
                                                  "assignee": "w1", "acceptance": ["x"]}]},
    "long-task-description": {"tasks": [{"id": "t1", "title": "d", "description": "x" * 501,
                                         "assignee": "w1", "acceptance": ["x"]}]},
    "list-goal": {"goal": ["compare"], "tasks": [{"id": "t1", "title": "d",
                                                  "assignee": "w1", "acceptance": ["x"]}]},
    "with-budget": {"config": {"budget_usd": 10, "budget_per_task_usd": 2,
                               "budget_warn_ratio": 0.9,
                               "benchmark": {"name": "billing", "executors": 3}},
                    "tasks": [{"id": "t1", "title": "d", "assignee": "w1", "acceptance": ["x"]}]},
    "no-budget": {"config": {"budget_usd": None}, "tasks": [{"id": "t1", "title": "d",
                                                              "assignee": "w1", "acceptance": ["x"]}]},
    "budget-free": {"config": {"budget_usd": 0}, "tasks": [{"id": "t1", "title": "d",
                                                            "assignee": "w1", "acceptance": ["x"]}]},
    "budget-negative": {"config": {"budget_usd": -1}, "tasks": [{"id": "t1", "title": "d",
                                                                 "assignee": "w1", "acceptance": ["x"]}]},
    "budget-ratio-out": {"config": {"budget_warn_ratio": 1.5}, "tasks": [{"id": "t1", "title": "d",
                                                                          "assignee": "w1", "acceptance": ["x"]}]},
    "benchmark-bad": {"config": {"benchmark": {"name": "x"}}, "tasks": [{"id": "t1", "title": "d",
                                                                        "assignee": "w1", "acceptance": ["x"]}]},
    "benchmark-zero": {"config": {"benchmark": {"name": "x", "executors": 0}}, "tasks": [{"id": "t1", "title": "d",
                                                                                       "assignee": "w1", "acceptance": ["x"]}]},
    "empty-tasks": {"tasks": []},
    "missing-tasks": {"config": {}},
    "not-object": ["nope"],
    "unknown-top-key": {"tsaks": [], "tasks": [{"id": "t1", "title": "d",
                                                 "assignee": "w1", "acceptance": ["x"]}]},
    "unknown-task-key": {"tasks": [{"id": "t1", "title": "d", "assignee": "w1",
                                    "file_write": ["x"], "acceptance": ["x"]}]},
    "bad-id": {"tasks": [{"id": "../x", "title": "d", "assignee": "w1",
                          "acceptance": ["x"]}]},
    "bad-assignee": {"tasks": [{"id": "t1", "title": "d", "assignee": "!!",
                                "acceptance": ["x"]}]},
    "no-title": {"tasks": [{"id": "t1", "assignee": "w1", "acceptance": ["x"]}]},
    "empty-acceptance": {"tasks": [{"id": "t1", "title": "d", "assignee": "w1",
                                    "acceptance": []}]},
    "bad-acceptance-type": {"tasks": [{"id": "t1", "title": "d", "assignee": "w1",
                                       "acceptance": [{"description": "x", "type": "vibe"}]}]},
    "bad-config-limit": {"tasks": [{"id": "t1", "title": "d", "assignee": "w1",
                                    "acceptance": ["x"]}],
                         "config": {"max_verification_failures": 0}},
    "status-in-spec": {"tasks": [{"id": "t1", "title": "d", "assignee": "w1",
                                  "acceptance": ["x"], "status": "pending"}]},
}


@pytest.mark.parametrize("name", sorted(SPECS))
def test_schema_matches_engine(name, tmp_path):
    _, _validator = schema()
    spec = SPECS[name]
    assert schema_ok(spec) == engine_structural_ok(spec, tmp_path), name


def test_example_files_parse_and_validate(tmp_path):
    for path in sorted((REPO / "examples").glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        result = validate_team_spec(spec, REPO)
        assert result.ok, f"{path.name}: {[i.message for i in result.errors]}"
