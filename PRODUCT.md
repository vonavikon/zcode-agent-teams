# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary:** a person who wants to hand a task to a team of AI
specialists, watch the work and get a finished result, and is not
required to understand programming, Git, agent architecture or how AI
tools work. The owner named the roles in the specification: product
managers, project managers, business analysts, team leads, entrepreneurs,
researchers, specialists without a technical background. They use the
product from inside ZCode, on their own machine, while a team is working
or after it has finished. Their job: know what the team is doing, what
is done, what needs their decision, what it cost, and take the result.

**Secondary:** developers, AI engineers and technical specialists who
want to control how the work is executed. They get a second level of
the same board (identifiers, branches, commits, raw records). The
interface serves both, starting from the primary one.

The lead session of ZCode (the agent that plans the work, verifies and
integrates) is a user of the engine and of its command line, not of the
board.

## Product Purpose

ZCode Agent Teams turns a tool for managing subagents into an
understandable system for organising and controlling the work of an AI
team. The user manages the work and the result, never the technical
machinery of execution.

Success: a person who opens the board answers, in a few seconds and
without documentation, terminal logs or technical graphs:

1. What is my AI team doing right now?
2. What part of the work is already done?
3. What exactly is each executor doing?
4. What is the next stage?
5. Are there problems, and do they need my decision?
6. How good is the work?
7. How much time and money has been spent?
8. When can the result be expected?
9. What is ready, and what can be looked at?

And can go from a task in plain words to a running team, confirm the
plan before the start, act where a decision is theirs, and hand a
report to a manager.

## Positioning

A digital team of specialists the user delegates work to. What a
neighbouring product cannot copy truthfully:

- The acceptance loop is enforced by the engine, not by discipline: what
  passed verification is byte-for-byte what is integrated.
- The board is read-only by construction and shows a number only when
  the engine recorded it. A cost that is not known is said to be not
  known; an estimate is never presented as a measurement; a sentence
  about speed or money is made only from comparable finished runs.
- The two levels (plain and technical) are built from one snapshot, so
  the technical reader and the owner never see two different truths.

## Operating Context

- The lead is the main ZCode session. Teammates and verifiers are
  subagents. All state lives in SQLite under `.agent-teams/<team>/` in
  the project; no database server, no Docker, no network.
- A task in plain words: the lead fills a brief with the owner, the
  engine asks one question at a time, shows the plan, and creates the
  team only after the owner's confirmation (`team.py plan --brief`,
  `team.py init --brief`).
- The board: `team.py ui --team <id>` opens a local page at
  127.0.0.1 while the team works. Started with the lead's authority, the
  owner can act from it: pause and resume, allow one more attempt of a
  stage, cancel a stage, ask the lead for a check, integrate, apply,
  raise the budget; download the report.
- The report: one self-contained HTML file with twelve sections, fit to
  show to a manager or a customer; it opens after the engine has
  finished.
- A comparison of teams exists only for teams that declare one benchmark
  set; real campaigns of 1, 2, 3 and 5 executors have not been run.
- A session with five non-technical people is the owner's acceptance of
  understandability; its protocol and a demo kit are in the repository.

## Capabilities and Constraints

Confirmed functionality: the brief; the board (state, progress, what
happens now, specialists, plan as list and graph, timeline, activity
feed, quality, spend against the budget, problems and how each ended,
the integration, the comparison of declared teams); control from the
board with a confirmation where there are consequences; the report for
a manager; the budget enforced by the engine.

Technical constraints future work must keep:

- Python 3.10 and newer, standard library only; no framework, no build
  step; the page is one HTML file, one stylesheet and one script.
- Works offline: the page loads nothing from the network, no web font,
  no icon font, no CDN, no analytics.
- The page's Content Security Policy allows no inline script and no
  inline style: bars and widths go through the page's style object.
- The board binds to loopback and has no authentication: it is for the
  owner's own machine. The lead token never reaches a page, a response
  or a log; the server keeps it in memory.
- Windows, macOS and Linux; CI on all three.
- Every text takes its endings from its number.

Terminology (the owner's glossary for the plain level; the board's
words are Russian today):

