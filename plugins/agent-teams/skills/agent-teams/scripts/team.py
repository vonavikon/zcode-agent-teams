#!/usr/bin/env python3
"""agent-teams engine: общий список задач, блокировки claim, mailbox-сообщения,
гейт приёмки (verify pass/fail), журнал событий, отчёт прогона в Langfuse.

Только stdlib, Python 3.10+. Каталог команды: <root>/.agent-teams/<team>/
  tasks.json   — список задач со статусами
  inbox/       — mailbox-файлы по агентам
  locks/       — блокировки claim (O_EXCL, Windows-safe)
  events.jsonl — журнал переходов (источник для Langfuse)
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

TEAMS_DIR = ".agent-teams"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def hex32(s: str) -> str:
    """Langfuse требует trace/span-id строго из 32 hex."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


def team_dir(root: Path, team: str) -> Path:
    d = root / TEAMS_DIR / team
    if not (d / "tasks.json").exists():
        sys.exit(f"команда не найдена: {d} (сначала init)")
    return d


def load_tasks(d: Path) -> dict:
    return json.loads((d / "tasks.json").read_text(encoding="utf-8"))


def save_tasks(d: Path, data: dict) -> None:
    tmp = d / "tasks.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "tasks.json")


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


# ---------------------------------------------------------------- init

def cmd_init(args) -> None:
    root = Path(args.project_root).resolve()
    src = Path(args.tasks).resolve()
    spec = json.loads(src.read_text(encoding="utf-8"))
    tasks = []
    seen = set()
    for t in spec.get("tasks", []):
        for field in ("id", "title", "assignee", "acceptance"):
            if not t.get(field):
                sys.exit(f"задача без обязательного поля {field}: {t}")
        if t["id"] in seen:
            sys.exit(f"дублирующийся id задачи: {t['id']}")
        seen.add(t["id"])
        tasks.append({
            "id": t["id"],
            "title": t["title"],
            "assignee": t["assignee"],
            "lens": t.get("lens", ""),
            "files_read": t.get("files_read", []),
            "files_write": t.get("files_write", []),
            "acceptance": t["acceptance"],
            "deps": t.get("deps", []),
            "status": "pending",
            "rounds": 0,
            "claimed_at": None,
            "completed_at": None,
            "last_event_ts": None,
        })
    d = root / TEAMS_DIR / args.team
    if d.exists():
        sys.exit(f"каталог команды уже есть: {d}")
    (d / "inbox").mkdir(parents=True)
    (d / "locks").mkdir()
    save_tasks(d, {"team": args.team, "created": now_iso(), "tasks": tasks})
    log_event(d, "team_created", tasks=len(tasks),
              teammates=sorted({t["assignee"] for t in tasks}))
    print(f"OK команда {args.team}: {len(tasks)} задач, {d}")


# ---------------------------------------------------------------- claim

def cmd_claim(args) -> None:
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    data = load_tasks(d)
    t = find_task(data, args.task)
    if t["status"] != "pending":
        sys.exit(f"задача {t['id']} не pending (status={t['status']})")
    for dep in t.get("deps", []):
        dt = find_task(data, dep)
        if dt["status"] != "completed":
            sys.exit(f"зависимость {dep} не завершена (status={dt['status']})")
    lock = d / "locks" / f"{t['id']}.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        owner = "?"
        if lock.exists():
            try:
                owner = json.loads(lock.read_text(encoding="utf-8")).get("owner", "?")
            except Exception:
                pass
        sys.exit(f"задача {t['id']} заблокирована другим агентом: {owner}")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"owner": args.by, "ts": now_iso()}, f, ensure_ascii=False)
    t.update(status="in_progress", claimed_at=now_iso(), last_event_ts=now_iso())
    save_tasks(d, data)
    log_event(d, "task_claimed", actor=args.by, task=t["id"])
    print(f"OK claim {t['id']} → {args.by}")


# ---------------------------------------------------------------- verify

