# Аудит консистентности документации и манифестов v0.2.0 (audit-docs)

Команда v020-verify, исполнитель aud-docs, 2026-10-30. Линза: docs.

Эталон — текущий `plugins/agent-teams/skills/agent-teams/scripts/team.py` (483 строки, v0.2.0). Сверялись: `plugins/agent-teams/skills/agent-teams/SKILL.md`, `README.md`, `BACKLOG.md`, `plugins/agent-teams/.zcode-plugin/plugin.json`, `marketplace.json`.

## Пункт 1. Версия 0.2.0 в plugin.json и marketplace.json — OK

- plugin.json:3 — `"version": "0.2.0"`
- marketplace.json:7 — `"version": "0.2.0"`

Косвенные упоминания согласованы: team.py:11 («Конкурентность v0.2.0»), README:7 («(v0.2.0)»), BACKLOG:5 («Сделано в v0.2.0»).

## Пункт 2. Команды SKILL.md против team.py — OK

Все девять команд существуют, флаги совпадают. `--team` и `--project-root` добавляются всем субкомандам общим хелпером (team.py:429-433), что соответствует заявлению SKILL.md:59 «Все команды принимают --project-root».

| Команда | SKILL.md | Парсер в team.py | Флаги сверх --team/--project-root |
|---|---|---|---|
| init | :50 | 435-437 | --tasks (436) |
| claim | :51 | 439-442 | --task (440), --by (441) |
| verify | :52 | 444-449 | --task (445), --verdict pass\|fail (446), --feedback (447), --by default verifier (448) |
| release | :53 | 451-454 | --task (452), --by default lead (453) |
| tokens | :54 | 456-460 | --task (457), --add int (458), --by (459) |
| message | :55 | 462-466 | --to (463), --by (464), --text (465) |
| read | :55 | 468-470 | --for → for_ (469) |
| status | :56 | 472 | — |
| langfuse | :57 | 474-476 | --name (475) |

Семантика в описаниях SKILL.md совпадает с кодом: verify fail требует --feedback (team.py:205-206), fail кладёт фидбек в mailbox исполнителя (230-234), после 2 провалов задача уходит в blocked с ROUND LIMIT (213-224); release возвращает в pending (249); tokens виден в status (311-312) и в langfuse (385, 401).

## Пункт 3. README «Что внутри» (строки 5-14) — OK

Все четыре механики из чеклиста подтверждены кодом:

- Лок состояния: README:7 («Все мутации — под единым локом состояния с атомарной записью») ↔ team.py:79-111 (StateLock); применён в claim (178), verify (200), release (244), tokens (263), message (279), read (294); атомарная запись — write_atomic (69-72).
- Blocked: README:10 («после двух провалов приёмки задача уходит в blocked») ↔ team.py:30 (MAX_ROUNDS = 2), 213-224 (round_limit → blocked + сообщение ROUND LIMIT в mailbox исполнителю).
- Release: README:10 («возвращается лиду через release») ↔ team.py:240-252 (возврат в pending, запрещён только для completed — 247-248).
- Tokens: README:12 («стоимость видна в UI») ↔ team.py:255-269 (cmd_tokens), 385 (tokens_total в метаданных trace), 401 (tokens в span). README команду tokens явно не называет, противоречия нет.

Остальное из блока тоже сходится: mailbox README:8 ↔ team.py:129-133; locks/O_EXCL/подбор протухших README:9 ↔ team.py:83, 90, 32 (LOCK_STALE_S = 7200), 96-99; events.jsonl README:9 ↔ log_event (114-119) — каждый переход статуса журналируется (task_claimed 190, verify_pass 210, verify_fail 228, round_limit 217, task_released 251); гейт exit-2 README:11 ↔ team.py:205-206, 230-234; langfuse README:12 ↔ team.py:374-419.

## Пункт 4. BACKLOG «Сделано в v0.2.0» (строки 7-13) — OK

Все семь пунктов подтверждены кодом, обещаний сверх кода нет:

