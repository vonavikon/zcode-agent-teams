"""Unit tests for agent_teams.validation (spec structure and rules)."""
from __future__ import annotations

import pytest

from agent_teams.validation import (
    validate_team_spec,
    normalize_acceptance,
)


def spec_minimal(**overrides):
    task = {
        "id": "t1", "title": "demo", "assignee": "w1",
        "files_read": [], "files_write": ["out/a.md"],
        "acceptance": ["file out/a.md exists"], "deps": [],
    }
    task.update(overrides)
    return {"tasks": [task]}


def codes(result):
    return [i.code for i in result.errors]


def wcodes(result):
    return [i.code for i in result.warnings]


class TestHappyPath:
    def test_legacy_v02_format_passes(self, tmp_path):
        result = validate_team_spec(spec_minimal(), tmp_path)
        assert result.ok, [i.message for i in result.errors]

    def test_full_format_passes(self, tmp_path):
        spec = {
            "config": {"max_verification_failures": 3, "workspace": "worktree"},
            "agents": [{"id": "w1", "role": "executor"}],
            "tasks": [{
                "id": "t1", "title": "demo", "assignee": "w1", "lens": "code",
                "files_read": ["src/a.py"], "files_write": ["out/a.md"],
                "acceptance": [
                    "free text criterion",
                    {"id": "AC-9", "description": "API returns 200", "type": "test",
                     "verification": "pytest x.py"},
                    {"description": "docs updated", "type": "documentation",
                     "required": False},
                ],
                "deps": [],
            }],
        }
        result = validate_team_spec(spec, tmp_path)
        assert result.ok, [i.message for i in result.errors]


class TestTopLevel:
    def test_not_an_object(self, tmp_path):
        result = validate_team_spec(["tasks"], tmp_path)
        assert codes(result) == ["E_SPEC_NOT_OBJECT"]

    def test_tasks_missing(self, tmp_path):
        result = validate_team_spec({"config": {}}, tmp_path)
        assert "E_TASKS_MISSING" in codes(result)

    def test_tasks_not_array(self, tmp_path):
        result = validate_team_spec({"tasks": "nope"}, tmp_path)
        assert "E_TASKS_NOT_ARRAY" in codes(result)

    def test_empty_team_rejected(self, tmp_path):
        result = validate_team_spec({"tasks": []}, tmp_path)
        assert "E_TASKS_EMPTY" in codes(result)

    def test_unknown_top_key(self, tmp_path):
        spec = spec_minimal()
        spec["tsaks"] = []
        result = validate_team_spec(spec, tmp_path)
        assert "E_UNKNOWN_KEY" in codes(result)
        assert any("tsaks" in i.message for i in result.errors)


class TestConfig:
    @pytest.mark.parametrize("key,value", [
        ("max_verification_failures", 0),
        ("max_execution_retries", -1),
        ("max_agent_restarts", True),
        ("execution_timeout_s", 0),
        ("heartbeat_timeout_s", "soon"),
        ("lock_timeout_s", False),
    ])
    def test_bad_limits(self, tmp_path, key, value):
        spec = spec_minimal()
        spec["config"] = {key: value}
        result = validate_team_spec(spec, tmp_path)
        assert "E_CONFIG_INVALID" in codes(result)

    def test_unknown_key(self, tmp_path):
        spec = spec_minimal()
        spec["config"] = {"ma_verification": 3}
        result = validate_team_spec(spec, tmp_path)
        assert "E_CONFIG_INVALID" in codes(result)

    def test_workspace_mode(self, tmp_path):
        spec = spec_minimal()
        spec["config"] = {"workspace": "cloud"}
        assert "E_CONFIG_INVALID" in codes(validate_team_spec(spec, tmp_path))


class TestTasks:
    def test_invalid_id(self, tmp_path):
        result = validate_team_spec(spec_minimal(id="../evil"), tmp_path)
        assert "E_TASK_ID_INVALID" in codes(result)

    def test_duplicate_id(self, tmp_path):
        spec = {"tasks": [spec_minimal()["tasks"][0],
                          spec_minimal()["tasks"][0]]}
        result = validate_team_spec(spec, tmp_path)
        assert "E_TASK_DUP_ID" in codes(result)

    def test_missing_title(self, tmp_path):
        result = validate_team_spec(spec_minimal(title="  "), tmp_path)
        assert "E_TASK_TITLE" in codes(result)

    def test_bad_assignee(self, tmp_path):
        result = validate_team_spec(spec_minimal(assignee="bad name!"), tmp_path)
        assert "E_TASK_ASSIGNEE" in codes(result)

    def test_status_in_spec_rejected(self, tmp_path):
        result = validate_team_spec(spec_minimal(status="completed"), tmp_path)
        assert "E_TASK_STATUS_IN_SPEC" in codes(result)

    def test_unknown_task_key(self, tmp_path):
        result = validate_team_spec(spec_minimal(file_write=["x"]), tmp_path)
        assert any("file_write" in i.message for i in result.errors)

    def test_wrong_field_type(self, tmp_path):
        result = validate_team_spec(spec_minimal(files_write="out/a.md"), tmp_path)
        assert "E_TASK_FIELD_TYPE" in codes(result)