| Engine term | Reader's word |
|---|---|
| Agent | AI-специалист |
| Task | Задача (on the board: a stage, «этап») |
| DAG | План работы, связи между задачами |
| Run | Запуск команды |
| Workspace | Рабочая область |
| Commit SHA | Версия результата |
| Submission | Переданный результат |
| Verification | Проверка качества |
| Quality Gate | Контроль качества |
| Integration | Объединение результатов |
| Apply | Применение изменений |
| Heartbeat | Последняя активность |
| Recovery | Восстановление работы |
| Quarantine | Сохранённая рабочая область после сбоя |
| Tokens | Объём использования AI |
| Cost | Затраты |

No technical term in the main cards, notices, buttons and headings of
the plain level; switching the level changes no team state and is
remembered between visits.

Explicitly undecided or pending (record, do not invent):

- **Language.** The owner decided on 2026-11-26 that the primary
  language of the product is English. The board and the report are in
  Russian today. Moving their texts into English is pending work with
  no date; it was kept out of the first polish pass by the owner's
  choice ("the look, not the texts").
- **Currency.** Spend is shown in US dollars, the currency of the price
  table. A currency of the reader's choice is undecided.
- **The session with five people** has not been run. Until it is, the
  board's understandability for non-technical people is not a
  demonstrated fact.
- **Four readings of the specification** the owner has not confirmed:
  the "start screen" as a conversation with the lead session; a plan
  with stages and no subtasks; "approve the final result" as the apply
  step; a line diff only in the command line.
- **Version 2.1.0** waits for the session with people; the releases so
  far are 2.1.0a1 to 2.1.0a5.

## Brand Commitments

- Name: ZCode Agent Teams (plugin `agent-teams`, repository
  `zcode-agent-teams`).
- Voice: plain words for a person who is not technical; whole
  sentences that say what a number counts. No long dashes in texts of
  the board or the report (the owner's standing rule), no clichés.
- The current look of the board is kept: the owner asked for a polish
  of every part, not for a redesign.
- Primary language: English (owner, 2026-11-26).

## Evidence on Hand

- The owner's specification of v2.1 (25 sections) in the owner's
  private notes; `SPEC.md` in the repository is the contract of the
  engine; `DESIGN.md` is its visual system.
- A demo team in every state a reader can meet, built by
  `examples/usability-demo/prepare.py` without a model or a network;
  the protocol of a session in `docs/usability-protocol.md`.
- A real run of the engine on its own development (2026-11-25): two
  executors and two independent verifiers through the engine, accepted
  and integrated.
- Measured on a hundred stages and twenty specialists (2026-11-26): the
  first picture of the board in 394 to 515 ms in a real browser, a
  change on screen about 250 ms after the poll that saw it; 1321
  automated tests.
- Measured contrast of the palette (recorded in `DESIGN.md`): since
  the polish pass of 2026-11-27 every text keeps 4.5:1 or more in both
  themes, counted with the WCAG formula and counted again on the built
  page, on the demo team and on a team of a hundred stages.

Absences future work must not fabricate: no results of a session with
real people; no benchmark campaign with real runs of 1, 2, 3 and 5
executors; no customer quotes, testimonials or case studies; no usage
numbers from other users.

## Product Principles

1. The user manages the work and the result, not the technical
   machinery.
2. Only what the engine recorded is shown. An unknown is said to be
   unknown; nothing is estimated, rounded up or invented to look
   complete.
3. Start from the reader who is not technical. The technical reader gets
   a second level, never the first word.
4. An action with consequences asks first; the board changes nothing by
   itself and always asks the engine.
5. It runs on the owner's machine, offline, with no secret in the page.

## Accessibility & Inclusion

The owner set the bar on 2026-11-26: WCAG AA for the board.

- Text contrast of at least 4.5:1 in both themes (light and dark).
- A visible focus from the keyboard; every control reachable by
  keyboard.
- Touch targets of at least 32 to 36 px on a phone.
- Texts in plain language for readers without a technical background is
  a product requirement since the specification (see Terminology).
- The contrast gaps the palette had were closed in the polish pass of
  2026-11-27. Not checked yet by anyone: a reader with a screen reader,
  and a phone in the hand (the board is reached at 127.0.0.1, so a
  phone reaches it only through a forwarded port).
