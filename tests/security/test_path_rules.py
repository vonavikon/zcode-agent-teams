"""Path rules: traversal, escapes, reserved dirs, symlinks, case."""
from __future__ import annotations

import os
import sys

import pytest

from agent_teams.errors import UsageError
from agent_teams.validation import (
    filesystem_is_case_insensitive,
    normalize_path,
    validate_team_spec,
)


class TestRejects:
    @pytest.mark.parametrize("raw", [
        "/etc/passwd",
        "C:/Windows/system32",
        "C:\\Windows",
        "//server/share",
        "~/.ssh/id_rsa",
    ])
    def test_absolute(self, tmp_path, raw):
        with pytest.raises(UsageError, match="absolute"):
            normalize_path(tmp_path, raw)

    @pytest.mark.parametrize("raw", [
        "../outside.md",
        "a/../../outside.md",
        "a/b/../../../outside.md",
        "..",
    ])
    def test_traversal(self, tmp_path, raw):
        with pytest.raises(UsageError, match=r"\.\."):
            normalize_path(tmp_path, raw)

    def test_control_characters(self, tmp_path):
        with pytest.raises(UsageError, match="control"):
            normalize_path(tmp_path, "a\x00b.md")

    def test_double_slash(self, tmp_path):
        # A trailing slash is allowed (directory), a middle empty component is not.
        with pytest.raises(UsageError, match="double slash"):
            normalize_path(tmp_path, "a//b")

    @pytest.mark.parametrize("raw", [".agent-teams/state.db", ".git/config"])
    def test_reserved(self, tmp_path, raw):
        with pytest.raises(UsageError, match="reserved"):
            normalize_path(tmp_path, raw)

    def test_empty_and_non_string(self, tmp_path):
        with pytest.raises(UsageError):
            normalize_path(tmp_path, "")
        with pytest.raises(UsageError):
            normalize_path(tmp_path, None)


class TestNormalizes:
    def test_backslashes(self, tmp_path):
        assert normalize_path(tmp_path, "src\\a.py", check_fs=False) == "src/a.py"

    def test_dot_dropped(self, tmp_path):
        assert normalize_path(tmp_path, "./src/./a.py", check_fs=False) == "src/a.py"

    def test_trailing_slash_kept_as_dir(self, tmp_path):
        assert normalize_path(tmp_path, "reports/", check_fs=False) == "reports"


class TestSymlinks:
    def test_symlink_escaping_project(self, tmp_path):
        if sys.platform == "win32" and not hasattr(os, "symlink"):
            pytest.skip("os.symlink unavailable on this Windows")
        # The link target must live OUTSIDE the project root.
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        outside.mkdir(exist_ok=True)
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(UsageError, match="symlink"):
            normalize_path(tmp_path, "link/escape.md", check_fs=True)

    def test_symlinked_project_root_itself(self, tmp_path):
        if sys.platform == "win32" and not hasattr(os, "symlink"):
            pytest.skip("os.symlink unavailable on this Windows")
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "proj").symlink_to(real, target_is_directory=True)
        assert normalize_path(tmp_path / "proj", "a.py", check_fs=True) == "a.py"

    def test_symlink_inside_project_ok(self, tmp_path):
        if sys.platform == "win32" and not hasattr(os, "symlink"):
            pytest.skip("os.symlink unavailable on this Windows")
        (tmp_path / "src").mkdir()
        (tmp_path / "alias").symlink_to(tmp_path / "src", target_is_directory=True)
        assert normalize_path(tmp_path, "alias/a.py") == "alias/a.py"


class TestCaseSensitivity:
    def test_probe_detected(self, tmp_path):
        # The probe result must match what the file system actually does.
        (tmp_path / "probe.tmp").write_text("x", encoding="utf-8")
        insensitive = (tmp_path / "PROBE.TMP").exists()
        assert filesystem_is_case_insensitive(tmp_path) is insensitive

    def test_case_collision_warning_only_on_insensitive_fs(self, tmp_path):
        spec = {
            "tasks": [{
                "id": "t1", "title": "demo", "assignee": "w1",
                "files_read": ["Docs/a.md", "docs/a.md"],
                "files_write": ["out/a.md"],
                "acceptance": ["done"], "deps": [],
            }],
        }
        result = validate_team_spec(spec, tmp_path)
        warnings = [i.code for i in result.warnings]
        if filesystem_is_case_insensitive(tmp_path):
            assert "W_PATH_CASE_COLLISION" in warnings
        else:
            assert "W_PATH_CASE_COLLISION" not in warnings
            assert "W_PATH_DUPLICATE" not in warnings


class TestDuplicates:
    def test_duplicate_in_one_list_warns(self, tmp_path):
        spec = {
            "tasks": [{
                "id": "t1", "title": "demo", "assignee": "w1",
                "files_read": ["a.py", "a.py"], "files_write": ["out/a.md"],
                "acceptance": ["done"], "deps": [],
            }],
        }
        result = validate_team_spec(spec, tmp_path)
        assert "W_PATH_DUPLICATE" in [i.code for i in result.warnings]

    def test_read_write_same_file_warns(self, tmp_path):
        spec = {
            "tasks": [{
                "id": "t1", "title": "demo", "assignee": "w1",
                "files_read": ["shared.md"], "files_write": ["shared.md"],
                "acceptance": ["done"], "deps": [],
            }],
        }
        result = validate_team_spec(spec, tmp_path)
        assert "W_READ_WRITE_SAME_FILE" in [i.code for i in result.warnings]
