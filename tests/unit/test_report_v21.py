"""The report of the team's work for a manager (v2.1 spec, section 18).

A real team is driven through the engine in a git project, the way the
tests of the dashboard drive one, and its report is read the way a
reader would: by its sections, by what they say, and by what must never
reach the page before the technical details (or must never reach it at
all).

The team has everything a report can be told about. One stage was
accepted by the decision of the lead, one by a real check with the
report of a verifier, one failed two checks and is stopped, and one is
in work under a title that tries to be markup. The only record of usage
has no price.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import timedelta, timezone
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path

import pytest

from agent_teams import dashboard_data, engine, report
from agent_teams import events as eventlog
from agent_teams.errors import NotFoundError
from agent_teams.storage import open_store

TEAM = "crew"
TITLE = "Отчётный проект"
GOAL = "Подготовить отчёт для руководителя"
HOSTILE_TITLE = "Проверить отчёт <script>alert(1)</script>"
HOSTILE_REMARK = "нет оценок <script>вероятности</script>"
TITLES = {"stage-report": "Собрать отчёт", "stage-check": HOSTILE_TITLE,
          "stage-risks": "Оценить риски", "stage-review": "Проверить защиту отчёта"}
WHO = {"stage-report": "writer", "stage-check": "analyst", "stage-risks": "writer",
       "stage-review": "guard"}
PEOPLE = ("writer", "analyst", "guard", "checker-1")
# The identifiers are long on purpose: a test that no identifier reaches
# the page before the technical details has to be able to tell one.
SECTION_IDS = ["sec-goal", "sec-result", "sec-done", "sec-not-done", "sec-problems",
               "sec-how-solved", "sec-quality", "sec-cost", "sec-time", "sec-team",
               "sec-history", "sec-tech"]
HEADINGS = ["Цель проекта", "Итоговый результат", "Что выполнено", "Что не удалось выполнить",
            "Какие проблемы возникали", "Как они решались", "Качество результатов", "Расходы",
            "Время выполнения", "Состав команды и вклад специалистов", "История этапов",
            "Технические подробности"]
# The whole vocabulary of the page: nothing here can load another file.
TAGS = {"html", "head", "meta", "title", "style", "body", "main", "header",
        "section", "details", "summary", "footer", "nav", "h1", "h2", "h3", "p", "ol", "ul",
        "li", "dl", "dt", "dd", "span", "b", "small", "code", "table", "thead", "tbody",
        "tr", "th", "td", "time", "i", "a", "br", "div"}
ATTRIBUTES = {"id", "class", "href", "lang", "charset", "name", "content", "scope",
              "aria-label"}


# ---------------------------------------------------------- a real team


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def init_git(root: Path) -> None:
    (root / "out").mkdir()
    # Git keeps no empty directory; a worktree of the base commit needs "out".
    (root / "out" / ".gitkeep").write_text("", encoding="utf-8")
    (root / ".gitignore").write_text(".agent-teams/\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@e.i")
    git(root, "config", "user.name", "t")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")


def _tasks() -> list[dict]:
    return [
        {"id": "stage-report", "title": TITLES["stage-report"], "assignee": "writer",
         "lens": "docs", "files_write": ["out/report.md"], "acceptance": ["готово"],
         "deps": [], "description": "Собрать данные за квартал"},
        {"id": "stage-check", "title": TITLES["stage-check"], "assignee": "analyst",
         "lens": "behavior", "files_write": ["out/check.md"], "acceptance": ["проверено"],
         "deps": ["stage-report"]},
        {"id": "stage-risks", "title": TITLES["stage-risks"], "assignee": "writer",
         "lens": "robustness", "files_write": ["out/risks.md"], "acceptance": ["риски"],
         "deps": []},
        # Checked by a machine (a security scanner) and judged by a verifier.
        {"id": "stage-review", "title": TITLES["stage-review"], "assignee": "guard",
         "lens": "security", "files_write": ["out/review.md"], "acceptance": [
             {"id": "AC-REVIEW-SCAN", "description": "no secrets in the review",
              "type": "security_check", "verification": "test -f out/review.md"},
             {"id": "AC-REVIEW-TEXT", "description": "the review is sound",
              "type": "code_review"}], "deps": []},
    ]


def spec_file(root: Path, config: dict | None = None,
              only: tuple[str, ...] | None = None) -> Path:
    only = tuple(only or TITLES)
    spec = {"title": TITLE, "goal": GOAL, "config": config or {},
            "tasks": [task for task in _tasks() if task["id"] in only]}
    (root / "out").mkdir(exist_ok=True)
    path = root / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def _notification(root: Path) -> Path:
    path = root / "notif.txt"
    path.write_text("<usage>\nsubagent_tokens: 1234\ntool_uses: 3\nduration_ms: 900\n"
                    "</usage>", encoding="utf-8")
    return path


def _verifier_report(root: Path, submission_id: str) -> dict:
    """The structured report of a verifier, bound to what was handed over."""
    with open_store(root / ".agent-teams" / TEAM) as store:
        submission = store.get_submission(submission_id)
    return {
        "schema_version": "1.0", "team_id": TEAM, "task_id": "stage-review",
        "submission_id": submission["id"], "commit_sha": submission["commit_sha"],
        "verifier": {"id": "checker-1", "mode": "subagent"},
        "criteria": [
            {"criterion_id": "AC-REVIEW-SCAN", "status": "pass",
             "evidence": [{"path": "out/review.md", "line_start": 1,
                           "claim": "nothing to scan"}], "reason": "scanned"},
            {"criterion_id": "AC-REVIEW-TEXT", "status": "pass",
             "evidence": [{"path": "out/review.md", "line_start": 1, "line_end": 1,
                           "claim": "the review is sound"}], "reason": "reviewed"},
        ],
        "verdict": "pass",
    }


def _build(root: Path, only: tuple[str, ...] | None = None) -> dict:
    """A real team in a git project, driven through the engine."""
    only = tuple(only or TITLES)
    init_git(root)
    lead = engine.op_init(root, TEAM, spec_file(root, only=only))["lead_token"]
    git(root, "add", "-A")
    git(root, "commit", "-qm", "spec")
    claims: dict[str, dict] = {}

    def claim(task_id):
        claims[task_id] = engine.op_claim(root, TEAM, task_id, WHO[task_id])

    def deliver(task_id, text):
        # The executor works in its own worktree and commits there.
        workspace = Path(claims[task_id]["workspace"])
        name = task_id.removeprefix("stage-") + ".md"
        (workspace / "out" / name).write_text(text, encoding="utf-8")
        git(workspace, "add", "-A")
        git(workspace, "commit", "-qm", "work")
        return engine.op_submit(root, TEAM, task_id, WHO[task_id],
                                claims[task_id]["execution_token"])

    if "stage-report" in only:
        claim("stage-report")
        deliver("stage-report", "отчёт за квартал\n")
        # A PASS without the verifier's report is the lead's own decision.
        engine.op_verify(root, TEAM, "stage-report", "pass", "", lead, by="checker-1",
                         override_reason="demo")
    if "stage-risks" in only:
        claim("stage-risks")
        deliver("stage-risks", "риски без оценок\n")
        engine.op_verify(root, TEAM, "stage-risks", "fail", HOSTILE_REMARK, lead,
                         by="checker-1")
        deliver("stage-risks", "риски, всё так же без оценок\n")
        engine.op_verify(root, TEAM, "stage-risks", "fail", "оценок по-прежнему нет",
                         lead, by="checker-1")
    if "stage-review" in only:
        claim("stage-review")
        submitted = deliver("stage-review", "защита проверена\n")
        engine.op_verify(root, TEAM, "stage-review", "pass", "", lead, by="checker-1",
                         report=_verifier_report(root, submitted["submission_id"]))
    if "stage-check" in only:
        claim("stage-check")
        # A notification carries no model: the price of its record is unknown.
        engine.op_usage(root, TEAM, "stage-check", "analyst", _notification(root))
    return {"root": root, "lead": lead, "db": root / ".agent-teams" / TEAM / "state.db"}


def _render(built: dict) -> str:
    with open_store(built["root"] / ".agent-teams" / TEAM) as store:
        return report.render_report(store, TEAM)


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    """Built once per module: git worktrees are slow and reading changes
    nothing. A test that writes builds a team of its own."""
    return _build(tmp_path_factory.mktemp("report-team"))


@pytest.fixture(scope="module")
def picture(team) -> dict:
    """One snapshot for the whole module. The page and what it is
    compared with come from the same moment: an open span of the
    timeline grows with every second, and two snapshots taken seconds
    apart disagree about its length."""
    return dashboard_data.snapshot(team["root"], TEAM, level="advanced",
                                   parts=("task_events",), whole=True)


@pytest.fixture(scope="module")
def page(picture) -> str:
    return report._page(picture)


@pytest.fixture(scope="module")
def snap(picture) -> dict:
    return picture


# ------------------------------------------------------- reading the page


class _Page(HTMLParser):
    """Every tag of the page with its attributes, its text and its style."""

    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict, int]] = []
        self.words: list[str] = []
        self.css: list[str] = []
        self._in_style = False
        self.feed(markup)
        self.close()
        self.css = "".join(self.css)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs), self.getpos()[0]))
        self._in_style = tag == "style"

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        (self.css if self._in_style else self.words).append(data)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _text(markup: str) -> str:
    """What a reader sees in a fragment of the page, on one line. Words set
    in bold or in code stay glued to their punctuation, as they are read."""
    body = re.sub(r"<style>.*?</style>", " ", markup, flags=re.S)
    body = re.sub(r"</?(?:b|code|i)>", "", body)
    return _norm(unescape(re.sub(r"<[^>]+>", " ", body)))


def _section(page: str, section_id: str) -> str:
    """The markup of one section, from its id to the next section."""
    begins = {name: page.index(f'id="{name}"') for name in SECTION_IDS}
    later = [position for name, position in begins.items() if position > begins[section_id]]
    start = page.rfind("<", 0, begins[section_id])
    end = page.rfind("<", 0, min(later)) if later else len(page)
    return page[start:end].rstrip()


def _block(text: str, first: str, *following: str) -> str:
    """A part of a text: from one stage to the next one named."""
    start = text.index(first)
    ends = [text.index(title, start + len(first)) for title in following
            if title in text[start + len(first):]]
    return text[start:min(ends) if ends else len(text)]


def _tables(markup: str) -> list[list[list[str]]]:
    """Every table of a fragment as rows of cell texts."""
    found = []
    for table in re.findall(r"<table.*?</table>", markup, flags=re.S):
        found.append([[_text(cell) for cell in
                       re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)]
                      for row in re.findall(r"<tr>(.*?)</tr>", table, flags=re.S)])
    return found


def _identifiers(snap: dict) -> set[str]:
    """Every identifier, commit and path of the team, read from the snapshot
    the report was built from."""
    found = {snap["team"], *PEOPLE, snap["project"]["root"],
             snap["run"]["tech"]["run_id"], snap["run"]["tech"]["status"]}
    for task in snap["tasks"]:
        own = task["tech"]
        found.add(task["id"])
        found |= {own[key] for key in ("branch", "base_commit", "trusted_base_commit",
                                       "draft_checkpoint_commit", "accepted_commit_sha",
                                       "head_commit") if own.get(key)}
        if task["result"].get("version"):
            found.add(task["result"]["version"])
    for submission in snap["tech"]["submissions"]:
        found |= {submission["id"], submission["commit_sha"], submission["base_commit_sha"],
                  submission["branch"]}
    build = snap["integration"]["tech"]
    found |= {build[key] for key in ("branch", "base_commit", "commit_sha") if build[key]}
    return {value for value in found if value}


def _assert_self_contained(page: str) -> None:
    """Nothing external of any kind and no JavaScript: the page is the only
    file it needs."""
    lowered = page.lower()
    for forbidden in ("http", "<script", "<link", "src=", "javascript:", "@import",
                      "url(", "<iframe", "<object", "<embed", "<base", "<img", "<form",
                      "<svg", "<canvas", "<video"):
        assert forbidden not in lowered, forbidden
    parsed = _Page(page)
    tags = [tag for tag, _, _ in parsed.tags]
    assert set(tags) <= TAGS, set(tags) - TAGS
    assert tags.count("style") == 1 and tags.count("details") == 1
    line_of = {tag: line for tag, _, line in parsed.tags}
    assert line_of["head"] < line_of["style"] < line_of["body"]
    for _, attrs, _ in parsed.tags:
        assert set(attrs) <= ATTRIBUTES, attrs
        for name, value in attrs.items():
            assert not name.lower().startswith("on"), (name, value)
            if name == "href":       # a link may only lead inside the page
                assert value.startswith("#") and value[1:] in SECTION_IDS, value
    assert "url(" not in parsed.css.lower() and "@import" not in parsed.css.lower()


# ------------------------------------------------------------ the document


class TestTheDocument:
    def test_twelve_sections_with_stable_ids_in_the_order_of_the_spec(self, page):
        found = re.findall(r'<(section|details) id="(sec-[a-z-]+)"', page)
        assert [section_id for _, section_id in found] == SECTION_IDS
        assert len(set(SECTION_IDS)) == 12
        # Eleven open sections, then the one collapsible block.
        assert [kind for kind, _ in found] == ["section"] * 11 + ["details"]
        words = _text(page)
        for number, heading in enumerate(HEADINGS, 1):
            assert f"{number}. {heading}" in words

    def test_no_section_is_just_a_heading(self, page, tmp_path):
        """A section with nothing to say says so in a sentence."""
        for section_id, heading in zip(SECTION_IDS, HEADINGS):
            words = _text(_section(page, section_id))
            assert words.startswith(f"{SECTION_IDS.index(section_id) + 1}. {heading} "), words[:80]
            assert len(words) > len(heading) + 10, section_id
        # A team where nothing happened yet says so in every section too.
        fresh = {"root": tmp_path}
        init_git(tmp_path)
        engine.op_init(tmp_path, TEAM, spec_file(tmp_path))
        idle = _render(fresh)
        for section_id, heading in zip(SECTION_IDS, HEADINGS):
            words = _text(_section(idle, section_id))
            assert words.startswith(f"{SECTION_IDS.index(section_id) + 1}. {heading} "), words[:80]
            assert len(words) > len(heading) + 10, section_id
        problems = _text(_section(idle, "sec-problems"))
        assert problems.startswith(f"5. {HEADINGS[4]} Проблем не было")

    def test_nothing_external_and_no_javascript(self, page):
        _assert_self_contained(page)

    def test_the_written_file_is_the_only_file_the_report_needs(self, team, tmp_path):
        out = tmp_path / "deep" / "report.html"
        result = engine.op_report(team["root"], TEAM, out)
        assert Path(result["report"]) == out
        assert [entry.name for entry in out.parent.iterdir()] == ["report.html"]
        # Alone in an empty folder, with nothing else to reach for.
        alone = tmp_path / "alone"
        alone.mkdir()
        copied = shutil.copy(out, alone / "report.html")
        _assert_self_contained(Path(copied).read_text(encoding="utf-8"))

    def test_the_technical_details_are_in_one_element_closed_by_default(self, page, snap):
        assert page.count("<details") == 1 and page.count("</details>") == 1
        [attrs] = [attrs for tag, attrs, _ in _Page(page).tags if tag == "details"]
        assert attrs == {"id": "sec-tech"}        # no "open": closed by default
        before, inside = page.split("<details", 1)
        for identifier in _identifiers(snap):
            assert identifier not in before, identifier
        assert not re.search(r"\b[0-9a-f]{40}\b", before)    # no commit of any kind
        assert ".agent-teams" not in before and "agent-teams/" not in before
        # ... and what a technical reader needs is all there, inside it.
        for identifier in (TEAM, snap["run"]["tech"]["run_id"], *PEOPLE, snap["project"]["root"]):
            assert identifier in inside, identifier
        for task in snap["tasks"]:
            assert task["id"] in inside, task["id"]
            if task["result"]["version"]:
                assert task["result"]["version"] in inside

    def test_a_hostile_title_and_a_hostile_remark_are_escaped(self, page):
        assert HOSTILE_TITLE not in page and HOSTILE_REMARK not in page
        assert "<script" not in page.lower()
        assert escape(HOSTILE_TITLE) in page and escape(HOSTILE_REMARK) in page
        assert "script" not in [tag for tag, _, _ in _Page(page).tags]
        # The reader still gets the words of the title.
        assert HOSTILE_TITLE in _text(_section(page, "sec-not-done"))

    def test_no_long_dash_anywhere(self, page):
        assert "\u2014" not in page and "\u2013" not in page

    def test_the_words_of_the_module_have_no_long_dash(self):
        source = Path(report.__file__).read_text(encoding="utf-8")
        assert "\u2014" not in source and "\u2013" not in source

    def test_the_page_is_russian_and_the_old_english_report_is_gone(self, page):
        [html_attrs] = [attrs for tag, attrs, _ in _Page(page).tags if tag == "html"]
        assert html_attrs == {"lang": "ru"}
        assert f"<title>Отчёт о работе команды: {TITLE}</title>" in page
        for old in ("agent-teams report", "Task graph", "Metrics and cost",
                    "Submissions (immutable snapshots)", "Recovery history",
                    "Integration and apply", "no verification attempts yet"):
            assert old not in page, old


# ------------------------------------------------- what the sections say


class TestWhatTheSectionsSay:
    def test_goal(self, page):
        words = _text(_section(page, "sec-goal"))
        assert words == f"1. {HEADINGS[0]} {TITLE} Цель: {GOAL}"

    def test_a_team_without_a_goal_says_so(self, tmp_path):
        init_git(tmp_path)
        path = spec_file(tmp_path)
        spec = json.loads(path.read_text(encoding="utf-8"))
        del spec["goal"], spec["title"]
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        engine.op_init(tmp_path, TEAM, path)
        words = _text(_section(_render({"root": tmp_path}), "sec-goal"))
        assert words.endswith("Цель проекта не записана в задании команды.")
        assert "Цель:" not in words

    def test_result(self, page, snap):
        words = _text(_section(page, "sec-result"))
        integration = snap["integration"]
        assert integration["story"] in words
        for step in integration["steps"]:
            assert f"{step['title']} {step['text']}" in words
        # What the team produced: the files of the accepted results, by stage.
        assert f"{TITLES['stage-report']}: out/report.md" in words
        assert f"{TITLES['stage-review']}: out/review.md" in words
        assert "out/risks.md" not in words and "out/check.md" not in words
        progress = snap["progress"]
        assert (f"Этапов в плане: {progress['total']}. Завершено: {progress['done']}, "
                f"выполняется: {progress['active']}, "
                f"требует внимания: {progress['needs_attention']}.") in words

    def test_done_names_each_accepted_stage_with_the_expected_and_the_real_files(
            self, page):
        words = _text(_section(page, "sec-done"))
        assert "Завершено 2 из 4 этапов." in words
        for title in (TITLES["stage-risks"], HOSTILE_TITLE):
            assert title not in words, title
        report_stage = _block(words, TITLES["stage-report"], TITLES["stage-review"])
        assert "Принято" in report_stage
        assert "Что нужно сделать Собрать данные за квартал" in report_stage
        assert "Ожидаемый результат out/report.md" in report_stage
        assert "Полученный результат Принятая версия результата. Файлы: out/report.md." \
            in report_stage
        assert "Длительность" in report_stage
        review_stage = _block(words, TITLES["stage-review"])
        assert "Ожидаемый результат out/review.md" in review_stage
        assert "Полученный результат Принятая версия результата. Файлы: out/review.md." \
            in review_stage

    def test_not_done_names_the_state_and_the_next_step_of_every_other_stage(self, page):
        words = _text(_section(page, "sec-not-done"))
        assert "Этапов не принято: 2." in words
        for title in (TITLES["stage-report"], TITLES["stage-review"]):
            assert title not in words, title
        checking = _block(words, TITLES["stage-check"], TITLES["stage-risks"])
        assert "В работе" in checking
        assert "Дальше Специалист работает" in checking
        assert "Ожидаемый результат out/check.md" in checking
        assert "Что уже сделано Начата" in checking
        risks = _block(words, TITLES["stage-risks"])
        assert "Нужно ваше внимание" in risks
        assert "Дальше Ждёт вашего решения" in risks
        assert "Что уже сделано Начата, Передана на проверку" in risks
        assert ("Результат Промежуточный результат, ещё не принят. Результат передавался "
                "2 раза, показана последняя версия. Файлы: out/risks.md.") in risks
        # A stage that waits for the owner says what happened and what is needed.
        assert "Что произошло Исполнитель передавал результат 2 раза" in risks
        assert "Что нужно от вас Посмотрите замечания проверяющего" in risks
        assert "Замечания проверяющего оценок по-прежнему нет" in risks

    def test_a_cancelled_stage_is_one_that_was_not_done(self, tmp_path):
        init_git(tmp_path)
        lead = engine.op_init(tmp_path, TEAM, spec_file(tmp_path))["lead_token"]
        engine.op_cancel(tmp_path, TEAM, "stage-risks", lead)
        page = _render({"root": tmp_path})
        words = _text(_section(page, "sec-not-done"))
        assert "Этапов не принято: 4." in words
        cancelled = _block(words, TITLES["stage-risks"], TITLES["stage-review"])
        assert "Отменена" in cancelled and "Дальше Задача снята" in cancelled

    def test_problems_are_newest_first_and_solved_in_the_same_order(self, page, snap):
        problems = _text(_section(page, "sec-problems"))
        solved = _text(_section(page, "sec-how-solved"))
        recovery = snap["recovery"]
        assert recovery["story"] in problems
        newest = sorted(recovery["incidents"], key=lambda i: i["when"], reverse=True)
        heads_p = [problems.index(f"{n}. {i['title']}, этап «{i['task']}»")
                   for n, i in enumerate(newest, 1)]
        heads_s = [solved.index(f"{n}. {i['title']}, этап «{i['task']}»")
                   for n, i in enumerate(newest, 1)]
        assert heads_p == sorted(heads_p) and heads_s == sorted(heads_s)
        assert [i["title"] for i in newest] == ["Задача остановлена", "Результат не прошёл проверку"]
        assert "1. Задача остановлена, этап «Оценить риски»" in problems
        assert ", ждёт вашего решения" in problems       # it waits for the owner
        assert "2. Результат не прошёл проверку, этап «Оценить риски»" in problems
        assert HOSTILE_REMARK in problems                # what happened, in words
        for incident in newest:
            block = _block(solved, f"{newest.index(incident) + 1}. {incident['title']}")
            assert "Что система сделала Система " + ", ".join(incident["did"]) + "." in block
            if incident["saved"]:
                assert "Сохранено " + incident["saved"] + "." in block
            assert "Итог " + incident["outcome"] + "." in block
            assert "Что нужно от вас " + incident["action"] in block
        assert "Итог нужна помощь: задача остановлена. Есть риск потери результата." in solved
        assert "Что нужно от вас Разрешите ещё одну попытку либо снимите задачу." in solved
        assert "Итог исполнитель доработал результат и передал его снова." in solved

    def test_a_team_without_problems_says_so(self, tmp_path):
        init_git(tmp_path)
        engine.op_init(tmp_path, TEAM, spec_file(tmp_path))
        page = _render({"root": tmp_path})
        # One plain sentence, nothing to list.
        assert _text(_section(page, "sec-problems")) == (
            "5. Какие проблемы возникали Проблем не было: ни один исполнитель не терял "
            "связь, ни одна проверка не вернула результат.")
        assert _text(_section(page, "sec-how-solved")) == (
            "6. Как они решались Проблем не было, решать было нечего.")

    def test_quality(self, page, snap):
        words = _text(_section(page, "sec-quality"))
        quality = snap["quality"]
        assert quality["story"] in words
        assert "Что проверила машина" in words and "Что оценил проверяющий" in words
        # The found defects, each with the title of its stage.
        for problem in quality["problems"]:
            assert f"{problem['task']}: {problem['remark']}" in words
        assert "Найденные недостатки" in words
        assert TITLES["stage-check"] in words          # still waiting for its check

    def test_cost_by_stage_and_by_specialist(self, page):
        words = _text(_section(page, "sec-cost"))
        for label in ("потрачено", "бюджет", "осталось", "в среднем на задачу",
                      "цена принятого результата", "По этапам", "По специалистам"):
            assert label in words, label

    def test_time(self, page, snap):
        words = _text(_section(page, "sec-time"))
        assert "Работа началась " + report._when(snap["created_at"]) in words
        assert "Работа ещё идёт" in words and "Работа завершена" not in words
        assert "в работе " in words and "работа заняла" not in words
        # Every stage by what it was doing, from the journal.
        for row in snap["timeline"]["rows"]:
            for span in row["spans"]:
                length = report._seconds(span["from"], span["to"])
                assert (f"{span['text']}: с {report._when(span['from'])}, "
                        f"{report._dur(length)}") in words, row["title"]

    def test_team(self, page, snap):
        words = _text(_section(page, "sec-team"))
        for agent in snap["agents"]:
            assert agent["name"] in words and agent["does"] in words, agent["name"]
            assert agent["state"] in words and agent["doing"] in words
            if agent["checks"] is not None:
                assert f"Проверок выполнено: {agent['checks']}" in words
            else:
                assert f"завершено {agent['completed']} из {agent['total']}" in words
                for task in agent["tasks"]:
                    assert f"{task['title']}: {task['state']}" in words
        checker = next(a for a in snap["agents"] if a["checks"] is not None)
        assert f"принято по проверке: {checker['completed']}" in words
        assert "в счёт проверок не входит" in words

    def test_history_of_every_stage_oldest_first(self, page, snap):
        section = _section(page, "sec-history")
        for task in snap["tasks"]:
            block = _block(section, f"<h3>{escape(task['title'])} ", "<h3>")
            events = sorted(snap["task_events"][task["id"]], key=lambda e: e["seq"])
            lines = re.findall(r'<li><time>(.*?)</time>(?:<span class="who">(.*?)</span>)?'
                               r"<span>(.*?)</span></li>", block, flags=re.S)
            assert lines, task["id"]
            assert [line[0] for line in lines] == [report._when(e["ts"]) for e in events]
            assert [unescape(line[1]) for line in lines] == [e["specialist"] for e in events]
            for line, event in zip(lines, events):
                assert unescape(line[2]).startswith(event["title"])
        # The journal half of a manual acceptance is a record of the decision.
        words = _text(section)
        decided = _block(words, TITLES["stage-report"], HOSTILE_TITLE)
        assert "Результат принят вручную, без отчёта о проверке" in decided
        assert "Проверка пройдена" not in decided
        checked = _block(words, TITLES["stage-review"])
        assert "Проверка пройдена" in checked

    def test_technical_details(self, page, snap):
        inside = page.split("<details", 1)[1]
        words = _text(inside)
        assert f"Идентификатор команды {TEAM}" in words
        assert snap["run"]["tech"]["run_id"] in words
        for task in snap["tasks"]:
            assert f"{task['id']} {task['title']} {task['tech']['status']}" in words
        for key in snap["tech"]["config"]:
            assert key in words, key
        # The journal: seq, time, event type and actor, the whole of it, oldest first.
        journal = _tables(inside)[-1]
        assert journal[0][:4] == ["№", "Время", "Тип события", "Этап"]
        events = sorted(snap["events"], key=lambda e: e["seq"])
        assert [row[0] for row in journal[1:]] == [str(e["seq"]) for e in events]
        for row, event in zip(journal[1:], events):
            assert row[1] == report._fmt(event["ts"], "%d.%m.%Y %H:%M:%S")
            assert row[2] == event["type"]
            assert row[4] == (event["tech"]["actor_id"] or "-")
            assert row[5] == event["title"]

    def test_the_journal_is_whole_however_long_it_grew(self, tmp_path):
        init_git(tmp_path)
        engine.op_init(tmp_path, TEAM, spec_file(tmp_path))
        # Far more events than the feed of a polling board carries (200).
        with open_store(tmp_path / ".agent-teams" / TEAM) as store:
            [run] = store.all("SELECT id FROM runs WHERE team_id = ?", (TEAM,))
            with store.transaction():
                for _ in range(250):
                    eventlog.append_event(store.conn, event_type="token_usage_recorded",
                                          team_id=TEAM, run_id=run["id"],
                                          task_id="stage-report", actor_id="writer",
                                          metadata={"add": 1, "total": 1})
            page = report.render_report(store, TEAM)
        journal = _tables(page.split("<details", 1)[1])[-1]
        assert len(journal) > 250
        assert journal[1][0] == "1" and journal[1][2] == "team_created"


# ------------------------------------------------- the rules of the board


class TestTheRulesOfTheBoard:
    def test_every_figure_is_the_one_of_the_snapshot(self, page, snap):
        quality, budget, progress = snap["quality"], snap["budget"], snap["progress"]
        tables = _tables(_section(page, "sec-quality"))
        figures = {row[0]: row[1] for row in tables[0]}
        one = report._one_of
        assert figures["передано на проверку результатов, которые исполнители сдали"] \
            == str(quality["handed_over"])
        assert figures["принято решением руководитель команды принял без проверки"] \
            == str(quality["accepted_by_decision"])
        first = "прошёл с первого раза" if quality["first_time"] == 1 else "прошли с первого раза"
        assert figures[f"{first} без доработки"] == str(quality["first_time"])
        assert figures[f"{one(quality['reworked'], 'доработка', 'доработки', 'доработок')} "
                       "результатов пришлось переделывать"] == str(quality["reworked"])
        machine = {row[0]: row[1] for table in tables[1:] for row in table}
        run = one(quality["machine"]["run"], "проверка", "проверки", "проверок")
        assert machine[f"{run} команды и файлы, которые проверяет машина"] \
            == str(quality["machine"]["run"])
        judged = one(quality["checks"], "проверка", "проверки", "проверок")
        assert machine[f"{judged} оценка содержания проверяющим"] == str(quality["checks"])
        assert machine["не пройдено"] == str(quality["failed"])
        # A machine check and the judgement of the checker are never one number.
        assert "Что проверила машина" in _text(_section(page, "sec-quality"))
        assert "Что оценил проверяющий" in _text(_section(page, "sec-quality"))
        # The spend.
        cost = {row[0]: row[1] for row in _tables(_section(page, "sec-cost"))[0]}
        unknown = budget["unknown_records"]
        records = "записи" if unknown == 1 else "записей"
        counted = "она не учтена" if unknown == 1 else "они не учтены"
        note = (f"Цена {unknown} {records} неизвестна: {counted}." if unknown
                else "Все расходы известны.")
        assert cost[f"потрачено {note}"] == budget["spent_text"]
        # Every sum of records carries the count of the records without a price.
        assert cost[f"в среднем на задачу по задачам, которые уже начались {note}"] \
            == budget["per_task_text"]
        assert cost[f"цена принятого результата расходы на все попытки, "
                    f"включая доработки {note}"] == budget["per_result_text"]
        # The stages of the whole.
        result = _text(_section(page, "sec-result"))
        assert f"Результаты приняты {progress['done']} из {progress['total']}" in result

    def test_a_cost_that_is_not_known_is_never_a_zero(self, page, snap):
        assert snap["budget"]["spent_text"] == "не определена"
        words = _text(_section(page, "sec-cost"))
        assert "Потрачено: не определена." in words
        # Not one sum is named where none is known.
        assert "$" not in words and "0 дол" not in words
        assert "потрачено Цена 1 записи неизвестна: она не учтена. не определена" in words
        said = "Цена 1 записи неизвестна: она не учтена."
        assert (f"в среднем на задачу по задачам, которые уже начались {said} не определена"
                in words)
        assert (f"цена принятого результата расходы на все попытки, включая доработки {said} "
                "не определена") in words
        assert "бюджет не согласован" in words and "осталось нет предела" in words
        # By stage and by specialist: no priced record, so "not defined".
        assert f"{HOSTILE_TITLE} не определена и ещё цена 1 записи неизвестна" in words
        assert "AI-аналитик не определена и ещё цена 1 записи неизвестна" in words

    def test_a_sum_with_an_unpriced_record_keeps_its_known_part_and_says_so(self, tmp_path):
        init_git(tmp_path)
        lead = engine.op_init(tmp_path, TEAM, spec_file(tmp_path, {"budget_usd": 20}))["lead_token"]
        engine.op_tokens(tmp_path, TEAM, "stage-report", 1_000_000, "writer", "glm-5.3")
        engine.op_usage(tmp_path, TEAM, "stage-check", "analyst", _notification(tmp_path))
        words = _text(_section(_render({"root": tmp_path, "lead": lead}), "sec-cost"))
        assert "На проект выделено $20.00. Потрачено $1.80. Осталось $18.20." in words
        assert "Предел считается по известным расходам: цена части записей неизвестна." in words
        assert "потрачено Цена 1 записи неизвестна: она не учтена. $1.80" in words
        assert f"{TITLES['stage-report']} $1.80" in words
        assert f"{HOSTILE_TITLE} не определена и ещё цена 1 записи неизвестна" in words
        assert "AI-редактор документации $1.80" in words
        assert "AI-аналитик не определена и ещё цена 1 записи неизвестна" in words
        # The two averages are sums over the records: they say so too. No
        # stage was started here, so the one per task is not defined at all.
        assert ("в среднем на задачу по задачам, которые уже начались "
                "Цена 1 записи неизвестна: она не учтена. не определена") in words

    def test_a_manual_acceptance_is_a_decision_never_a_passed_check(self, page, snap):
        before = page.split("<details", 1)[0]
        words = _text(before)
        # Where the lead decided, the words say so, in every section.
        decided = _block(_text(_section(page, "sec-done")), TITLES["stage-report"],
                         TITLES["stage-review"])
        assert "Проверка результата Принято вручную, без проверки" in decided
        assert "Проверка пройдена" not in decided
        quality = _text(_section(page, "sec-quality"))
        assert "1 результат принят решением руководителя команды, без проверки." in quality
        checker = next(a for a in snap["agents"] if a["checks"] is not None)
        assert checker["checks"] == 3       # two failed checks and one real pass
        assert f"Проверок выполнено: {checker['checks']}" in words
        assert f"принято по проверке: {checker['completed']}" in words
        # "Проверка пройдена" is said of the one stage the checker really
        # passed, in the section of what was done and in its history.
        assert _text(_section(page, "sec-done")).count("Проверка пройдена") == 1
        assert _text(_section(page, "sec-quality")).count("Проверка пройдена") == 0
        assert "Принято вручную, без проверки" in _text(_section(page, "sec-quality")) \
            or "принято решением" in _text(_section(page, "sec-quality"))
        assert "Результат принят вручную, без отчёта о проверке" in \
            _text(_section(page, "sec-history"))


# --------------------------------------------- after the run has finished


class TestAfterTheRunFinished:
    def test_the_report_can_be_written_after_the_run_finished(self, tmp_path):
        built = _build(tmp_path, only=("stage-report", "stage-review"))
        engine.op_integrate(tmp_path, TEAM, built["lead"])
        assert engine.op_apply(tmp_path, TEAM, built["lead"]) == {"applied": True,
                                                                  "run": "finished"}
        out = tmp_path / "report.html"
        engine.op_report(tmp_path, TEAM, out)
        page = out.read_text(encoding="utf-8")
        with sqlite3.connect(f"file:{built['db']}?mode=ro", uri=True) as conn:
            status, finished_at = conn.execute("SELECT status, finished_at FROM runs").fetchone()
        assert status == "finished" and finished_at
        _assert_self_contained(page)
        words = _text(page)
        assert "Состояние работы: Завершено (результат применён к проекту)." in words
        assert "Финальная версия готова: изменения внесены в проект." in words
        time_section = _text(_section(page, "sec-time"))
        assert f"Работа завершена {report._when(finished_at)}" in time_section
        assert "Продолжительность работа заняла " in time_section
        assert ("Работа ещё идёт" not in time_section
                and "Продолжительность в работе " not in time_section)
        assert _text(_section(page, "sec-not-done")).endswith("Все этапы приняты.")
        # The end of the run is the team's own event: it is in the journal.
        journal = _tables(page.split("<details", 1)[1])[-1]
        assert journal[-1][2:] == ["team_completed", "-", "lead", "Работа команды завершена"]
        assert any(row[5] == "Изменения внесены в проект" for row in journal)


# ------------------------------------------------------------- the contract


class TestTheContract:
    def test_the_names_and_the_signatures_are_kept(self):
        render, write = inspect.signature(report.render_report), inspect.signature(report.write_report)
        assert list(render.parameters) == ["store", "team"]
        assert render.return_annotation == "str"
        assert list(write.parameters) == ["store", "team", "out_path"]
        assert write.return_annotation == "Path"

    def test_an_unknown_team_is_named(self, team):
        with open_store(team["root"] / ".agent-teams" / TEAM) as store:
            with pytest.raises(NotFoundError, match="nobody"):
                report.render_report(store, "nobody")

    def test_the_report_reads_and_never_writes(self, team):
        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"
        wal = Path(str(team["db"]) + "-wal")
        with open_store(team["root"] / ".agent-teams" / TEAM) as store:
            before = (sha(team["db"]), sha(wal))
            for _ in range(3):
                report.render_report(store, TEAM)
            assert (sha(team["db"]), sha(wal)) == before

    def test_write_report_makes_the_folder_and_returns_the_path(self, team, tmp_path):
        out = tmp_path / "a" / "b" / "report.html"
        with open_store(team["root"] / ".agent-teams" / TEAM) as store:
            assert report.write_report(store, TEAM, out) == out
        assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>\n<html lang=\"ru\">")

    def test_the_snapshot_is_taken_from_the_row_of_the_team(self, team, monkeypatch):
        asked = {}
        real = dashboard_data.snapshot

        def spy(project_root, name, **options):
            asked.update(root=project_root, team=name, options=options)
            return real(project_root, name, **options)
        monkeypatch.setattr(report.dashboard_data, "snapshot", spy)
        with open_store(team["root"] / ".agent-teams" / TEAM) as store:
            report.render_report(store, TEAM)
        assert asked["root"] == str(team["root"].resolve())
        assert asked["team"] == TEAM
        assert asked["options"]["level"] == "advanced"
        assert "task_events" in asked["options"]["parts"]


# ----------------------------------------------- the words of time and money


class TestTheWordsOfTimeAndMoney:
    def test_a_length_of_time_in_the_words_of_the_board(self):
        seconds = (0, 45, 60, 720, 3600, 3900, 90000, 180000, -5)
        assert [report._dur(value) for value in seconds] == [
            "0 с", "45 с", "1 мин", "12 мин", "1 ч", "1 ч 5 мин", "1 дн 1 ч", "2 дн 2 ч", "0 с"]

    def test_a_moment_is_shown_in_the_zone_of_the_one_who_reads(self):
        moment = "2026-11-25T18:29:31.329Z"
        assert report._when(moment, timezone.utc) == "25.11.2026 18:29"
        assert report._when(moment, timezone(timedelta(hours=3))) == "25.11.2026 21:29"
        assert report._when("not a date") == "время не записано"
        assert report._when(None) == "время не записано"
        assert report._zone(moment, timezone.utc) == "UTC"
        assert report._zone(moment, timezone(timedelta(hours=3))) == "UTC+03:00"

    def test_a_sum_without_one_priced_record_is_not_defined(self):
        assert report._money_rows({"a": 1.8}, {"b": 1}) == [
            ("a", "$1.80", ""), ("b", "не определена", "и ещё цена 1 записи неизвестна")]
        assert report._money_rows({"a": 1.8}, {"a": 2}) == [
            ("a", "$1.80", "и ещё цена 2 записей неизвестна")]
        assert report._money_rows({}, {}) == []
        assert report._reliability(0) == "Все расходы известны."
        assert report._reliability(1) == "Цена 1 записи неизвестна: она не учтена."
        assert report._reliability(4) == "Цена 4 записей неизвестна: они не учтены."
