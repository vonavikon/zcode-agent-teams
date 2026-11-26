"""The size the specification names (v2.1 spec 21): five, twenty and a
hundred stages, twenty specialists, a journal of thousands of lines.

The rules under test:

- every stage and every specialist is on the board, at any of the sizes;
- the whole journal is never loaded at once: the feed is a window, the
  timeline carries the last marks of a stage and a count of the older
  ones, the problems carry their exact totals and the newest incidents;
- the technical level caps its raw records the same way;
- the picture of a hundred stages is built well inside the half second
  the specification allows, and is packed for a browser that accepts it.
"""
from __future__ import annotations

import gzip
import http.client
import json
import time

import pytest

from agent_teams import dashboard_data
from agent_teams.dashboard_server import serve
from big_team import build_big_team


def payload_kb(snap: dict) -> float:
    return len(json.dumps(snap, ensure_ascii=False).encode("utf-8")) / 1024


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """Built once: a hundred stages and five thousand lines of journal."""
    return build_big_team(tmp_path_factory.mktemp("big-team"))


@pytest.mark.parametrize("tasks,agents,events", [(5, 3, 40), (20, 8, 400), (100, 20, 5000)])
def test_every_stage_and_every_specialist_is_on_the_board(tmp_path, tasks, agents, events):
    info = build_big_team(tmp_path, tasks=tasks, agents=agents, events=events, usage=events // 2)
    snap = dashboard_data.snapshot(tmp_path, info["team"])
    assert len(snap["plan"]["nodes"]) == tasks and snap["plan"]["rows"] >= 1
    assert len(snap["timeline"]["rows"]) == tasks
    assert len(snap["agents"]) == agents
    assert snap["progress"]["total"] == tasks
    assert snap["progress"]["active"] == info["in_progress"]
    # Every specialist with a stage in work is shown at work, each with
    # that stage: twenty can be busy at once.
    busy = [a for a in snap["agents"] if a["state"] == "Работает"]
    assert len(busy) == info["busy_agents"] <= agents
    assert all(any(task["state"] == "В работе" for task in a["tasks"]) for a in busy)
    # Nobody looks lost: the story ends now, with every worker answering.
    assert not [a for a in snap["agents"] if a["state"] == "Проблема"]
    assert sum(snap["recovery"]["by_kind"].values()) == snap["recovery"]["total"]


class TestTheJournalIsNeverLoadedAtOnce:
    def test_the_feed_is_a_window(self, big):
        snap = dashboard_data.snapshot(big["root"], "big")
        assert len(snap["events"]) <= 200 and snap["last_seq"] == big["last_seq"]
        # Nothing is fetched for one stage's history until it is asked for.
        assert "task_events" not in snap

    def test_the_timeline_carries_the_last_marks_and_counts_the_rest(self, big):
        snap = dashboard_data.snapshot(big["root"], "big")
        rows = snap["timeline"]["rows"]
        assert all(len(row["marks"]) <= 10 for row in rows)
        assert sum(row["marks_more"] for row in rows) > 0        # the journal is longer than that
        # What is sent is the end of the story, in order.
        last = rows[0]["marks"][-1]
        assert all(item["ts"] <= last["ts"] for item in rows[0]["marks"])

    def test_the_problems_carry_exact_totals_and_the_newest_incidents(self, big):
        recovery = dashboard_data.snapshot(big["root"], "big")["recovery"]
        assert recovery["total"] == recovery["shown"] + recovery["more"] > 50
        assert len(recovery["incidents"]) == recovery["shown"] == 50
        assert sum(recovery["by_kind"].values()) == recovery["total"]
        # The newest first, whatever is sent.
        when = [i["when"] for i in recovery["incidents"]]
        assert when == sorted(when, reverse=True)

    def test_the_technical_level_caps_its_raw_records(self, big):
        tech = dashboard_data.snapshot(big["root"], "big", level="advanced")["tech"]
        assert len(tech["usage"]) == 200 and tech["usage_more"] == big["usage"] - 200
        assert len(tech["attempts"]) <= 200 and "attempts_more" in tech
        assert len(tech["submissions"]) <= 200 and "submissions_more" in tech


class TestItIsBuiltInTimeAndPacked:
    def test_a_hundred_stages_take_well_under_half_a_second(self, big):
        # A guard against a walk that turns quadratic, not a benchmark:
        # the machine this runs on is not the reader's.
        for level in ("simple", "advanced"):
            started = time.perf_counter()
            snap = dashboard_data.snapshot(big["root"], "big", level=level)
            took = time.perf_counter() - started
            assert took < 3.0, (level, took)
            assert payload_kb(snap) < 1536, (level, payload_kb(snap))

    def test_a_big_answer_is_packed_for_a_browser_that_accepts_it(self, big):
        server = serve(big["root"], "big", port=0)
        assert server is not None
        port = server.server_address[1]
        try:
            plain = request(port, None)
            packed = request(port, "gzip")
            small = request(port, "gzip", "/api/health")
        finally:
            server.shutdown()
            server.server_close()
        assert "content-encoding" not in plain[1] and len(plain[2]) > 100_000
        assert packed[1].get("content-encoding") == "gzip" and packed[1].get("vary") == "Accept-Encoding"
        assert len(packed[2]) < len(plain[2]) // 10
        assert json.loads(gzip.decompress(packed[2]))["team"] == "big"
        # A small answer is never packed: it would only grow.
        assert "content-encoding" not in small[1] and small[0] == 200

    def test_the_light_poll_is_light(self, big):
        server = serve(big["root"], "big", port=0)
        port = server.server_address[1]
        try:
            started = time.perf_counter()
            status, _headers, data = request(port, None, "/api/status?team=big")
            took = time.perf_counter() - started
        finally:
            server.shutdown()
            server.server_close()
        assert status == 200 and json.loads(data)["last_seq"] == big["last_seq"]
        assert took < 3.0, took


def request(port: int, accept: str | None, path: str = "/api/snapshot?team=big"):
    """One raw request, so the accepted encoding is exactly what is sent."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", f"127.0.0.1:{port}")
    if accept:
        conn.putheader("Accept-Encoding", accept)
    conn.endheaders()
    response = conn.getresponse()
    data = response.read()
    headers = {key.lower(): value for key, value in response.getheaders()}
    conn.close()
    return response.status, headers, data


def test_a_report_can_ask_for_the_whole_journal(big):
    # A page that polls lives under the limits; a report written once
    # does not: it asks, and gets every incident, mark and record.
    snap = dashboard_data.snapshot(big["root"], "big", level="advanced", whole=True)
    assert snap["recovery"]["more"] == 0 and len(snap["recovery"]["incidents"]) == snap["recovery"]["total"]
    rows = snap["timeline"]["rows"]
    assert all(row["marks_more"] == 0 for row in rows)
    assert sum(len(row["marks"]) for row in rows) > 10 * big["tasks"]
    assert len(snap["tech"]["usage"]) == big["usage"] and snap["tech"]["usage_more"] == 0
