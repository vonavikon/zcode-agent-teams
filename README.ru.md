# agent-teams: команды агентов для ZCode

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) · **Русский** · [中文](README.zh.md) · [Español](README.es.md) · [Français](README.fr.md)

Перенос подхода [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) на механику ZCode, в котором цикл приёмки контролирует движок, а не дисциплина агентов: несколько агентов работают в изолированных git-worktree над одной кодовой базой, и каждый результат проходит машинную проверку перед интеграцией.

## Что внутри

- **Машина состояний и SQLite-состояние**: каждая смена статуса задачи это защищённый переход с hash-chain в `state.db`; потери обновлений нет, полная история аудита, автоматические бэкапы и миграции
- **Изолированные рабочие области**: на каждую задачу свой git worktree и ветка, base commit записан, незакоммиченная работа переживает краши и release
- **Контроль записей**: фактический diff сверяется с `files_write` задачи; запись вне множества валит гейт
- **Неизменяемое исполнение**: submit требует чистый worktree, закрепляет неизменяемый снимок (git ref + SQLite), и проверка идёт по снимку; принято то, что байт-в-байт интегрируется
- **Машинный гейт приёмки**: критерии с командой проверки исполняет сам движок; провал обязательного критерия перекрывает вердикт PASS. PASS дополнительно требует структурный отчёт верификации (или аудируемый override лида). Исполнитель не подтверждает свою работу сам: вердиктам нужен lead-токен, посылкам execution-токен
- **Интеграционный гейт и apply**: принятые SHA мержатся по DAG в интеграционную ветку с опциональной проверкой; apply отдельным шагом быстрого слияния, отказ при грязном проекте или сдвинутой базе
- **Восстановление и fencing**: таймауты lease, heartbeat и всей попытки; потерянные попытки изолируются (мёртвый токен, worktree уходит в quarantine и сохраняется) и возвращаются в очередь в рамках лимитов
- **Наблюдаемость**: токены из нотификаций субагентов, стоимость с явным флагом достоверности, метрики прогона включая сравнение «команда против одного агента», самодостаточный HTML-отчёт и идемпотентный экспорт в Langfuse

Роли: лид это основная сессия ZCode; тиммейты и верификаторы это субагенты (профили ролей лежат в `agents/`).

## Установка

ZCode → Plugin Marketplace → Add → Add Plugin Marketplace → указать каталог этого репозитория (marketplace.json в корне). Затем Personal → zcode-agent-teams → agent-teams → Install.

Скилл появится как `agent-teams`. Движку нужен Python 3.10+ и ничего больше (только stdlib). Экспорт в Langfuse дополнительно требует `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` и опционально `LANGFUSE_HOST` (self-hosted поддерживается; трейсы идут в OTLP-эндпоинт, режима events_only достаточно).

## Схема работы

```
lead: tasks.json (validated: DAG, paths, write overlaps)
  └─ team.py init                     prints the LEAD TOKEN once
  └─ spawn teammates (Agent tool, background)      ← 2-5 agents
       └─ teammate: claim (execution token, worktree) → work → submit
            └─ lead spawns a verifier (read-only) on each VERIFICATION REQUEST
                 ├─ engine runs command-backed criteria itself
                 ├─ PASS → team.py verify --verdict pass --lead-token ...
                 └─ FAIL → verify --verdict fail --feedback "..." → executor fixes
                      (2nd FAIL by default → blocked → release or handle)
  └─ team.py integrate --lead-token ... [--check CMD] [--apply]
  └─ team.py status → all completed; report --out run.html; metrics; langfuse

```

## Рецепты

- **Ревью в N линз**: security, performance и coverage над одним объектом, у каждой линзы свой выходной файл
- **Competing hypotheses**: N независимых следователей одного бага, спор вынесен в отдельную задачу. Защита от якорения на первой гипотезе
- **Параллельная реализация по слоям**: сплит на непересекающиеся файлы, `deps` задаёт порядок волн, интеграционный гейт сводит результат

## Когда команда не нужна

Последовательные задачи, правки одного файла, много зависимостей: одна сессия или один субагент дешевле. Токены растут линейно на каждого тиммейта; фактический выигрыш смотрите в `metrics`.

## Структура

```
plugins/agent-teams/
  .zcode-plugin/plugin.json          manifest (skills + agents)
  agents/                            executor and verifier role profiles
  skills/agent-teams/SKILL.md        the lead playbook
  skills/agent-teams/references/     teammate and verifier prompts
  skills/agent-teams/scripts/
    team.py                          engine CLI (v1.0)
    team_v02.py                      legacy v0.2 engine for old teams
    agent_teams/                     the package (engine, storage, locks,
                                     validation, workspaces, quality,
                                     observability, report, langfuse)
docs/                                architecture, state machine, security,
                                     workspace isolation, verification,
                                     observability, cost model, testing,
                                     troubleshooting
schemas/tasks.schema.json            JSON Schema for the task spec
tests/                               unit, security, concurrency (real
                                     processes), integration (scenarios A-F)
marketplace.json                     installation catalog
examples/                            task examples and run artifacts

```

## Совместимость

Команды, созданные движком v0.2 (JSON-файлы без `state.db`), продолжают работать через `team_v02.py`. Новые команды используют `team.py` и SQLite-движок. Зеркала v0.2 (`tasks.json`, `inbox/`, `events.jsonl`) по-прежнему пишутся при каждой мутации.

## Лицензия

MIT
