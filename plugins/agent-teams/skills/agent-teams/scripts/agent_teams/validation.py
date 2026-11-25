"""Strict validation of a team specification before any executor starts.

Checks (spec sections 4.1-4.3): structure and types, unique ids, acceptance
criteria, the dependency DAG, project-relative path rules, and write
overlaps between tasks. All issues are collected in one result so the lead
sees every problem at once; nothing is executed until ``ok`` is true.

Public API::

    validate_team_spec(spec, project_root, check_fs=True) -> ValidationResult
    normalize_acceptance(raw) -> list[dict]
    normalize_path(project_root, raw, check_fs=True) -> str
    find_write_overlaps(tasks, project_root=None) -> list[Overlap]
    filesystem_is_case_insensitive(root) -> bool
    topological_order(tasks) -> list[str] | None
    find_cycle(tasks) -> list[str] | None
    is_ancestor(tasks, ancestor, descendant) -> bool
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import BUDGET_KEYS, merge_config
from .errors import UsageError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ACCEPTANCE_TYPES = frozenset(
    {"test", "code_review", "file_check", "security_check", "documentation", "manual"})
COMMAND_ACCEPTANCE_TYPES = frozenset({"test", "file_check", "security_check"})
ROLES = frozenset({"lead", "executor", "verifier", "integrator"})

CONFIG_KEYS = frozenset({
    "workspace", "allow_write_overlaps", "allow_write_overlaps_reason",
    "max_verification_failures", "max_execution_retries", "max_agent_restarts",
    "execution_timeout_s", "heartbeat_timeout_s", "lock_timeout_s",
}) | BUDGET_KEYS
TASK_KEYS = frozenset({
    "id", "title", "assignee", "lens", "files_read", "files_write",
    "acceptance", "deps", "description",
})

# Words for people (v2.1 spec 4.1, 8.1): the name and the goal of a team,
# and what a task is about. Optional; the board shows them as they are.
TEXT_LIMITS = {"title": 120, "goal": 1000, "description": 500}

_RESERVED_DIRS = (".agent-teams", ".git")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    path: str = ""
    task_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Overlap:
    task_a: str
    task_b: str
    path_a: str
    path_b: str
    ordered: bool


# ---------------------------------------------------------------- paths


def normalize_path(project_root: Path, raw: object, *, check_fs: bool = True) -> str:
    """Validate one project-relative path and return it in POSIX form.

    Raises UsageError on any violation. Backslashes become "/"; "." is
    dropped; a trailing slash means "this directory".
    """
    if not isinstance(raw, str) or not raw:
        raise UsageError(f"path must be a non-empty string, got {raw!r}")
    if any(ord(ch) < 32 for ch in raw):
        raise UsageError(f"path contains control characters: {raw!r}")
    candidate = raw.replace("\\", "/")
    if candidate.startswith("/") or candidate.startswith("~") \
            or _WINDOWS_DRIVE.match(candidate) or candidate.startswith("//"):
        raise UsageError(f"path must be project-relative, got an absolute path: {raw!r}")
    if any(part == "" for part in candidate.split("/")[1:-1]):
        raise UsageError(f"path has an empty component (double slash): {raw!r}")
    parts = [p for p in candidate.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UsageError(f"path must not contain '..': {raw!r}")
    if not parts:
        raise UsageError(f"path resolves to the project root: {raw!r}")
    if parts[0] in _RESERVED_DIRS:
        raise UsageError(
            f"path is reserved for the engine: {raw!r} "
            f"(first component {parts[0]!r})")
    normalized = "/".join(parts)
    if check_fs:
        root = Path(project_root).resolve()
        probe = root.joinpath(*parts)
        # Walk the longest existing ancestor and require it to stay inside
        # the project root after resolving symlinks.
        existing = probe
        while not existing.exists() and existing != root:
            existing = existing.parent
        try:
            if root not in existing.resolve().parents and existing.resolve() != root:
                raise UsageError(
                    f"path escapes the project root through a symlink: {raw!r} "
                    f"({existing} -> {existing.resolve()})")
        except OSError as exc:
            raise UsageError(f"path could not be resolved: {raw!r} ({exc})") from exc
    return normalized


def filesystem_is_case_insensitive(root: Path) -> bool:
    """Probe the file system by creating and looking up a mixed-case name."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    probe_name = f".CaseProbe{os.getpid()}"
    probe = root / probe_name
    try:
        probe.write_text("x", encoding="utf-8")
        return (root / probe_name.lower()).exists()
    except OSError:
        return os.name == "nt"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- acceptance


