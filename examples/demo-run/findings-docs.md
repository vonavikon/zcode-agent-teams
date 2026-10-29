# lens-docs: ревью документации (SKILL.md + README.md)

Задача lens-docs, круг 2. Линза: сможет ли чужой агент (и человек) запустить команду по SKILL.md и README.md без дополнительных пояснений.

Метод: каждая команда и путь из SKILL.md и README.md сверены с фактическими файлами репозитория и argparse-парсером team.py; команды `read`, `claim`, `status` выполнены вживую против команды demo-review. После круга 1 лид переписал в team.py экспортёр Langfuse и обновил SKILL.md:76 — оба файла перечитаны, все ссылки отчёта сверены с текущими версиями (в круге 1 часть ссылок указывала на старую версию team.py, исправлено).

## Что проверено и сходится

- Все 7 команд из SKILL.md:50-55 (`init`, `claim`, `verify`, `message`, `read`, `status`, `langfuse`) существуют в team.py:319-364, флаги совпадают, `--verdict` ограничен `pass|fail` (team.py:335), `--feedback` при fail обязателен (team.py:150-151).
- SKILL.md:57 «все команды принимают `--project-root` (по умолчанию `.`)» — подтверждено: флаг объявлен во всех семи сабпарсерах (team.py:322, 329, 338, 346, 352, 357, 363).
- Пути из SKILL.md:22-23 и README:47-56: `plugins/agent-teams/.zcode-plugin/plugin.json`, `skills/agent-teams/SKILL.md`, `skills/agent-teams/references/prompt-teammate.md`, `skills/agent-teams/references/prompt-verifier.md`, `skills/agent-teams/scripts/team.py`, `marketplace.json`, `examples/tasks-review.json` — все существуют.
- README.md:54 «пример задач (ревью в 3 линзы)» — в examples/tasks-review.json действительно 3 задачи (lens-code, lens-robust, lens-docs).
- README.md:16 «Personal → zcode-agent-teams → agent-teams → Install» — имена совпадают: маркетплейс `zcode-agent-teams` (marketplace.json:2), плагин `agent-teams` (marketplace.json:5), скилл `agent-teams` (SKILL.md:2).
- Структура каталога команды: `.agent-teams/<team>/tasks.json`, `locks/<task-id>.lock` (O_EXCL, team.py:122-124), `inbox/<agent>.json` (team.py:180) — соответствует SKILL.md:14-15 и README.md:7-8.
- SKILL.md:17 и README.md:10: `langfuse` шлёт trace на команду (корневой span с атрибутом `langfuse.internal.is_app_root`, team.py:266-279), span на задачу (team.py:280-295) и score на каждый вердикт (team.py:300-307).
- SKILL.md:76 про эндпоинты Langfuse — соответствует коду: трейсы через `/api/public/otel/v1/traces` (team.py:296), scores через `/api/public/scores` (team.py:302).
- SKILL.md:70 «init не проверяет пересечение files_write» — соответствует коду: cmd_init (team.py:70-106) такой проверки не делает.
- SKILL.md:75 «trace-id строго 32 hex, team.py уже делает hex32» — соответствует team.py:31-33.
- SKILL.md:77 «mailbox — read-modify-write без локов» — соответствует team.py:163-170, 180-186.
- «stdlib-only» для team.py — верно: импорты только из стандартной библиотеки (team.py:11-22).
- Фидбек при FAIL автоматически кладётся в mailbox исполнителя (SKILL.md:26, README.md:9) — соответствует team.py:162-170.

## CRITICAL

Нет. Все команды и пути из быстрого старта SKILL.md:19-27 воспроизводимы: init/claim/verify/message/read/status/langfuse соответствуют фактическому CLI, файлы промптов для спавна тиммейтов и верификатора на месте.

## MAJOR

### M1. SKILL.md:26 — «Максимум 2 круга, дальше задачу забирает лид»: механизма забора нет

