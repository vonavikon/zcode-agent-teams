---
name: agent-teams
description: Команды агентов в ZCode — параллельные исполнители с непересекающимися файлами, гейт приёмки через верификаторов (цикл exit-2), mailbox-сообщения между агентами, отчёт прогона в Langfuse. Использовать, когда просят «командой агентов», «agent teams», «волны исполнитель + верификатор», «ревью в N линз», «competing hypotheses», «параллельная реализация по слоям».
---

# Agent Teams для ZCode

Портирование подхода Claude Code Agent Teams на механику ZCode. Лид — основная сессия (ты), тиммейты — субагенты через Agent tool (`run_in_background: true`), координация через файлы на диске, приёмка — верификаторы.

Соответствие механик:

| Claude Code | Здесь |
|---|---|
| общий список задач + file locking на claim | `.agent-teams/<team>/tasks.json` + лок-файлы `locks/` (O_EXCL) |
| mailbox-сообщения между агентами | `.agent-teams/<team>/inbox/<agent>.json` |
| хук `TaskCompleted` с exit 2 («не принято, продолжай») | верификатор-субагент + `team.py verify --verdict fail --feedback ...` |
| trace команды | `team.py langfuse` → trace + span на задачу + score на каждый вердикт |

## Быстрый старт

1. **Опиши задачи** — JSON-файл (см. схему ниже). Главные правила: у задач непересекающиеся `files_write` (два агента в одном файле = перезаписи), у каждой — проверяемые `acceptance`-критерии. Ориентиры: 2–5 тиммейтов, 1–3 задачи на тиммейта.
2. **init**: `python <skill>/scripts/team.py init --team <id> --tasks tasks.json --project-root <root>`
3. **Породи тиммейтов**: Agent tool, по одному на тиммейта, `run_in_background: true`, промпт — из `references/prompt-teammate.md`, подставь абсолютные пути к `team.py`, project root, каталогу команды и id задач тиммейта.
4. **Гейт приёмки**: тиммейт в финальном сообщении пишет `VERIFICATION REQUEST <task-id>` + резюме по критериям. На каждый запрос породи верификатора (промпт `references/prompt-verifier.md`, только чтение). Вердикт:
   - PASS → `team.py verify --team <id> --task <tid> --verdict pass`
   - FAIL → `team.py verify ... --verdict fail --feedback "<что именно не принято>"` — фидбек сам упадёт исполнителю в mailbox, затем SendMessage агенту-исполнителю: «прочитай mailbox, исправь, снова запроси верификацию». Максимум 2 круга, дальше лид останавливает задачу и разбирает сам (команды reset/reassign в движке нет — см. BACKLOG репозитория).
5. **Финал**: `python <skill>/scripts/team.py status --team <id>` — убедись, что все `completed`. Затем `team.py langfuse --team <id> --name "<имя прогона>"` (нужны env `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`). Сводку и trace-ссылку — владельцу.

## Схема задач

```json
{
  "tasks": [
    {
      "id": "lens-security",
      "title": "ревью security-линзой",
      "assignee": "sec-lens",
      "lens": "security",
      "files_read": ["src/auth.py"],
      "files_write": ["reports/security.md"],
      "acceptance": ["каждый finding с файл:строка", "нет нерелевантных findings"],
      "deps": []
    }
  ]
}
```

## Команды team.py

- `init --team <id> --tasks <file>` — создать команду
- `claim --team <id> --task <tid> --by <agent>` — забрать задачу (лок против гонок, зависимости проверяются)
- `verify --team <id> --task <tid> --verdict pass|fail [--feedback ...]` — вердикт гейта; fail автоматически кладёт фидбек в mailbox исполнителя
- `message --team <id> --to <agent> --by <agent> --text "..."` / `read --team <id> --for <agent>` — mailbox
- `status --team <id>` — статусы, круги, непрочитанные
- `langfuse --team <id> [--name ...]` — выгрузить прогон (trace + span на задачу + score на каждый вердикт)

Все команды принимают `--project-root` (по умолчанию `.`) — тиммейтам передавай абсолютный путь.

## Рецепты

- **Ревью в N линз**: один объект, у каждой линзы свой выходной файл. Верификатор проверяет, что findings доказательны (файл:строка существуют и подтверждаются).
- **Competing hypotheses**: N следователей одной проблемы, у каждого своя гипотеза в `assignee`, входы независимы, спор — отдельная задача по чтению всех отчётов. Против якорения на первую гипотезу.
- **Параллельная реализация**: сплит по слоям/модулям, у задач непересекающиеся `files_write`, `deps` для порядка волн.

## Правила лида

- Лид не реализует сам — он раздаёт, верифицирует и сводит. Начал писать код за тиммейта — остановись и переспawnь.
- Пока команда работает, не меняй файлы из `files_read` задач: ревьюеры сверяют строки по текущему файлу, твой сдвиг строк ломает их ссылки. Первый прогон поймал ровно это (см. examples/demo-run).
- Первый прогон новой конфигурации — на research/ревью, не на параллельном написании кода.
- Последовательные задачи, правки одного файла, куча зависимостей — команда не нужна, работай сам или одним субагентом.
- Пересечение `files_write` у двух задач запрещено — init не проверяет это за тебя, проверяй при составлении tasks.json.

## Грабли

- Windows/Git Bash: передавай абсолютные пути в `--project-root`, не полагайся на cwd субагента.
- trace-id в Langfuse строго 32 hex — `team.py` уже делает `hex32`, не выдумывай свой формат.
- Self-hosted Langfuse v4 в режиме events_only: legacy `/api/public/ingestion` принимает только score-события. `team.py langfuse` шлёт трейсы через OTLP-эндпоинт `/api/public/otel/v1/traces`, scores — через `/api/public/scores`. Чтение REST выпилено — трейс смотреть в UI или через langfuse-MCP.
- mailbox — read-modify-write без локов: не давай двум агентам одновременно писать в один ящик, шлёт обычно лид.
- Токены: у каждого тиммейта свой полный контекст, команда линейно дороже одной сессии — держи задачи крупными, а команду из 2–5 агентов.