def normalize_acceptance(raw: object) -> list[dict]:
    """Turn acceptance items into structured criteria, in input order.

    A plain string becomes a manual criterion; objects keep their fields.
    Raises UsageError on an invalid item.
    """
    if not isinstance(raw, list) or not raw:
        raise UsageError("acceptance must be a non-empty array")
    criteria, seen_ids = [], set()
    for index, item in enumerate(raw):
        if isinstance(item, str):
            if not item.strip():
                raise UsageError(f"acceptance[{index}] is an empty string")
            criteria.append({"id": "", "description": item, "type": "manual",
                             "required": True, "verification": ""})
            continue
        if not isinstance(item, dict):
            raise UsageError(
                f"acceptance[{index}] must be a string or an object, got {type(item).__name__}")
        unknown = sorted(set(item) - {"id", "description", "type", "required", "verification"})
        if unknown:
            raise UsageError(f"acceptance[{index}] has unknown keys: {', '.join(unknown)}")
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise UsageError(f"acceptance[{index}] is missing a description")
        kind = item.get("type", "manual")
        if kind not in ACCEPTANCE_TYPES:
            raise UsageError(
                f"acceptance[{index}] has an unknown type {kind!r} "
                f"(known: {', '.join(sorted(ACCEPTANCE_TYPES))})")
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise UsageError(f"acceptance[{index}].required must be true or false")
        verification = item.get("verification", "")
        if kind in COMMAND_ACCEPTANCE_TYPES and (
                not isinstance(verification, str) or not verification.strip()):
            raise UsageError(
                f"acceptance[{index}] of type {kind} requires a non-empty "
                "'verification' shell command")
        if not isinstance(verification, str):
            raise UsageError(f"acceptance[{index}].verification must be a string")
        criterion_id = item.get("id", "")
        if criterion_id is not None and not isinstance(criterion_id, str):
            raise UsageError(f"acceptance[{index}].id must be a string")
        if criterion_id:
            if criterion_id in seen_ids:
                raise UsageError(f"duplicate acceptance id inside the task: {criterion_id}")
            seen_ids.add(criterion_id)
        criteria.append({"id": criterion_id, "description": description, "type": kind,
                         "required": required, "verification": verification})
    auto = 0
    for criterion in criteria:
        if not criterion["id"]:
            auto += 1
            criterion["id"] = f"AC-{auto:03d}"
    return criteria


# ---------------------------------------------------------------- DAG


def _deps_graph(tasks: list[dict]) -> dict[str, list[str]]:
    return {t["id"]: list(t.get("deps", [])) for t in tasks}


def find_cycle(tasks: list[dict]) -> list[str] | None:
    graph = _deps_graph(tasks)
    state: dict[str, int] = {node: 0 for node in graph}  # 0 new, 1 in stack, 2 done
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if state.get(dep) == 1:
                start = stack.index(dep)
                return stack[start:] + [dep]
            if state.get(dep) == 0 and dep in graph:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if state[node] == 0:
            found = visit(node)
            if found:
                return found
    return None