| BACKLOG | Обещание | Подтверждение в team.py |
|---|---|---|
| :7 | единый лок .state.lock (O_EXCL, ретраи, протухший >2 ч) на все мутации, tmp pid+uuid | 79-111 (StateLock), 32 (LOCK_STALE_S), 69-72 (write_atomic), 58-66 (replace_retry) |
| :8 | валидация имён regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` | 33 (NAME_RE), 45-47 (check_name); вызовы: 51, 140, 149, 176, 198, 242, 276-277 |
| :9 | release возвращает в pending | 249 |
| :10 | ретрай PermissionError на replace | 58-66 (сон 0.2*(i+1), 5 попыток) |
| :11 | битые строки events.jsonl не роняют langfuse | 361-369 (JSONDecodeError → счётчик bad) |
| :12 | лимит кругов: blocked + ROUND LIMIT в mailbox | 213-224 |
| :13 | tokens --add, счётчики в tasks.json/status, суммарные в trace и span | 456-460, 159, 261, 311-312, 385, 401 |

Оговорка к строке 7 — расхождение 5 ниже: чтение в cmd_tokens идёт вне лока.

## Расхождения сверх чеклиста

1. **SKILL.md:81 — устаревшее описание mailbox (прямое противоречие коду).** «mailbox — read-modify-write без локов: не давай двум агентам одновременно писать в один ящик». В v0.2.0 все mailbox-мутации идут под StateLock: message (team.py:279), read (294), записи из verify (218, 230 — внутри блока `with StateLock` от строки 200). BACKLOG:7 и README:7 описывают механику верно. Остаток текста v0.1 — единственное место, где документация утверждает то, что код уже не делает.

2. **SKILL.md:59 — «движок отклоняет остальные» имена: обещано больше, чем проверяется.** (а) Regex требует буквенно-цифровой первый символ, «.»/«-»/«_» в начале не проходят (team.py:33) — SKILL.md об этом умалчивает. (б) `read --for` и `tokens --by` не валидируются вовсе: cmd_read строит путь `d / "inbox" / f"{args.for_}.json"` без check_name (team.py:287-290), cmd_tokens не проверяет args.by (255-263). Для этих двух флагов заявление SKILL.md:59 не выполняется. Стык с аудитом aud-paths.

3. **SKILL.md:14 — таблица соответствий описывает механику v0.1.** Строка «общий список задач + file locking на claim → `.agent-teams/<team>/tasks.json` + лок-файлы `locks/` (O_EXCL)». В v0.2.0 locks/ держит один лок состояния .state.lock (team.py:83), сериализующий все мутации, а не локи на claim. README:9 сформулировано правильно. Неточность формулировки, не функциональная.

4. **SKILL.md:26 и :52 — ссылка «см. BACKLOG репозитория» ведёт в пустоту.** Команд reset/reassign в движке действительно нет (парсер team.py:425-476 — девять субкоманд, перечислены в пункте 2). Но BACKLOG не содержит пункта про reset/reassign ни в «Мелком (открыто)», ни в «Дистрибуции».

5. **team.py:258 — чтение tasks.json в cmd_tokens вне лока.** Под локом только запись (263-264): между чтением (258) и записью конкурентный verify может потерять обновление (lost update). Формально README:7 «все мутации — под единым локом» верно про записи, но защита read-modify-write в tokens неполная. Стык с аудитом aud-races (остаточные риски лока).

6. **Попутные проверки без расхождений.** references/prompt-teammate.md и references/prompt-verifier.md существуют (на них ссылается SKILL.md:23-24); examples/tasks-review.json и examples/demo-run/ существуют (README:56-57); описания плагина в plugin.json:4 и marketplace.json:8 совпадают дословно; README:18 (путь установки через zcode-agent-teams → agent-teams) соответствует структуре marketplace.json:2-13.

## Итог

Чеклист: 4 из 4 OK. Сверх чеклиста: 5 находок — одна жёсткая (SKILL.md:81 противоречит коду v0.2.0), остальные неточности формулировок и стыки с соседними аудитами (aud-paths, aud-races).