def cmd_verify(args) -> None:
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    data = load_tasks(d)
    t = find_task(data, args.task)
    if t["status"] not in ("in_progress", "verifying"):
        sys.exit(f"задача {t['id']} не в работе (status={t['status']})")
    if args.verdict == "fail" and not args.feedback:
        sys.exit("вердикт fail требует --feedback (фидбек для исполнителя)")
    if args.verdict == "pass":
        t.update(status="completed", completed_at=now_iso(), last_event_ts=now_iso())
        save_tasks(d, data)
        log_event(d, "verify_pass", actor=args.by, task=t["id"], round=t["rounds"] + 1)
        print(f"OK задача {t['id']} закрыта с {t['rounds'] + 1} круга(ов)")
    else:
        t["rounds"] += 1
        t["last_event_ts"] = now_iso()
        save_tasks(d, data)
        log_event(d, "verify_fail", actor=args.by, task=t["id"], round=t["rounds"])
        # фидбек летит исполнителю в mailbox — цикл exit-2: работа не принята
        mb = d / "inbox" / f"{t['assignee']}.json"
        box = json.loads(mb.read_text(encoding="utf-8")) if mb.exists() else {"messages": []}
        box["messages"].append({
            "id": uuid.uuid4().hex[:12], "from": args.by, "to": t["assignee"],
            "text": f"VERIFY FAIL по задаче {t['id']} (круг {t['rounds']}): {args.feedback}",
            "ts": now_iso(), "read": False,
        })
        mb.write_text(json.dumps(box, ensure_ascii=False, indent=2), encoding="utf-8")
        log_event(d, "message_sent", actor=args.by, to=t["assignee"], task=t["id"])
        print(f"FAIL зафиксирован, фидбек в mailbox {t['assignee']}, круг {t['rounds']}")


# ---------------------------------------------------------------- message / read

def cmd_message(args) -> None:
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    mb = d / "inbox" / f"{args.to}.json"
    box = json.loads(mb.read_text(encoding="utf-8")) if mb.exists() else {"messages": []}
    box["messages"].append({
        "id": uuid.uuid4().hex[:12], "from": args.by, "to": args.to,
        "text": args.text, "ts": now_iso(), "read": False,
    })
    mb.write_text(json.dumps(box, ensure_ascii=False, indent=2), encoding="utf-8")
    log_event(d, "message_sent", actor=args.by, to=args.to)
    print(f"OK сообщение {args.by} → {args.to}")


def cmd_read(args) -> None:
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    mb = d / "inbox" / f"{args.for_}.json"
    if not mb.exists():
        print("[]")
        return
    box = json.loads(mb.read_text(encoding="utf-8"))
    unread = [m for m in box["messages"] if not m["read"]]
    for m in unread:
        m["read"] = True
    mb.write_text(json.dumps(box, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(unread, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- status

def cmd_status(args) -> None:
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    data = load_tasks(d)
    print(f"команда {data['team']} (создана {data['created']})")
    for t in data["tasks"]:
        print(f"  {t['id']:<18} {t['status']:<12} круг {t['rounds']}  {t['assignee']:<14} {t['title']}")
    inbox = d / "inbox"
    if inbox.exists():
        for mb in sorted(inbox.glob("*.json")):
            box = json.loads(mb.read_text(encoding="utf-8"))
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
            print(f"HTTP {resp.status} ← {url.rsplit('/', 2)[-2] + '/' + url.rsplit('/', 2)[-1]}")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
        sys.exit(1)


def cmd_langfuse(args) -> None:
    """Трейсы — OTLP-эндпоинтом (events_only не принимает legacy ingestion),
    scores — отдельным POST /api/public/scores."""
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    host = os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3100").strip().rstrip("/")
    if not pub or not sec:
        sys.exit("нет LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY в окружении")
    data = load_tasks(d)
    events = [json.loads(l) for l in (d / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

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
            _attr("langfuse.trace.metadata", {"project": root.name,
                                              "tasks": len(data["tasks"]),
                                              "teammates": sorted({t["assignee"] for t in data["tasks"]})}),
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
          f"{sum(1 for e in events if e['type'].startswith('verify'))} score")
    print(f"трейс: {host}/trace/{trace_id}")


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="создать команду из файла задач")
    sp.add_argument("--team", required=True)
    sp.add_argument("--tasks", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("claim", help="забрать задачу (с блокировкой)")
    sp.add_argument("--team", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=cmd_claim)

    sp = sub.add_parser("verify", help="вердикт гейта приёмки")
    sp.add_argument("--team", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--verdict", choices=["pass", "fail"], required=True)
    sp.add_argument("--feedback", default="")
    sp.add_argument("--by", default="verifier")
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=cmd_verify)

    sp = sub.add_parser("message", help="сообщение агенту в mailbox")
    sp.add_argument("--team", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--text", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=cmd_message)

    sp = sub.add_parser("read", help="прочитать свои непрочитанные")
    sp.add_argument("--team", required=True)
    sp.add_argument("--for", dest="for_", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=cmd_read)

    sp = sub.add_parser("status", help="статус задач и mailbox")
    sp.add_argument("--team", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("langfuse", help="выгрузить прогон в Langfuse")
    sp.add_argument("--team", required=True)
    sp.add_argument("--name", default="")
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=cmd_langfuse)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
