"""An uncommitted transaction of a killed process leaves no rows."""
from __future__ import annotations

import time


def _crasher(team_dir: str, ready_path: str) -> None:  # pragma: no cover - subprocess
    from pathlib import Path

    from agent_teams import events
    from agent_teams.storage import open_store

    with open_store(Path(team_dir)) as store:
        with store.transaction():
            events.append_event(store.conn, event_type="task_created",
                                team_id="t1", run_id="r1", actor_id="doomed")
            events.append_event(store.conn, event_type="task_claimed",
                                team_id="t1", run_id="r1", actor_id="doomed")
            Path(ready_path).write_text("in-tx", encoding="utf-8")
            time.sleep(60)  # killed while the transaction is open


def test_uncommitted_rows_are_invisible_and_db_usable(tmp_path, spawn_ctx):
    from agent_teams.storage import open_store

    with open_store(tmp_path) as store:
        with store.transaction():
            store.create_team({"id": "t1", "project_root": "."})

    ready = tmp_path / "ready"
    p = spawn_ctx.Process(target=_crasher, args=(str(tmp_path), str(ready)))
    p.start()
    for _ in range(200):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists()
    p.terminate()  # SIGKILL-equivalent for the purposes of this suite
    p.join(timeout=30)

    with open_store(tmp_path) as store:
        rows = store.all("SELECT * FROM events")
        assert rows == []  # the killed transaction rolled back
        with store.transaction():
            from agent_teams import events

            events.append_event(store.conn, event_type="team_created",
                                team_id="t1", run_id="r2", actor_id="lead")
        assert len(store.all("SELECT * FROM events")) == 1