После второго FAIL лид, следующий инструкции, попадает в тупик. `verify --verdict fail` не меняет статус задачи (team.py:157-172, статус остаётся `in_progress`), а `claim` принимает задачу только в статусе `pending` (team.py:116-117: `if t["status"] != "pending"`). Команды сброса статуса или переназначения исполнителя в team.py нет. Иными словами, «забрать задачу» нечем: ни одна команда из SKILL.md:50-55 этого не делает.

Предложение правки: заменить в SKILL.md:26 хвост «Максимум 2 круга, дальше задачу забирает лид» на конкретное действие, например: «Максимум 2 круга; дальше лид правит `.agent-teams/<team>/tasks.json` вручную (status → `pending`, при необходимости удалить `locks/<task-id>.lock`) и забирает задачу сам, либо закрывает её verify pass с оговоркой в фидбеке». Альтернатива — добавить в team.py команду `reset`/`reassign` и описать её в списке команд SKILL.md:50-55.

## MINOR

### m1. SKILL.md:27 и README.md:18 — LANGFUSE_HOST заявлена обязательной, в коде она опциональна

Оба места пишут «нужны env `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`». В коде выход происходит только при отсутствии двух ключей (team.py:259-260), а `LANGFUSE_HOST` имеет дефолт `http://127.0.0.1:3100` (team.py:258). Агент, следующий доке, будет считать отсутствие HOST ошибкой конфигурации, хотя прогон на localhost с дефолтом сработает.

Предложение правки: в обоих местах сформулировать «нужны `LANGFUSE_PUBLIC_KEY` и `LANGFUSE_SECRET_KEY`; `LANGFUSE_HOST` опционален, по умолчанию `http://127.0.0.1:3100`».

### m2. README.md:55 — examples/demo-run/ заявлен в структуре, каталога в репозитории нет

Дерево в «Структура» перечисляет `examples/demo-run/   артефакты первого прогона`, но каталога не существует (проверено обходом файлов репозитория). Для свежего клона путь битый. На момент этого ревью прогон как раз создаёт файлы findings-*.md в этом каталоге.

Предложение правки: закоммитить артефакты прогона (findings-code.md, findings-robust.md, findings-docs.md) после завершения демо — тогда строка станет верной. Если артефакты коммитить не планируют — убрать строку из README.md:55.

### m3. README.md:58-60 — раздел «Лицензия» объявляет MIT, файла LICENSE нет

Раздел «## Лицензия / MIT» заявляет лицензию, но файла LICENSE (или COPYING) в репозитории нет — проверено обходом. Для плагина, попадающего на публичный маркетплейс, текст лицензии нужен: без него заявление «MIT» не даёт прав на распространение.

Предложение правки: добавить в корень файл LICENSE с текстом MIT (Copyright (c) 2026 vonavikon) и, при желании, поле `"license": "MIT"` в marketplace.json и plugin.json. Либо убрать раздел, если лицензирование пока не решено.

### m4. README.md:7-8 — в «Что внутри» не перечислены locks/ и events.jsonl

Раздел описывает `tasks.json` и `inbox/`, но движок создаёт в том же каталоге ещё `locks/<task-id>.lock` и `events.jsonl` (team.py:53-58, 101-102). Лид, заглянувший в каталог команды по README, увидит неописанные файлы. В SKILL.md:14 locks/ упомянута, events.jsonl не упомянута нигде в доках.

Предложение правки: добавить в README.md:7-8 строку вида: «`locks/` — блокировки claim (по файлу на задачу), `events.jsonl` — журнал событий, источник отчёта в Langfuse».

### m5. README.md:18 — «используется только ingestion» расходится с кодом и SKILL.md:76

README.md:18: «режим events_only достаточно — используется только ingestion». Вызова `/api/public/ingestion` в team.py нет: трейсы уходят OTLP-эндпоинтом `/api/public/otel/v1/traces` (team.py:296), score — отдельным POST `/api/public/scores` (team.py:302). SKILL.md:76 после правки лида описывает эндпоинты верно. Читатель README будет настраивать ingestion, который код не использует.

Предложение правки: заменить в README.md:18 скобку на «self-hosted тоже поддерживается, режима events_only достаточно — трейсы идут через `/api/public/otel/v1/traces`, score через `/api/public/scores`», синхронно со SKILL.md:76.
