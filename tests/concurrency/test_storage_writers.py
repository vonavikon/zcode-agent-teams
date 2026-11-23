"""Concurrent writers: no lost events, chain stays valid."""
from __future__ import annotations


def _writer(team_dir: str, count: int, tag: str) -> None:  # pragma: no cover - subprocess
    from agent_teams import events
    from agent_teams.storage import open_store

    with open_store(__import__("pathlib").Path(team_dir)) as store:
        for i in range(count):
            # A deferred read of the previous hash would race here; the
            # store's BEGIN IMMEDIATE transaction serializes it.
            with store.transaction():
                events.append_event(store.conn, event_type="message_sent",
                                    team_id="t1", run_id="r1", actor_id=tag,
                                    metadata={"i": i})


def test_no_lost_events_and_chain_ok(tmp_path, spawn_ctx):
    from agent_teams.storage import open_store

    with open_store(tmp_path) as store:
        with store.transaction():
            store.create_team({"id": "t1", "project_root": "."})
    n, m = 8, 25
    procs = [spawn_ctx.Process(target=_writer, args=(str(tmp_path), m, f"w{i}"))
             for i in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=180)
    assert all(p.exitcode == 0 for p in procs)

    from agent_teams import events
    from agent_teams.storage import open_store

    with open_store(tmp_path) as store:
        rows = store.all("SELECT seq FROM events ORDER BY seq")
        assert len(rows) == n * m
        seqs = [r["seq"] for r in rows]
        assert len(set(seqs)) == len(seqs)
        check = events.verify_chain(store.conn, "t1")
        assert check.ok and check.events_checked == n * m
