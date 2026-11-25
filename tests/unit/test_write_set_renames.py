"""Write-set control of renames and copies (v2.1 spec 3.1).

The checks under test read git output, so every case is a real temporary
git repository rather than an imitation of what git might have printed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_teams import engine, workspaces
from agent_teams.errors import ExternalError

ALLOWED = ("out",)  # the declared write set: one directory

BASE_FILES = (
    "README.md", "out/keep.txt", "out/plain.txt",
    "src/secret.txt", "src/engine.txt", "docs/guide.md",
)

# Names that a line-based or space-split reading of git output would break.
AWKWARD_NAMES = (
    "with space.txt",
    'double "quote".txt',
    "single 'quote'.txt",
    "\u043f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440.txt",
    "\u65e5\u672c\u8a9e.txt",
)
AWKWARD_IDS = ("space", "double-quote", "single-quote", "cyrillic", "cjk")


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def content(number: int) -> str:
    """Text that no other file of the repository shares: git pairs a rename
    by content, and identical files would let it pair the wrong ones."""
    return "".join(f"line {number}.{i}\n" for i in range(20))


def write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


def move(root: Path, source: str, destination: str) -> None:
    """``git mv``, the directory of the destination created on demand."""
    (root / destination).parent.mkdir(parents=True, exist_ok=True)
    git(root, "mv", "--", source, destination)


def copy(root: Path, source: str, destination: str) -> None:
    """A copy outside git: the source file is not touched by it."""
    write(root, destination, (root / source).read_text(encoding="utf-8"))


def git_repo(root: Path, files: dict[str, str]) -> str:
    """A repository at ``root`` with ``files`` as its base commit; returns
    the commit."""
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    for name, text in files.items():
        write(root, name, text)
    commit(root, "base")
    return git(root, "rev-parse", "HEAD").strip()


class Repo:
    """A temporary git repository and the base commit diffs start from."""

    def __init__(self, root: Path, base: str):
        self.root = root
        self.base = base


def diff_of(repo: Repo) -> workspaces.WorkspaceDiff:
    return workspaces.capture_diff(repo.root, repo.base)


def violations(repo: Repo, files_write: tuple[str, ...] = ALLOWED) -> list[str]:
    return workspaces.write_set_violations(diff_of(repo), list(files_write))


def stream(*fields: str) -> str:
    """A ``git -z`` output: every field closed by a NUL."""
    return "".join(field + "\0" for field in fields)


@pytest.fixture
def repo(tmp_path):
    """``out/`` is the allowed directory; ``src/``, ``docs/`` and the root
    of the repository are not. The awkward names exist in both zones, so
    each of them can be moved either way."""
    names = list(BASE_FILES)
    names += [f"{zone}/{name}" for name in AWKWARD_NAMES for zone in ("out", "src")]
    files = {name: content(number) for number, name in enumerate(names)}
    return Repo(tmp_path, git_repo(tmp_path, files))


class TestMoveInsideAllowedDirectory:
    def test_move_inside_allowed_directory_is_clean(self, repo):
        move(repo.root, "out/plain.txt", "out/archive/plain.txt")
        commit(repo.root, "move")
        diff = diff_of(repo)
        assert diff.renames == (("out/plain.txt", "out/archive/plain.txt"),)
        assert diff.changed == ("out/archive/plain.txt", "out/plain.txt")
        assert violations(repo) == []
        # the destination alone, as the flat field of the same name gave it
        assert diff.renamed == ("out/archive/plain.txt",)

    def test_move_with_edits_inside_allowed_directory_is_clean(self, repo):
        move(repo.root, "out/plain.txt", "out/renamed.txt")
        write(repo.root, "out/renamed.txt", content(2) + "one more line\n")
        commit(repo.root, "move and edit")
        assert diff_of(repo).renames == (("out/plain.txt", "out/renamed.txt"),)
        assert violations(repo) == []

    def test_each_end_is_judged_on_its_own(self, repo):
        move(repo.root, "out/plain.txt", "out/moved.txt")
        commit(repo.root, "move")
        assert violations(repo, ("out/plain.txt",)) == ["out/moved.txt"]
        assert violations(repo, ("out/plain.txt", "out/moved.txt")) == []


class TestMoveAcrossTheWriteSet:
    def test_move_from_forbidden_into_allowed_is_a_violation(self, repo):
        move(repo.root, "src/secret.txt", "out/secret.txt")
        commit(repo.root, "move")
        assert diff_of(repo).renames == (("src/secret.txt", "out/secret.txt"),)
        assert violations(repo) == ["src/secret.txt"]

    def test_move_of_a_forbidden_file_elsewhere_is_a_violation(self, repo):
        move(repo.root, "src/secret.txt", "docs/old secret.txt")
        commit(repo.root, "move")
        assert violations(repo) == ["docs/old secret.txt", "src/secret.txt"]

    def test_move_from_allowed_into_forbidden_is_a_violation(self, repo):
        move(repo.root, "out/plain.txt", "src/plain.txt")
        commit(repo.root, "move")
        assert violations(repo) == ["src/plain.txt"]

    def test_move_with_a_full_rewrite_is_still_a_violation(self, repo):
        # under the similarity threshold git reports a deletion and an
        # addition instead of a rename: both paths stay in the diff
        move(repo.root, "src/secret.txt", "out/secret.txt")
        write(repo.root, "out/secret.txt", "%\n" * 40)
        commit(repo.root, "move and rewrite")
        diff = diff_of(repo)
        assert diff.renames == () and diff.copies == ()
        assert violations(repo) == ["src/secret.txt"]

    def test_staged_move_is_a_violation_before_any_commit(self, repo):
        move(repo.root, "src/secret.txt", "out/secret.txt")
        diff = diff_of(repo)
        assert diff.has_uncommitted is True
        assert diff.renames == (("src/secret.txt", "out/secret.txt"),)
        assert violations(repo) == ["src/secret.txt"]

    def test_unstaged_move_is_a_violation(self, repo):
        # nothing is staged, so there is no rename to see: a file missing
        # from where it was and an untracked one where it went
        copy(repo.root, "src/secret.txt", "out/secret.txt")
        (repo.root / "src/secret.txt").unlink()
        diff = diff_of(repo)
        assert diff.renames == ()
        assert violations(repo) == ["src/secret.txt"]


class TestCopies:
    def test_copy_of_a_forbidden_file_is_a_violation(self, repo):
        secret = repo.root / "src/secret.txt"
        original = secret.read_text(encoding="utf-8")
        copy(repo.root, "src/secret.txt", "out/secret copy.txt")
        commit(repo.root, "copy")
        assert secret.read_text(encoding="utf-8") == original  # untouched
        assert diff_of(repo).copies == (("src/secret.txt", "out/secret copy.txt"),)
        assert violations(repo) == ["src/secret.txt"]

    def test_copy_edited_on_the_way_is_still_a_violation(self, repo):
        original = (repo.root / "src/secret.txt").read_text(encoding="utf-8")
        copy(repo.root, "src/secret.txt", "out/secret.txt")
        write(repo.root, "out/secret.txt", original + "one more line\n")
        commit(repo.root, "copy and edit")
        diff = diff_of(repo)
        assert [source for source, _destination in diff.copies] == ["src/secret.txt"]
        assert violations(repo) == ["src/secret.txt"]

    def test_staged_copy_is_a_violation_before_any_commit(self, repo):
        copy(repo.root, "src/secret.txt", "out/secret.txt")
        git(repo.root, "add", "-A")
        assert violations(repo) == ["src/secret.txt"]

    def test_copy_inside_allowed_directory_is_clean(self, repo):
        copy(repo.root, "out/plain.txt", "out/plain again.txt")
        commit(repo.root, "copy")
        assert violations(repo) == []


class TestAwkwardNames:
    @pytest.mark.parametrize("name", AWKWARD_NAMES, ids=AWKWARD_IDS)
    def test_move_inside_allowed_directory_is_clean(self, repo, name):
        move(repo.root, f"out/{name}", f"out/moved {name}")
        commit(repo.root, "move")
        assert diff_of(repo).renames == ((f"out/{name}", f"out/moved {name}"),)
        assert violations(repo) == []

    @pytest.mark.parametrize("name", AWKWARD_NAMES, ids=AWKWARD_IDS)
    def test_move_from_forbidden_zone_reports_the_whole_name(self, repo, name):
        move(repo.root, f"src/{name}", f"out/arrived {name}")
        commit(repo.root, "move")
        assert diff_of(repo).renames == ((f"src/{name}", f"out/arrived {name}"),)
        assert violations(repo) == [f"src/{name}"]

    @pytest.mark.skipif(os.name == "nt",
                        reason="Windows does not allow these names")
    def test_control_characters_in_a_name_come_through_unchanged(self, repo):
        # read in text mode, the carriage return would turn into a newline
        names = ["out/tab\there.txt", "out/carriage\rreturn.txt",
                 "out/new\nline.txt"]
        for number, name in enumerate(names):
            write(repo.root, name, content(100 + number))
        commit(repo.root, "odd names")
        assert diff_of(repo).changed == tuple(sorted(names))
        assert violations(repo, tuple(names)) == []  # each one named exactly

    def test_names_read_the_same_under_a_c_locale(self, repo):
        """The decoding must not depend on the locale: under the C one the
        UTF-8 names of git output are not text in Python's default encoding."""
        name = "\u043f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440.txt"
        move(repo.root, f"out/{name}", f"out/moved {name}")
        commit(repo.root, "move")
        scripts = Path(workspaces.__file__).resolve().parents[1]
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0",
               "PYTHONCOERCECLOCALE": "0", "PYTHONPATH": str(scripts)}
        code = ("import json, sys; from pathlib import Path; "
                "from agent_teams import workspaces; "
                "diff = workspaces.capture_diff(Path(sys.argv[1]), sys.argv[2]); "
                "print(json.dumps(diff.changed))")
        proc = subprocess.run([sys.executable, "-c", code,
                               str(repo.root), repo.base],
                              capture_output=True, text=True, env=env,
                              timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == [f"out/moved {name}", f"out/{name}"]


class TestParsingContract:
    """The field order of the two -z formats, as git 2.54 prints them (its
    diff.c writes the source of a rename first): the two differ."""

    def test_diff_puts_the_source_of_a_rename_first(self):
        raw = stream("M", "out/a b.txt", "R100", "src/old.txt", "out/new.txt",
                     "C075", "src/x.txt", "out/y.txt", "A", "out/z.txt")
        assert workspaces.parse_name_status_z(raw) == [
            ("M", "out/a b.txt", None),
            ("R100", "src/old.txt", "out/new.txt"),
            ("C075", "src/x.txt", "out/y.txt"),
            ("A", "out/z.txt", None)]

    def test_status_puts_the_destination_first_the_source_after(self):
        raw = stream("R  out/new.txt", "src/old.txt", "?? out/untracked.txt",
                     "RM out/edited.txt", "src/edited.txt",
                     "C  out/copy.txt", "src/copy.txt", " D out/gone.txt")
        assert workspaces.parse_status_z(raw) == [
            ("R ", "out/new.txt", "src/old.txt"),
            ("??", "out/untracked.txt", None),
            ("RM", "out/edited.txt", "src/edited.txt"),
            ("C ", "out/copy.txt", "src/copy.txt"),
            (" D", "out/gone.txt", None)]

    def test_no_output_means_no_records(self):
        assert workspaces.parse_name_status_z("") == []
        assert workspaces.parse_status_z("") == []

    def test_output_that_cannot_be_read_is_an_error(self):
        # never an empty diff: that would pass the write-set control
        with pytest.raises(ExternalError):  # destination missing
            workspaces.parse_name_status_z(stream("R100", "src/old.txt"))
        with pytest.raises(ExternalError):  # a path read as a status
            workspaces.parse_name_status_z(stream("M", "a.txt", "out", "b.txt"))
        with pytest.raises(ExternalError):  # not terminated
            workspaces.parse_name_status_z("M\0out/cut.txt")
        with pytest.raises(ExternalError):  # source missing
            workspaces.parse_status_z(stream("R  out/new.txt"))
        with pytest.raises(ExternalError):  # a path read as a record
            workspaces.parse_status_z(stream("src/old.txt"))


class TestSubmitGate:
    """The defect as the engine met it: a submit that used to pass clean."""

    @pytest.mark.parametrize("operation", ["move", "copy"])
    def test_submit_reports_the_forbidden_source(self, tmp_path, operation):
        files = {".gitignore": ".agent-teams/\n"}
        files.update({name: content(number)
                      for number, name in enumerate(BASE_FILES)})
        git_repo(tmp_path, files)
        spec = {"tasks": [{"id": "a", "title": "rename or copy", "assignee": "w1",
                           "files_read": [], "files_write": list(ALLOWED),
                           "acceptance": ["done"], "deps": []}]}
        (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        engine.op_init(tmp_path, "t", tmp_path / "spec.json")
        claim = engine.op_claim(tmp_path, "t", "a", "w1")
        ws = Path(claim["workspace"])
        if operation == "move":
            move(ws, "src/secret.txt", "out/secret.txt")
        else:
            copy(ws, "src/secret.txt", "out/secret.txt")
        commit(ws, operation)
        result = engine.op_submit(tmp_path, "t", "a", "w1",
                                  claim["execution_token"])
        assert result["write_set_violations"] == ["src/secret.txt"]
