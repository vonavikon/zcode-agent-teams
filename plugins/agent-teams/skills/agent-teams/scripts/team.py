#!/usr/bin/env python3
"""agent-teams engine: shared task list, mailbox messages, acceptance gate,
event log, run report to Langfuse.

Stdlib only, Python 3.10+. Team directory: <root>/.agent-teams/<team>/
  tasks.json   — task list with statuses
  inbox/       — per-agent mailbox files
  locks/       — state lock (.state.lock): serializes all mutations
  events.jsonl — transition log (the source for Langfuse)

Concurrency v0.2.0: every mutation of tasks.json/mailbox runs under a
single lock with retries and stale-lock detection; writes are atomic
(unique tmp + os.replace with a PermissionError retry — Windows).
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

TEAMS_DIR = ".agent-teams"
MAX_ROUNDS = 2
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 7200.0
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def hex32(s: str) -> str:
    """Langfuse requires trace/span ids to be exactly 32 hex chars."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


def check_name(kind: str, value: str) -> None:
    if not NAME_RE.match(value or ""):
        sys.exit(f"недопустимое имя {kind}: {value!r} (шаблон ^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}$)")


def team_dir(root: Path, team: str) -> Path:
    check_name("команды", team)
    d = root / TEAMS_DIR / team
    if not (d / "tasks.json").exists():
        sys.exit(f"команда не найдена: {d} (сначала init)")
    return d


