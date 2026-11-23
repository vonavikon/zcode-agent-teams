# agent-teams: команды агентов для ZCode

Перенос подхода [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) на механику ZCode: несколько агентов работают над одной кодовой базой с общим списком задач, приёмка встроена в цикл.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) · **Русский** · [中文](README.zh.md) · [Español](README.es.md) · [Français](README.fr.md)

## Что внутри

- **Общий список задач**: `.agent-teams/<team>/tasks.json` хранит статусы, зависимости и наборы файлов. Все мутации идут под единым локом состояния и записываются атомарно (с v0.2.0)
- **Mailbox-сообщения**: агенты пишут друг другу напрямую, минуя лида. Файлы: `.agent-teams/<team>/inbox/<agent>.json`
- **Журнал и блокировки**: `events.jsonl` фиксирует каждый переход статуса (это источник Langfuse-отчёта). Каталог `locks/` держит лок состояния (O_EXCL; протухшие подбираются автоматически)
- **Лимит кругов**: задача, которая раз за разом проваливает приёмку, уходит в `blocked` и возвращается лиду через `release`. В v0.2.x блокировка происходит на третьем провале приёмки (см. [BACKLOG.md](BACKLOG.md))
- **Гейт приёмки (цикл exit-2)**: исполнитель не закрывает задачу сам. Верификатор-субагент проверяет каждый критерий по фактическим файлам. FAIL кладёт фидбек в mailbox исполнителя, работа продолжается
- **Langfuse-аналитика**: `team.py langfuse` выгружает прогон: trace на команду, span на задачу, score на каждый вердикт. Круги до прохождения, время по линзам и стоимость видны в UI

Роли: лид это основная сессия ZCode, она планирует, порождает и сводит; тиммейты и верификаторы это субагенты, верификаторы работают только на чтение.

## Установка

ZCode → Plugin Marketplace → Add → Add Plugin Marketplace → указать каталог этого репозитория (marketplace.json в корне). Затем Personal → zcode-agent-teams → agent-teams → Install.

Скилл появится в списке как `agent-teams`. Для Langfuse-отчёта нужны переменные окружения `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` и `LANGFUSE_HOST`. Self-hosted поддерживается: трейсы пишутся в OTLP-эндпоинт `/api/public/otel/v1/traces`, scores в `/api/public/scores`, поэтому режима events_only достаточно.

## Схема работы

```
lead: tasks.json (непересекающиеся files_write, acceptance-критерии)
  └─ team.py init
  └─ spawn тиммейтов (Agent tool, background)      ← 2-5 агентов
       └─ тиммейт: claim → работа → "VERIFICATION REQUEST"
            └─ лид spawn верификатора (read-only)
                 ├─ PASS → team.py verify --verdict pass
                 └─ FAIL → verify --verdict fail --feedback "..."
                      └─ фидбек в mailbox → SendMessage исполнителю → исправление
  └─ team.py status → все completed
  └─ team.py langfuse → trace + spans + scores
```

## Рецепты

- **Ревью в N линз**: security, performance и coverage над одним объектом, у каждой линзы свой выходной файл
- **Competing hypotheses**: N независимых следователей одного бага, входы не пересекаются, спор вынесен в отдельную задачу. Защита от якорения на первой гипотезе
- **Параллельная реализация по слоям**: сплит на непересекающиеся файлы, `deps` задаёт порядок волн

## Когда команда не нужна

Последовательные задачи, правки одного файла, много зависимостей: одна сессия или один субагент дешевле. Токены растут линейно на каждого тиммейта.

## Структура

```
plugins/agent-teams/
  .zcode-plugin/plugin.json          plugin manifest
  skills/agent-teams/SKILL.md         lead playbook
  skills/agent-teams/references/      teammate and verifier prompts
  skills/agent-teams/scripts/team.py  engine (stdlib-only, Python 3.10+)
marketplace.json                     installation catalog
examples/tasks-review.json           task example (review in 3 lenses)
examples/demo-run/                   artifacts of the first run
```

## Лицензия

MIT