def topological_order(tasks: list[dict]) -> list[str] | None:
    """Kahn's algorithm; None when a cycle exists. Unknown deps are ignored
    here (E_DEP_UNKNOWN covers them)."""
    graph = _deps_graph(tasks)
    known = set(graph)
    indegree = {node: 0 for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            if dep in known:
                indegree[node] += 1
    queue = sorted(node for node, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for other, deps in graph.items():
            if node in deps:
                indegree[other] -= 1
                if indegree[other] == 0:
                    queue.append(other)
        queue.sort()
    return order if len(order) == len(graph) else None


def is_ancestor(tasks: list[dict], ancestor: str, descendant: str) -> bool:
    graph = _deps_graph(tasks)
    seen = set()
    frontier = [descendant]
    while frontier:
        node = frontier.pop()
        for dep in graph.get(node, []):
            if dep == ancestor:
                return True
            if dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    return False


# ---------------------------------------------------------------- overlaps


def _paths_overlap(a: str, b: str) -> bool:
    pa, pb = PurePosixPath(a), PurePosixPath(b)
    da, db = list(pa.parts), list(pb.parts)
    shared = min(len(da), len(db))
    return da[:shared] == db[:shared]


def find_write_overlaps(tasks: list[dict],
                        project_root: Path | None = None) -> list[Overlap]:
    """Write-set overlaps between task pairs, including directory prefixes.

    ``ordered`` marks pairs where one task transitively depends on the
    other (the overlap is then sequenced, not parallel). Paths compare
    case-folded only on case-insensitive file systems.
    """
    fold: object = str
    if project_root is not None and filesystem_is_case_insensitive(project_root):
        fold = str.casefold
    by_task: dict[str, list[str]] = {}
    for task in tasks:
        raw_paths = task.get("files_write", [])
        if not isinstance(raw_paths, list):
            continue
        by_task[task["id"]] = [fold(p) if isinstance(p, str) else p for p in raw_paths]
    ids = sorted(by_task)
    overlaps: list[Overlap] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            for pa in by_task[a]:
                for pb in by_task[b]:
                    if _paths_overlap(pa, pb):
                        overlaps.append(Overlap(
                            task_a=a, task_b=b, path_a=pa, path_b=pb,
                            ordered=is_ancestor(tasks, a, b) or is_ancestor(tasks, b, a)))
    return overlaps


# ---------------------------------------------------------------- spec


def _check_agents(spec: dict, errors: list, warnings: list) -> dict[str, str]:
    roles: dict[str, str] = {}
    agents = spec.get("agents")
    if agents is None:
        return roles
    if not isinstance(agents, list):
        errors.append(Issue("E_AGENTS_INVALID", "agents must be an array"))
        return roles
    seen = set()
    for index, agent in enumerate(agents):
        where = f"agents[{index}]"
        if not isinstance(agent, dict):
            errors.append(Issue("E_AGENTS_INVALID", f"{where} must be an object"))
            continue
        unknown = sorted(set(agent) - {"id", "role"})
        if unknown:
            errors.append(Issue("E_AGENTS_INVALID",
                                f"{where}: unknown keys {', '.join(unknown)}"))
        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not NAME_RE.match(agent_id or ""):
            errors.append(Issue("E_AGENTS_INVALID", f"{where}.id is invalid: {agent_id!r}"))
            continue
        if agent_id in seen:
            errors.append(Issue("E_AGENTS_INVALID", f"duplicate agent id: {agent_id}"))
            continue
        seen.add(agent_id)
        role = agent.get("role", "executor")
        if role not in ROLES:
            errors.append(Issue("E_AGENTS_INVALID",
                                f"{where}.role {role!r} is not one of {', '.join(sorted(ROLES))}"))
            continue
        roles[agent_id] = role
    return roles


def _check_config(spec: dict, errors: list, warnings: list) -> dict:
    config = spec.get("config")
    if config is None:
        return {}
    if not isinstance(config, dict):
        errors.append(Issue("E_CONFIG_INVALID", "config must be a JSON object"))
        return {}
    unknown = sorted(set(config) - CONFIG_KEYS)
    if unknown:
        errors.append(Issue("E_CONFIG_INVALID",
                            f"config has unknown keys: {', '.join(unknown)}"))
    # The money limits and the declared benchmark are checked where they
    # are defined, so the rule cannot differ between a spec and the engine.
    try:
        merge_config({key: value for key, value in config.items() if key in BUDGET_KEYS})
    except UsageError as exc:
        errors.append(Issue("E_CONFIG_INVALID", str(exc)))
    limits = {
        "max_verification_failures": (int, 1),
        "max_execution_retries": (int, 0),
        "max_agent_restarts": (int, 0),
    }
    for key, (kind, minimum) in limits.items():
        if key in config:
            value = config[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                errors.append(Issue("E_CONFIG_INVALID",
                                    f"config.{key} must be an integer >= {minimum}, got {value!r}"))
    for key in ("execution_timeout_s", "heartbeat_timeout_s", "lock_timeout_s"):
        if key in config:
            value = config[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                errors.append(Issue("E_CONFIG_INVALID",
                                    f"config.{key} must be a number > 0, got {value!r}"))
    if "workspace" in config and config["workspace"] not in ("auto", "worktree", "shared"):
        errors.append(Issue("E_CONFIG_INVALID",
                            f"config.workspace must be auto, worktree or shared, "
                            f"got {config['workspace']!r}"))
    overlaps = config.get("allow_write_overlaps", False)
    reason = config.get("allow_write_overlaps_reason", "")
    if not isinstance(overlaps, bool):
        errors.append(Issue("E_CONFIG_INVALID", "config.allow_write_overlaps must be true or false"))
    elif overlaps and (not isinstance(reason, str) or not reason.strip()):
        errors.append(Issue(
            "E_CONFIG_INVALID",
            "config.allow_write_overlaps_reason is required when "
            "allow_write_overlaps is true: the lead must explain why two "
            "tasks may write the same files"))
    return config


def _check_text(value: object, where: str, limit: int, errors: list) -> None:
    """A text for people: present means non-blank and not longer than ``limit``."""
    if not isinstance(value, str) or not value.strip():
        errors.append(Issue("E_TEXT", f"{where} must be a non-empty text"))
    elif len(value) > limit:
        errors.append(Issue("E_TEXT", f"{where} is longer than {limit} characters"))


def validate_team_spec(spec: object, project_root: Path,
                       *, check_fs: bool = True) -> ValidationResult:
    errors: list[Issue] = []
    warnings: list[Issue] = []
    if not isinstance(spec, dict):
        return ValidationResult(
            errors=(Issue("E_SPEC_NOT_OBJECT",
                          f"the spec must be a JSON object, got {type(spec).__name__}"),))

    known_top = {"tasks", "config", "agents", "title", "goal"}
    unknown_top = sorted(set(spec) - known_top)
    if unknown_top:
        errors.append(Issue("E_UNKNOWN_KEY",
                            f"unknown top-level keys: {', '.join(unknown_top)}"))
    for key in ("title", "goal"):
        if key in spec:
            _check_text(spec[key], key, TEXT_LIMITS[key], errors)

    config = _check_config(spec, errors, warnings)
    roles = _check_agents(spec, errors, warnings)

    tasks = spec.get("tasks")
    if tasks is None:
        errors.append(Issue("E_TASKS_MISSING", "the spec has no 'tasks' array"))
        tasks = []
    elif not isinstance(tasks, list):
        errors.append(Issue("E_TASKS_NOT_ARRAY", "tasks must be an array"))
        tasks = []
    elif not tasks:
        errors.append(Issue("E_TASKS_EMPTY", "tasks is empty: a team needs at least one task"))

    ids: list[str] = []
    parsed: list[dict] = []
    case_fold = filesystem_is_case_insensitive(Path(project_root)) if check_fs \
        else (os.name == "nt")
    for index, task in enumerate(tasks):
        where = f"tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(Issue("E_TASK_ID_INVALID", f"{where} must be an object"))
            continue
        parsed.append(task)  # deps and paths are checked even with a bad id
        unknown = sorted(set(task) - TASK_KEYS)
        if unknown:
            errors.append(Issue("E_UNKNOWN_KEY",
                                f"{where}: unknown keys {', '.join(unknown)} "
                                f"(task ids: {task.get('id', '?')})"))
        task_id = task.get("id")
        if not isinstance(task_id, str) or not NAME_RE.match(task_id or ""):
            errors.append(Issue("E_TASK_ID_INVALID",
                                f"{where}.id is invalid: {task_id!r}"))
            task_id = None
        elif task_id in ids:
            errors.append(Issue("E_TASK_DUP_ID", f"duplicate task id: {task_id}"))
        if task_id is not None:
            ids.append(task_id)

        title = task.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(Issue("E_TASK_TITLE", f"{where}.title is missing or empty"))
        if "description" in task:
            _check_text(task["description"], f"{where}.description",
                        TEXT_LIMITS["description"], errors)
        if "status" in task:
            errors.append(Issue(
                "E_TASK_STATUS_IN_SPEC",
                f"{where}.status is {task['status']!r}: tasks always start as pending"))
        assignee = task.get("assignee")
        if not isinstance(assignee, str) or not NAME_RE.match(assignee or ""):
            errors.append(Issue("E_TASK_ASSIGNEE",
                                f"{where}.assignee is invalid: {assignee!r}"))
        elif roles:
            if assignee not in roles:
                errors.append(Issue(
                    "E_TASK_UNKNOWN_ASSIGNEE",
                    f"{where}.assignee {assignee!r} is not a declared agent "
                    f"(declared: {', '.join(sorted(roles)) or 'none'})"))
            elif roles[assignee] != "executor":
                errors.append(Issue(
                    "E_TASK_ASSIGNEE_ROLE",
                    f"{where}.assignee {assignee!r} has role "
                    f"{roles[assignee]!r}, only executors can own tasks"))

        for key in ("lens", "files_read", "files_write", "deps"):
            if key in task:
                value = task[key]
                if key == "lens":
                    if not isinstance(value, str):
                        errors.append(Issue("E_TASK_FIELD_TYPE",
                                            f"{where}.{key} must be a string"))
                elif not isinstance(value, list) or not all(
                        isinstance(item, str) for item in value):
                    errors.append(Issue("E_TASK_FIELD_TYPE",
                                        f"{where}.{key} must be an array of strings"))

        # acceptance (errors only; normalization failures are reported per item)
        raw_acceptance = task.get("acceptance")
        if raw_acceptance is None or raw_acceptance == []:
            errors.append(Issue("E_ACCEPTANCE_EMPTY",
                                f"{where}.acceptance is missing or empty"))
        else:
            try:
                normalize_acceptance(raw_acceptance)
            except UsageError as exc:
                errors.append(Issue("E_ACCEPTANCE_INVALID", str(exc.message)))

    # DAG over the parsed tasks
    known = set(ids)
    for task in parsed:
        where = f"tasks[{task.get('id', '?')}]"
        deps = task.get("deps", [])
        for dep in deps:
            if dep == task["id"]:
                errors.append(Issue("E_DEP_SELF",
                                    f"{where}: the task depends on itself"))
            elif dep not in known:
                errors.append(Issue(
                    "E_DEP_UNKNOWN",
                    f"{where}.deps: unknown dependency {dep!r} "
                    f"(known ids: {', '.join(sorted(known)) or 'none'})"))
    cycle = find_cycle(parsed)
    if cycle:
        errors.append(Issue("E_DEP_CYCLE",
                            "dependency cycle: " + " -> ".join(cycle)))

    # paths
    for index, task in enumerate(parsed):
        label = task.get("id") if isinstance(task.get("id"), str) and task.get("id") in ids \
            else f"tasks[{index}]"
        where = f"tasks[{label}]"
        for key in ("files_read", "files_write"):
            values = task.get(key, [])
            if not isinstance(values, list):
                continue  # already reported as E_TASK_FIELD_TYPE
            seen_norm: dict[str, str] = {}
            for raw in values:
                try:
                    norm = normalize_path(project_root, raw, check_fs=check_fs)
                except UsageError as exc:
                    code = "E_PATH_" + _path_code(str(exc))
                    errors.append(Issue(code, f"{where}.{key}: {exc}",
                                        path=raw, task_ids=(task["id"],)))
                    continue
                fold = norm.casefold() if case_fold else norm
                if fold in seen_norm and seen_norm[fold] != norm:
                    warnings.append(Issue(
                        "W_PATH_CASE_COLLISION",
                        f"{where}.{key}: {seen_norm[fold]!r} and {norm!r} differ "
                        "only by case on this file system",
                        path=norm, task_ids=(task["id"],)))
                elif norm in seen_norm:
                    warnings.append(Issue(
                        "W_PATH_DUPLICATE",
                        f"{where}.{key}: duplicate path {norm!r}",
                        path=norm, task_ids=(task["id"],)))
                seen_norm[fold] = norm
        def _safe_norm(raw: object) -> str | None:
            try:
                return normalize_path(project_root, raw, check_fs=False)
            except UsageError:
                return None  # already reported by the loop above

        read_list = task.get("files_read", [])
        write_list = task.get("files_write", [])
        read_set = {norm for norm in (_safe_norm(p) for p in read_list
                                      if isinstance(p, str)) if norm} \
            if isinstance(read_list, list) else set()
        write_set = {norm for norm in (_safe_norm(p) for p in write_list
                                       if isinstance(p, str)) if norm} \
            if isinstance(write_list, list) else set()
        both = read_set & write_set
        if both:
            warnings.append(Issue(
                "W_READ_WRITE_SAME_FILE",
                f"{where}: {', '.join(sorted(both))} is both in files_read and "
                "files_write of one task",
                path=", ".join(sorted(both)), task_ids=(task["id"],)))

    # write overlaps
    if not find_cycle(parsed):
        for overlap in find_write_overlaps(parsed, Path(project_root) if check_fs else None):
            pair = (overlap.task_a, overlap.task_b)
            if overlap.ordered:
                warnings.append(Issue(
                    "W_WRITE_OVERLAP_ORDERED",
                    f"write overlap between tasks {overlap.task_a} and "
                    f"{overlap.task_b} on {overlap.path_a!r}, but they run in "
                    "a fixed order (deps)",
                    task_ids=pair))
            elif config.get("allow_write_overlaps") is True:
                warnings.append(Issue(
                    "W_WRITE_OVERLAP_ALLOWED",
                    f"tasks {overlap.task_a} and {overlap.task_b} both write "
                    f"{overlap.path_a!r} (explicitly allowed by the lead)",
                    task_ids=pair))
            else:
                errors.append(Issue(
                    "E_WRITE_OVERLAP",
                    f"tasks {overlap.task_a} and {overlap.task_b} write "
                    f"overlapping paths {overlap.path_a!r} and {overlap.path_b!r}. "
                    "Add a dependency so they run one after the other, or merge "
                    "the tasks",
                    task_ids=pair))

    return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def _path_code(error: UsageError) -> str:
    """Map a normalize_path error to its issue-code suffix."""
    message = str(error)
    if "absolute" in message:
        return "ABSOLUTE"
    if "'..'" in message:
        return "TRAVERSAL"
    if "symlink" in message:
        return "SYMLINK_ESCAPES"
    if "reserved" in message:
        return "RESERVED"
    return "INVALID_CHARS"