def replace_retry(src: Path, dst: Path, attempts: int = 5) -> None:
    """os.replace with retries: on Windows dst can be briefly locked."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(0.2 * (i + 1))
    os.replace(src, dst)  # final attempt — let the exception propagate


def write_atomic(path: Path, data: dict) -> None:
    tmp = path.parent / f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_retry(tmp, path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class StateLock:
    """Single lock for all state mutations. A stale lock (>2 h) gets reclaimed."""

    def __init__(self, d: Path, owner: str):
        self.path = d / "locks" / ".state.lock"
        self.owner = owner

    def __enter__(self) -> "StateLock":
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"owner": self.owner, "ts": now_iso()}, f, ensure_ascii=False)
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > LOCK_STALE_S:
                        self.path.unlink(missing_ok=True)  # stale — reclaim it
                        continue
                except FileNotFoundError:
                    continue  # the lock was released between checks
                if time.monotonic() > deadline:
                    sys.exit(f"не удалось взять лок состояния ({LOCK_TIMEOUT_S:.0f}с): {self.path}")
                time.sleep(0.2)

    def __exit__(self, *exc) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except PermissionError:
            time.sleep(0.2)
            self.path.unlink(missing_ok=True)


def log_event(d: Path, etype: str, **fields) -> None:
    rec = {"ts": now_iso(), "type": etype}
    rec.update(fields)
    with open(d / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()


def find_task(data: dict, tid: str) -> dict:
    for t in data["tasks"]:
        if t["id"] == tid:
            return t
    sys.exit(f"задача не найдена: {tid}")


def save_mailbox(d: Path, agent: str, msg: dict) -> None:
    mb = d / "inbox" / f"{agent}.json"
    box = read_json(mb) if mb.exists() else {"messages": []}
    box["messages"].append(msg)
    write_atomic(mb, box)


# ---------------------------------------------------------------- init

def cmd_init(args) -> None:
    root = Path(args.project_root).resolve()
    check_name("команды", args.team)
    src = Path(args.tasks).resolve()
    spec = json.loads(src.read_text(encoding="utf-8"))
    tasks, seen = [], set()
    for t in spec.get("tasks", []):
        for field in ("id", "title", "assignee", "acceptance"):
            if not t.get(field):
                sys.exit(f"задача без обязательного поля {field}: {t}")
        check_name("id задачи", t["id"])
        check_name("исполнителя", t["assignee"])
        if t["id"] in seen:
            sys.exit(f"дублирующийся id задачи: {t['id']}")
        seen.add(t["id"])
        tasks.append({
            "id": t["id"], "title": t["title"], "assignee": t["assignee"],
            "lens": t.get("lens", ""),
            "files_read": t.get("files_read", []), "files_write": t.get("files_write", []),
            "acceptance": t["acceptance"], "deps": t.get("deps", []),
            "status": "pending", "rounds": 0,
            "claimed_at": None, "completed_at": None, "last_event_ts": None, "tokens": 0,
        })
    d = root / TEAMS_DIR / args.team
    if d.exists():
        sys.exit(f"каталог команды уже есть: {d}")
    (d / "inbox").mkdir(parents=True)
    (d / "locks").mkdir()
    write_atomic(d / "tasks.json", {"team": args.team, "created": now_iso(), "tasks": tasks})
    log_event(d, "team_created", tasks=len(tasks),
              teammates=sorted({t["assignee"] for t in tasks}))
    print(f"OK команда {args.team}: {len(tasks)} задач, {d}")


# ---------------------------------------------------------------- claim

def cmd_claim(args) -> None:
    root = Path(args.project_root).resolve()
    check_name("агента", args.by)
    d = team_dir(root, args.team)
    with StateLock(d, f"claim:{args.by}"):
        data = read_json(d / "tasks.json")
        t = find_task(data, args.task)
        if t["status"] != "pending":
            sys.exit(f"задача {t['id']} не pending (status={t['status']})")
        for dep in t.get("deps", []):
            dt = find_task(data, dep)
            if dt["status"] != "completed":
                sys.exit(f"зависимость {dep} не завершена (status={dt['status']})")
        t.update(status="in_progress", claimed_at=now_iso(), last_event_ts=now_iso(),
                 assignee=args.by)  # owner = actual executor: acceptance feedback goes to them
        write_atomic(d / "tasks.json", data)
        log_event(d, "task_claimed", actor=args.by, task=t["id"])
    print(f"OK claim {t['id']} → {args.by}")


# ---------------------------------------------------------------- verify

def cmd_verify(args) -> None:
    root = Path(args.project_root).resolve()
    check_name("агента", args.by)
    d = team_dir(root, args.team)
    with StateLock(d, f"verify:{args.by}"):
        data = read_json(d / "tasks.json")
        t = find_task(data, args.task)
        if t["status"] not in ("in_progress", "verifying"):
            sys.exit(f"задача {t['id']} не в работе (status={t['status']})")
        if args.verdict == "fail" and not args.feedback:
            sys.exit("вердикт fail требует --feedback (фидбек для исполнителя)")
        if args.verdict == "pass":
            t.update(status="completed", completed_at=now_iso(), last_event_ts=now_iso())
            write_atomic(d / "tasks.json", data)
            log_event(d, "verify_pass", actor=args.by, task=t["id"], round=t["rounds"] + 1)
            print(f"OK задача {t['id']} закрыта, провалов приёмки: {t['rounds']}")
        else:
            if t["rounds"] >= MAX_ROUNDS:
                # round limit reached: don't return to the executor — the task goes to the lead
                t.update(status="blocked", last_event_ts=now_iso())
                write_atomic(d / "tasks.json", data)
                log_event(d, "round_limit", actor=args.by, task=t["id"], round=t["rounds"])
                save_mailbox(d, t["assignee"], {
                    "id": uuid.uuid4().hex[:12], "from": args.by, "to": t["assignee"],
                    "text": f"ROUND LIMIT по задаче {t['id']}: {MAX_ROUNDS} провала приёмки — задача передана лиду (release или разбор вручную)",
                    "ts": now_iso(), "read": False})
                log_event(d, "message_sent", actor=args.by, to=t["assignee"], task=t["id"])
                print(f"ROUND LIMIT: задача {t['id']} → blocked, разбирает лид")
                return
            t["rounds"] += 1
            t["last_event_ts"] = now_iso()
            write_atomic(d / "tasks.json", data)
            log_event(d, "verify_fail", actor=args.by, task=t["id"], round=t["rounds"])
            # feedback goes to the executor's mailbox — exit-2 loop: work not accepted
            save_mailbox(d, t["assignee"], {
                "id": uuid.uuid4().hex[:12], "from": args.by, "to": t["assignee"],
                "text": f"VERIFY FAIL по задаче {t['id']} (круг {t['rounds']}): {args.feedback}",
                "ts": now_iso(), "read": False})
            log_event(d, "message_sent", actor=args.by, to=t["assignee"], task=t["id"])
            print(f"FAIL зафиксирован, фидбек в mailbox {t['assignee']}, круг {t['rounds']}")


# ---------------------------------------------------------------- release / tokens

def cmd_release(args) -> None:
    root = Path(args.project_root).resolve()
    check_name("агента", args.by)
    d = team_dir(root, args.team)
    with StateLock(d, f"release:{args.by}"):
        data = read_json(d / "tasks.json")
        t = find_task(data, args.task)
        if t["status"] == "completed":
            sys.exit(f"задача {t['id']} завершена — release не нужен")
        t.update(status="pending", claimed_at=None, last_event_ts=now_iso())
        write_atomic(d / "tasks.json", data)
        log_event(d, "task_released", actor=args.by, task=t["id"], rounds=t["rounds"])
    print(f"OK release {t['id']} → pending (кругов было: {t['rounds']})")


def cmd_tokens(args) -> None:
    root = Path(args.project_root).resolve()
    if args.by:
        check_name("агента", args.by)
    d = team_dir(root, args.team)
    if not args.add:
        data = read_json(d / "tasks.json")
        t = find_task(data, args.task)
        print(f"{t['id']}: {t.get('tokens', 0)} токенов")
        return
    with StateLock(d, f"tokens:{args.by or 'lead'}"):  # read and write under a single lock
        data = read_json(d / "tasks.json")
        t = find_task(data, args.task)
        t["tokens"] = t.get("tokens", 0) + args.add
        t["last_event_ts"] = now_iso()
        write_atomic(d / "tasks.json", data)
    log_event(d, "task_tokens", actor=args.by or "lead", task=t["id"], add=args.add,
              total=t["tokens"])
    print(f"OK +{args.add} → {t['tokens']} у задачи {t['id']}")


# ---------------------------------------------------------------- message / read

def cmd_message(args) -> None:
    root = Path(args.project_root).resolve()
    check_name("агента", args.to)
    check_name("агента", args.by)
    d = team_dir(root, args.team)
    with StateLock(d, f"message:{args.by}"):
        save_mailbox(d, args.to, {
            "id": uuid.uuid4().hex[:12], "from": args.by, "to": args.to,
            "text": args.text, "ts": now_iso(), "read": False})
        log_event(d, "message_sent", actor=args.by, to=args.to)
    print(f"OK сообщение {args.by} → {args.to}")


def cmd_read(args) -> None:
    root = Path(args.project_root).resolve()
    check_name("агента", args.for_)
    d = team_dir(root, args.team)
    mb = d / "inbox" / f"{args.for_}.json"
    if not mb.exists():
        print("[]")
        return
    with StateLock(d, f"read:{args.for_}"):
        box = read_json(mb)
        unread = [m for m in box["messages"] if not m["read"]]
        for m in unread:
            m["read"] = True
        write_atomic(mb, box)
    print(json.dumps(unread, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- status

def cmd_status(args) -> None:
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    data = read_json(d / "tasks.json")
    print(f"команда {data['team']} (создана {data['created']})")
    for t in data["tasks"]:
        print(f"  {t['id']:<18} {t['status']:<12} провал.приёмки {t['rounds']}  "
              f"токены {t.get('tokens', 0):<7} {t['assignee']:<14} {t['title']}")
    inbox = d / "inbox"
    if inbox.exists():
        for mb in sorted(inbox.glob("*.json")):
            box = read_json(mb)
            n = sum(1 for m in box["messages"] if not m["read"])
            if n:
                print(f"  mailbox {mb.stem}: {n} непрочитанных")


# ---------------------------------------------------------------- langfuse

def _nano(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return str(int(dt.timestamp() * 1_000_000_000))


def _attr(key: str, value) -> dict:
    return {"key": key, "value": {"stringValue": value if isinstance(value, str)
                                  else json.dumps(value, ensure_ascii=False)}}


def _post(url: str, payload: dict, pub: str, sec: str) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(f"{pub}:{sec}".encode()).decode()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(f"HTTP {resp.status} ← .../{url.split('/api/')[-1]}")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
        sys.exit(1)


def cmd_langfuse(args) -> None:
    """Traces go to the OTLP endpoint (events_only doesn't accept legacy ingestion),
    scores go to a separate POST /api/public/scores."""
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    host = os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3100").strip().rstrip("/")
    if not pub or not sec:
        sys.exit("нет LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY в окружении")
    data = read_json(d / "tasks.json")
    events, bad = [], 0
    for line in (d / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1  # a corrupt log line must not crash the upload
    if bad:
        print(f"предупреждение: пропущено битых строк журнала: {bad}")

    trace_id = hex32(f"{root}|{args.team}")
    team_end = max((t["completed_at"] or t["last_event_ts"] or now_iso()) for t in data["tasks"])
    root_span = uuid.uuid4().hex[:16]
    spans = [{
        "traceId": trace_id, "spanId": root_span,
        "name": args.name or f"agent-teams:{args.team}",
        "startTimeUnixNano": _nano(data["created"]), "endTimeUnixNano": _nano(team_end),
        "attributes": [
            _attr("langfuse.trace.name", args.name or f"agent-teams:{args.team}"),
            _attr("langfuse.internal.is_app_root", "true"),
            _attr("langfuse.observation.type", "span"),
            _attr("langfuse.trace.metadata", {
                "project": root.name, "tasks": len(data["tasks"]),
                "teammates": sorted({t["assignee"] for t in data["tasks"]}),
                "tokens_total": sum(t.get("tokens", 0) for t in data["tasks"])}),
        ],
    }]
    for t in data["tasks"]:
        start = t["claimed_at"] or t["completed_at"] or team_end
        end = t["completed_at"] or t["last_event_ts"] or now_iso()
        spans.append({
            "traceId": trace_id, "spanId": hex32(f"{root}|{args.team}|{t['id']}")[:16],
            "parentSpanId": root_span,
            "name": f"{t['id']}: {t['title']}",
            "startTimeUnixNano": _nano(start), "endTimeUnixNano": _nano(end),
            "attributes": [
                _attr("langfuse.observation.type", "span"),
                _attr("langfuse.observation.metadata", {
                    "assignee": t["assignee"], "lens": t.get("lens", ""),
                    "files_write": t["files_write"], "rounds": t["rounds"],
                    "tokens": t.get("tokens", 0),
                    "verdict": "pass" if t["status"] == "completed" else t["status"]}),
            ],
        })
    _post(f"{host}/api/public/otel/v1/traces",
          {"resourceSpans": [{"scopeSpans": [{"scope": {"name": "agent-teams"}, "spans": spans}]}]},
          pub, sec)

    for e in events:
        if e["type"] in ("verify_pass", "verify_fail"):
            _post(f"{host}/api/public/scores",
                  {"traceId": trace_id, "name": "verify-gate",
                   "value": 1.0 if e["type"] == "verify_pass" else 0.0,
                   "dataType": "NUMERIC",
                   "comment": f"task={e.get('task')} round={e.get('round')}"},
                  pub, sec)
    print(f"OK: trace {trace_id}, {len(spans)} span, "
          f"{sum(1 for e in events if e['type'].startswith('verify'))} score, "
          f"токенов {sum(t.get('tokens', 0) for t in data['tasks'])}")
    print(f"трейс: {host}/trace/{trace_id}")


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_: str):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--team", required=True)
        sp.add_argument("--project-root", default=".")
        return sp

    sp = add("init", "создать команду из файла задач")
    sp.add_argument("--tasks", required=True)
    sp.set_defaults(fn=cmd_init)

    sp = add("claim", "забрать задачу (под общим локом состояния)")
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.set_defaults(fn=cmd_claim)

    sp = add("verify", "вердикт гейта приёмки")
    sp.add_argument("--task", required=True)
    sp.add_argument("--verdict", choices=["pass", "fail"], required=True)
    sp.add_argument("--feedback", default="")
    sp.add_argument("--by", default="verifier")
    sp.set_defaults(fn=cmd_verify)

    sp = add("release", "вернуть задачу в pending (после ROUND LIMIT или сбоя)")
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", default="lead")
    sp.set_defaults(fn=cmd_release)

    sp = add("tokens", "учесть токены задачи (--add N) или посмотреть")
    sp.add_argument("--task", required=True)
    sp.add_argument("--add", type=int, default=0)
    sp.add_argument("--by", default="")
    sp.set_defaults(fn=cmd_tokens)

    sp = add("message", "сообщение агенту в mailbox")
    sp.add_argument("--to", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--text", required=True)
    sp.set_defaults(fn=cmd_message)

    sp = add("read", "прочитать свои непрочитанные")
    sp.add_argument("--for", dest="for_", required=True)
    sp.set_defaults(fn=cmd_read)

    add("status", "статус задач и mailbox").set_defaults(fn=cmd_status)

    sp = add("langfuse", "выгрузить прогон в Langfuse")
    sp.add_argument("--name", default="")
    sp.set_defaults(fn=cmd_langfuse)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