class TestAgents:
    def test_unknown_assignee(self, tmp_path):
        spec = spec_minimal()
        spec["agents"] = [{"id": "other", "role": "executor"}]
        result = validate_team_spec(spec, tmp_path)
        assert "E_TASK_UNKNOWN_ASSIGNEE" in codes(result)

    def test_lead_cannot_be_assignee(self, tmp_path):
        spec = spec_minimal()
        spec["agents"] = [{"id": "w1", "role": "lead"}]
        result = validate_team_spec(spec, tmp_path)
        assert "E_TASK_ASSIGNEE_ROLE" in codes(result)

    def test_duplicate_agent(self, tmp_path):
        spec = spec_minimal()
        spec["agents"] = [{"id": "w1"}, {"id": "w1"}]
        result = validate_team_spec(spec, tmp_path)
        assert "E_AGENTS_INVALID" in codes(result)

    def test_unknown_role(self, tmp_path):
        spec = spec_minimal()
        spec["agents"] = [{"id": "w1", "role": "wizard"}]
        result = validate_team_spec(spec, tmp_path)
        assert "E_AGENTS_INVALID" in codes(result)


class TestAcceptance:
    def test_missing(self, tmp_path):
        task = spec_minimal()["tasks"][0]
        del task["acceptance"]
        result = validate_team_spec({"tasks": [task]}, tmp_path)
        assert "E_ACCEPTANCE_EMPTY" in codes(result)

    def test_empty_array(self, tmp_path):
        result = validate_team_spec(spec_minimal(acceptance=[]), tmp_path)
        assert "E_ACCEPTANCE_EMPTY" in codes(result)

    def test_unknown_type(self, tmp_path):
        result = validate_team_spec(spec_minimal(acceptance=[
            {"description": "x", "type": "vibe"}]), tmp_path)
        assert "E_ACCEPTANCE_INVALID" in codes(result)

    def test_command_type_requires_verification(self, tmp_path):
        for kind in ("test", "file_check", "security_check"):
            result = validate_team_spec(spec_minimal(acceptance=[
                {"description": "x", "type": kind}]), tmp_path)
            assert "E_ACCEPTANCE_INVALID" in codes(result), kind

    def test_duplicate_id(self, tmp_path):
        result = validate_team_spec(spec_minimal(acceptance=[
            {"id": "AC-1", "description": "a"},
            {"id": "AC-1", "description": "b"}]), tmp_path)
        assert "E_ACCEPTANCE_INVALID" in codes(result)


class TestNormalizeAcceptance:
    def test_string_becomes_manual(self):
        out = normalize_acceptance(["check it"])
        assert out[0] == {"id": "AC-001", "description": "check it",
                          "type": "manual", "required": True, "verification": ""}

    def test_auto_ids_in_order(self):
        out = normalize_acceptance(["a", "b", {"id": "X", "description": "c"}, "d"])
        assert [c["id"] for c in out] == ["AC-001", "AC-002", "X", "AC-003"]

    def test_defaults(self):
        out = normalize_acceptance([{"description": "docs"}])
        assert out[0]["type"] == "manual" and out[0]["required"] is True

    def test_empty_rejected(self):
        from agent_teams.errors import UsageError
        with pytest.raises(UsageError):
            normalize_acceptance([])


class TestOverlaps:
    def _two_tasks(self, deps_a=(), deps_b=()):
        return {"tasks": [
            {"id": "a", "title": "a", "assignee": "w1", "files_write": ["src/gen.py"],
             "acceptance": ["done"], "deps": list(deps_a)},
            {"id": "b", "title": "b", "assignee": "w2", "files_write": ["src/gen.py"],
             "acceptance": ["done"], "deps": list(deps_b)},
        ]}

    def test_overlap_rejected(self, tmp_path):
        result = validate_team_spec(self._two_tasks(), tmp_path)
        assert "E_WRITE_OVERLAP" in codes(result)
        assert any("merge" in i.message for i in result.errors)

    def test_directory_prefix_overlaps(self, tmp_path):
        spec = self._two_tasks()
        spec["tasks"][1]["files_write"] = ["src"]
        assert "E_WRITE_OVERLAP" in codes(validate_team_spec(spec, tmp_path))

    def test_non_overlap_prefix(self, tmp_path):
        spec = self._two_tasks()
        spec["tasks"][1]["files_write"] = ["srcrepo/gen.py"]
        assert validate_team_spec(spec, tmp_path).ok

    def test_ordered_overlap_warns(self, tmp_path):
        spec = self._two_tasks(deps_b=["a"])
        result = validate_team_spec(spec, tmp_path)
        assert result.ok
        assert "W_WRITE_OVERLAP_ORDERED" in wcodes(result)

    def test_override_with_reason_warns(self, tmp_path):
        spec = self._two_tasks()
        spec["config"] = {"allow_write_overlaps": True,
                          "allow_write_overlaps_reason": "same report file, merged later"}
        result = validate_team_spec(spec, tmp_path)
        assert result.ok
        assert "W_WRITE_OVERLAP_ALLOWED" in wcodes(result)

    def test_override_without_reason_rejected(self, tmp_path):
        spec = self._two_tasks()
        spec["config"] = {"allow_write_overlaps": True}
        assert "E_CONFIG_INVALID" in codes(validate_team_spec(spec, tmp_path))


class TestCollectsAllIssues:
    def test_many_problems_at_once(self, tmp_path):
        spec = {
            "tsaks": [],
            "tasks": [
                {"id": "x!", "title": "", "assignee": "bad name",
                 "files_write": ["/abs/path"], "acceptance": [],
                 "deps": ["ghost"], "status": "pending"},
            ],
        }
        result = validate_team_spec(spec, tmp_path)
        found = set(codes(result))
        assert {"E_UNKNOWN_KEY", "E_TASK_ID_INVALID", "E_TASK_TITLE",
                "E_TASK_ASSIGNEE", "E_TASK_STATUS_IN_SPEC", "E_ACCEPTANCE_EMPTY",
                "E_DEP_UNKNOWN", "E_PATH_ABSOLUTE"} <= found
