# agent-teams — команды агентов для ZCode

Портирование подхода [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) на механику ZCode: несколько агентов работают над одной кодовой базой с общим списком задач, а приёмка встроена в цикл.

## Что внутри

- **Общий список задач** — `.agent-teams/<team>/tasks.json`: статусы, зависимости, наборы файлов. Claim задачи с блокировкой (два агента не заберут одну задачу)
- **Mailbox-сообщения** — агенты пишут друг другу напрямую, минуя лида: `.agent-teams/<team>/inbox/<agent>.json`
- **Журнал и блокировки** — `events.jsonl` фиксирует каждый переход статуса (источник Langfuse-отчёта), `locks/` держит O_EXCL-блокировки claim
- **Гейт приёмки (цикл exit-2)** — исполнитель не закрывает задачу сам. Верификатор-субагент проверяет каждый критерий по фактическим файлам; FAIL = фидбек в mailbox исполнителя, работа продолжается
- **Langfuse-аналитика** — `team.py langfuse` выгружает прогон: trace на команду, span на задачу, score на каждый вердикт. Круги до прохождения, время по линзам, стоимость видна в UI

Роли: лид — основная сессия ZCode (планирует, порождает, сводит), тиммейты — субагенты, верификаторы — субагенты только-для-чтения.

## Установка

ZCode → Plugin Marketplace → Add → Add Plugin Marketplace → указать каталог этого репозитория (marketplace.json в корне). Затем Personal → zcode-agent-teams → agent-teams → Install.

Скилл появится в списке как `agent-teams`. Для Langfuse-отчёта нужны переменные окружения `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (self-hosted поддерживается; трейсы пишутся OTLP-эндпоинтом `/api/public/otel/v1/traces`, scores — `/api/public/scores`, поэтому режима events_only достаточно).

## Схема работы

```
лид: tasks.json (непересекающиеся files_write, acceptance-критерии)
  └─ team.py init
  └─ spawn тиммейтов (Agent tool, background)      ← 2–5 агентов
       └─ тиммейт: claim → работа → "VERIFICATION REQUEST"
            └─ лид spawn верификатора (read-only)
                 ├─ PASS → team.py verify --verdict pass
                 └─ FAIL → verify --verdict fail --feedback "..."
                      └─ фидбек в mailbox → SendMessage исполнителю → исправление
  └─ team.py status → все completed
  └─ team.py langfuse → trace + spans + scores
```

## Рецепты

- **Ревью в N линз** — security / производительность / покрытие над одним объектом, у каждой линзы свой выходной файл
- **Competing hypotheses** — N независимых следователей одного бага, входы не пересекаются, спор — отдельной задачей. Против якорения на первую гипотезу
- **Параллельная реализация по слоям** — сплит на непересекающиеся файлы, `deps` задают порядок волн

## Когда команда не нужна

Последовательные задачи, правки одного файла, много зависимостей — одна сессия или один субагент дешевле. Токены растут линейно на каждого тиммейта.

## Структура

```
plugins/agent-teams/
  .zcode-plugin/plugin.json          манифест плагина
  skills/agent-teams/SKILL.md        плейбук лида
  skills/agent-teams/references/     промпты тиммейта и верификатора
  skills/agent-teams/scripts/team.py движок (stdlib-only, Python 3.10+)
marketplace.json                     каталог для установки
examples/tasks-review.json           пример задач (ревью в 3 линзы)
examples/demo-run/                   артефакты первого прогона
```

## Лицензия

MIT
