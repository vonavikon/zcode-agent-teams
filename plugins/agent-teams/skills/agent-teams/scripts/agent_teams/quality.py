"""Machine-checkable acceptance criteria and the integration gate.

Spec sections 7.1-7.3: criteria of type test / file_check /
security_check carry a shell command; the engine runs them itself and a
required criterion that fails blocks PASS regardless of what the lead or
the verifier subagent says. Write-set violations recorded at submit force
a FAIL the same way. The integration gate merges accepted task branches
into one integration branch, runs an optional check command and refuses
to declare the team complete on conflicts or regressions.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ExternalError

CHECK_TIMEOUT_S = 120
COMMAND_TYPES = ("test", "file_check", "security_check")


@dataclass(frozen=True)
class CriterionResult:
    id: str
    type: str
    command: str
    passed: bool
    exit_code: int | None
    output: str
    required: bool = True

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "command": self.command,
                "passed": self.passed, "exit_code": self.exit_code,
                "required": self.required, "output": self.output[:2000]}


def working_directory(task: dict, project_root: Path) -> Path:
    ws = task.get("workspace_path")
    if ws and Path(ws).exists():
        return Path(ws)
    return Path(project_root).resolve()


def run_checks(task: dict, project_root: Path) -> list[CriterionResult]:
    """Run every command-backed acceptance criterion of the task."""
    results = []
    for criterion in task.get("acceptance", []):
        if not isinstance(criterion, dict):
            continue  # legacy string criteria are for the human/LLM gate
        if criterion.get("type") not in COMMAND_TYPES:
            continue
        command = criterion.get("verification", "")
        if not command:
            results.append(CriterionResult(
                criterion.get("id", "?"), criterion.get("type", ""), "",
                passed=False, exit_code=None,
                output="criterion has no verification command",
                required=criterion.get("required", True)))
            continue
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(working_directory(task,
                                                                project_root)),
                capture_output=True, text=True, timeout=CHECK_TIMEOUT_S)
            results.append(CriterionResult(
                criterion.get("id", "?"), criterion.get("type", ""), command,
                passed=proc.returncode == 0, exit_code=proc.returncode,
                output=(proc.stdout + proc.stderr).strip(),
                required=criterion.get("required", True)))
        except subprocess.TimeoutExpired:
            results.append(CriterionResult(
                criterion.get("id", "?"), criterion.get("type", ""), command,
                passed=False, exit_code=None,
                output=f"timed out after {CHECK_TIMEOUT_S}s",
                required=criterion.get("required", True)))
        except OSError as exc:
            results.append(CriterionResult(
                criterion.get("id", "?"), criterion.get("type", ""), command,
                passed=False, exit_code=None, output=str(exc),
                required=criterion.get("required", True)))
    return results


def forced_failures(task: dict, project_root: Path) -> tuple[list[str],
                                                              list[dict]]:
    """Everything that blocks a PASS: write-set violations and failed
    required machine criteria. Returns (reasons, criterion results)."""
    reasons: list[str] = []
    for path in task.get("metadata", {}).get("workspace_violations", []):
        reasons.append(f"write outside files_write: {path}")
    results = run_checks(task, project_root)
    for result in results:
        if result.required and not result.passed:
            reasons.append(
                f"required criterion {result.id} failed "
                f"(exit {result.exit_code}): {result.output[:200]}")
    return reasons, [r.to_dict() for r in results]


def _git(args: list[str], cwd: Path, *, check: bool = True
         ) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=180)
    if check and proc.returncode != 0:
        raise ExternalError(f"git {' '.join(args[:2])} failed: "
                            f"{proc.stderr.strip()[:300]}")
    return proc


def integration_base_of(project_root: Path, team: str) -> str:
    """The project HEAD an integration branch was built from."""
    return _git(["rev-parse", "HEAD"], project_root).stdout.strip()


def integration_branch_name(team: str) -> str:
    """The branch the results of a team are merged on."""
    return f"agent-teams/{team}/integration"


def merge_shas(project_root: Path, team_root: Path, team: str,
               shas: list[str], *, check_command: str = ""
               ) -> dict:
    """Integration gate over immutable accepted SHAs (hardening spec 5).

    Same contract as the former branch merge: a fresh integration
    branch and worktree, sequential merges, an optional check command;
    conflicts and failed checks change nothing that was accepted.
    """
    integration_branch = integration_branch_name(team)
    workspace = team_root / "integration" / "workspace"
    # The worktree holds a checkout ref: remove it before deleting the
    # branch, or the deletion silently fails and the recreate explodes.
    _git(["worktree", "remove", "--force", str(workspace)], project_root,
         check=False)
    _git(["branch", "-D", integration_branch], project_root, check=False)
    base = _git(["rev-parse", "HEAD"], project_root).stdout.strip()
    _git(["branch", integration_branch, base], project_root)
    _git(["worktree", "add", str(workspace), integration_branch], project_root)

    integrated: list[str] = []
    try:
        for sha in shas:
            proc = _git(["merge", "--no-edit", sha], workspace, check=False)
            if proc.returncode != 0:
                conflicts = _git(["diff", "--name-only", "--diff-filter=U"],
                                 workspace).stdout.split()
                _git(["merge", "--abort"], workspace, check=False)
                return {"ok": False, "integrated": integrated,
                        "conflicts": {"sha": sha,
                                      "files": [f.replace("\\", "/")
                                                for f in conflicts]},
                        "check": None, "workspace": str(workspace)}
            integrated.append(sha)
        check_result = None
        if check_command:
            try:
                proc = subprocess.run(check_command, shell=True,
                                      cwd=str(workspace), capture_output=True,
                                      text=True, timeout=CHECK_TIMEOUT_S)
                check_result = {"exit_code": proc.returncode,
                                "output": (proc.stdout + proc.stderr)[:2000]}
                if proc.returncode != 0:
                    return {"ok": False, "integrated": integrated,
                            "conflicts": None, "check": check_result,
                            "workspace": str(workspace)}
            except (subprocess.TimeoutExpired, OSError) as exc:
                check_result = {"exit_code": None, "output": str(exc)[:500]}
                return {"ok": False, "integrated": integrated, "conflicts": None,
                        "check": check_result, "workspace": str(workspace)}
        return {"ok": True, "integrated": integrated, "conflicts": None,
                "check": check_result, "workspace": str(workspace)}
    finally:
        pass


def merge_branches(project_root: Path, team_root: Path, team: str,
                   branches: list[str], *, check_command: str = ""
                   ) -> dict:
    """Integration gate: merge accepted branches into a fresh integration
    worktree, optionally run a check command there.

    Returns {"ok": bool, "integrated": [...], "conflicts": [...],
    "check": {...}, "workspace": path}. On any failure the integration
    branch is removed; accepted task branches are never touched.
    """
    integration_branch = integration_branch_name(team)
    workspace = team_root / "integration" / "workspace"
    _git(["branch", "-D", integration_branch], project_root, check=False)
    base = _git(["rev-parse", "HEAD"], project_root).stdout.strip()
    _git(["branch", integration_branch, base], project_root)
    _git(["worktree", "remove", "--force", str(workspace)], project_root,
         check=False)
    _git(["worktree", "add", str(workspace), integration_branch], project_root)

    integrated: list[str] = []
    try:
        for branch in branches:
            proc = _git(["merge", "--no-edit", branch], workspace, check=False)
            if proc.returncode != 0:
                conflicts = _git(["diff", "--name-only", "--diff-filter=U"],
                                 workspace).stdout.split()
                _git(["merge", "--abort"], workspace, check=False)
                return {"ok": False, "integrated": integrated,
                        "conflicts": {"branch": branch,
                                      "files": [f.replace("\\", "/")
                                                for f in conflicts]},
                        "check": None, "workspace": str(workspace)}
            integrated.append(branch)
        check_result = None
        if check_command:
            try:
                proc = subprocess.run(check_command, shell=True,
                                      cwd=str(workspace), capture_output=True,
                                      text=True, timeout=CHECK_TIMEOUT_S)
                check_result = {"exit_code": proc.returncode,
                                "output": (proc.stdout + proc.stderr)[:2000]}
                if proc.returncode != 0:
                    return {"ok": False, "integrated": integrated,
                            "conflicts": None, "check": check_result,
                            "workspace": str(workspace)}
            except (subprocess.TimeoutExpired, OSError) as exc:
                check_result = {"exit_code": None, "output": str(exc)[:500]}
                return {"ok": False, "integrated": integrated, "conflicts": None,
                        "check": check_result, "workspace": str(workspace)}
        return {"ok": True, "integrated": integrated, "conflicts": None,
                "check": check_result, "workspace": str(workspace)}
    finally:
        pass


def cleanup_integration(project_root: Path, team_root: Path, team: str,
                        *, keep_branch: bool) -> None:
    workspace = team_root / "integration" / "workspace"
    _git(["worktree", "remove", "--force", str(workspace)], project_root,
         check=False)
    if not keep_branch:
        _git(["branch", "-D", f"agent-teams/{team}/integration"],
             project_root, check=False)


def apply_integration(project_root: Path, team: str) -> dict:
    """Merge the integration branch into the project's current branch."""
    proc = _git(["merge", "--no-edit", f"agent-teams/{team}/integration"],
                project_root, check=False)
    if proc.returncode != 0:
        _git(["merge", "--abort"], project_root, check=False)
        raise ExternalError(
            "applying the integration branch failed, the merge was aborted: "
            f"{proc.stderr.strip()[:300]}")
    return {"applied": True}
