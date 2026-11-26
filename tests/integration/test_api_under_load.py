"""Reading while the engine writes (v2.1 spec 22.2).

The board reads with a connection of its own, read-only, while the lead
and the executors change the state under the engine's lock. What is
pinned: every snapshot the reader gets is whole and consistent on its
own (its parts agree with each other), the cursor of the journal never
goes back, and no read fails or blocks a write.
"""
from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from agent_teams import dashboard_data, engine
from agent_teams.dashboard_server import serve

STATES = {"В очереди", "В работе", "На проверке", "Принято", "Нужно ваше внимание", "Отменена",
          "Не отвечает"}
PLAN = {
    "title": "Проверка под нагрузкой",
    "goal": "Проверить, что доска читает, пока движок пишет",
    "tasks": [
        {"id": f"s{n}", "title": f"Этап {n}", "assignee": f"w{n % 4}", "lens": "behavior",
         "files_write": [f"out/s{n}.md"], "acceptance": ["готово"], "deps": []}
        for n in range(1, 13)],
}


@pytest.fixture
def busy_team(tmp_path):
    """Twelve stages in shared mode: no worktree to make, so the engine
    can move fast while the reader reads."""
    (tmp_path / "out").mkdir()
    spec = {"config": {"workspace": "shared"}, **PLAN,
            "agents": [{"id": f"w{n}", "role": "executor"} for n in range(4)]}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    lead = engine.op_init(tmp_path, "t", path)["lead_token"]
    return {"root": tmp_path, "lead": lead}


def consistent(snap: dict, task_ids: set[str]) -> list[str]:
    """What must hold in every snapshot, whatever else is happening."""
    faults = []
    seen = {t["id"] for t in snap["tasks"]}
    if seen != task_ids:
        faults.append(f"stages came and went: {sorted(seen ^ task_ids)}")
    for task in snap["tasks"]:
        if task["state"] not in STATES:
            faults.append(f"unknown state {task['state']!r}")
    # The progress counts the stages that are not cancelled, all of them.
    live = [t for t in snap["tasks"] if t["state"] != "Отменена"]
    if snap["progress"]["total"] != len(live):
        faults.append("progress counts another plan")
    done = sum(1 for t in live if t["state"] == "Принято")
    if snap["progress"]["done"] != done:
        faults.append(f"progress says {snap['progress']['done']} done, the stages say {done}")
    seqs = [e["seq"] for e in snap["events"]]
    if seqs != sorted(seqs) or len(seqs) != len(set(seqs)):
        faults.append("the feed is out of order or repeats itself")
    if seqs and snap["last_seq"] < seqs[-1]:
        faults.append("the cursor is behind the newest line it sent")
    for event in snap["events"]:
        if event.get("task") is not None and event.get("task") not in {
                t["title"] for t in snap["tasks"]}:
            faults.append("a line of the feed names a stage that is not there")
    quality = snap["quality"]
    if quality["accepted_by_decision"] > quality["handed_over"]:
        faults.append("more decisions than hand-overs")
    return faults


def spend(team: dict, task_id: str) -> dict:
    """One usage record: a write of its own kind, while the reader reads."""
    return engine.op_tokens(team["root"], "t", task_id, 500, "w1", "glm-5.3-flash")


class TestTheDataReader:
    def test_every_snapshot_is_whole_while_the_engine_works(self, busy_team):
        root = busy_team["root"]
        ids = {t["id"] for t in engine.op_status(root, "t")["tasks"]}
        faults: list[str] = []
        reads = [0]
        stop = threading.Event()

        def read() -> None:
            while not stop.is_set():
                for level in ("simple", "advanced"):
                    try:
                        snap = dashboard_data.snapshot(root, "t", level=level)
                    except Exception as exc:              # a read must never fail
                        faults.append(f"read failed: {type(exc).__name__}: {exc}")
                        return
                    faults.extend(consistent(snap, ids))
                    reads[0] += 1

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            for task_id in sorted(ids)[:6]:
                claim = engine.op_claim(root, "t", task_id, f"w{int(task_id[1:]) % 4}")
                engine.op_heartbeat(root, "t", claim["owner"], claim["execution_token"])
                engine.op_submit(root, "t", task_id, claim["owner"], claim["execution_token"])
                spend(busy_team, task_id)
                engine.op_verify(root, "t", task_id, "fail", "нет оценок", busy_team["lead"])
                engine.op_submit(root, "t", task_id, claim["owner"], claim["execution_token"])
                spend(busy_team, task_id)
                engine.op_verify(root, "t", task_id, "pass", "", busy_team["lead"],
                                 override_reason="test")
            engine.op_cancel(root, "t", sorted(ids)[6], busy_team["lead"])
            engine.op_pause(root, "t", busy_team["lead"])
            engine.op_resume(root, "t", busy_team["lead"])
        finally:
            stop.set()
            reader.join(timeout=30)
        assert not reader.is_alive()
        assert reads[0] >= 4, "the reader must have read while the engine worked"
        assert faults == []


class TestTheServer:
    def test_the_board_answers_while_the_engine_works(self, busy_team):
        server = serve(busy_team["root"], "t", port=0, lead_token=busy_team["lead"])
        assert server is not None
        port = server.server_address[1]
        ids = {t["id"] for t in engine.op_status(busy_team["root"], "t")["tasks"]}
        faults: list[str] = []
        stop = threading.Event()

        def poll() -> None:
            while not stop.is_set():
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    conn.request("GET", "/api/snapshot?team=t")
                    response = conn.getresponse()
                    data = response.read()
                    conn.close()
                except Exception as exc:
                    faults.append(f"request failed: {type(exc).__name__}: {exc}")
                    return
                if response.status != 200:
                    faults.append(f"status {response.status}")
                    return
                faults.extend(consistent(json.loads(data), ids))

        threads = [threading.Thread(target=poll, daemon=True) for _ in range(3)]
        for thread in threads:
            thread.start()
        try:
            for task_id in sorted(ids)[6:10]:
                claim = engine.op_claim(busy_team["root"], "t", task_id, "w2")
                engine.op_submit(busy_team["root"], "t", task_id, "w2", claim["execution_token"])
            # The owner acts from the board while other pages read it.
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            body = json.dumps({"action": "pause", "confirm": True}).encode("utf-8")
            conn.putrequest("POST", "/api/actions?team=t", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{port}")
            conn.putheader("Origin", f"http://127.0.0.1:{port}")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            response = conn.getresponse()
            assert response.status == 200 and json.loads(response.read())["paused"] is True
            conn.close()
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=30)
            server.shutdown()
            server.server_close()
        assert not any(thread.is_alive() for thread in threads)
        assert faults == []


def test_a_read_leaves_no_trace_in_the_state(busy_team):
    url = f"file:{busy_team['root'] / '.agent-teams' / 't' / 'state.db'}?mode=ro"

    def content() -> str:
        with sqlite3.connect(url, uri=True) as conn:
            return "\n".join(conn.iterdump())

    before = content()
    for level in ("simple", "advanced"):
        dashboard_data.snapshot(busy_team["root"], "t", level=level, parts=("task_events",))
    assert content() == before
