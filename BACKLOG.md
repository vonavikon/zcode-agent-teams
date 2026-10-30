# Backlog

Движок получил 30 findings в первом прогоне (examples/demo-run/findings-*.md).

## Сделано в v0.2.0 (2026-10-30)

- Гонки read-modify-write на tasks.json и mailbox: единый лок состояния `.state.lock` (O_EXCL, ретраи, подбор протухшего >2 ч) на все мутации + атомарные записи через уникальный tmp (pid+uuid) — findings-code M1/M2, findings-robust R-1/R-2/R-7
- Path traversal: имена команды/агентов/id задач валидируются regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` — findings-code M4, findings-robust R-6
- Осиротевшие состояния: подкоманда `release` возвращает задачу в pending — findings-code M3, findings-robust R-3
- Windows PermissionError на replace: ретрай с нарастающим ожиданием — findings-robust R-4
- Битые строки events.jsonl не роняют langfuse-выгрузку — findings-robust R-5
- Лимит кругов enforcement: после 2 провалов приёмки задача → `blocked`, фидбек ROUND LIMIT в mailbox — findings-robust R-9
- Экономика: `tokens --task --add N`, счётчики в tasks.json/status, суммарные токены в trace-метаданных и span

## Мелкое (открыто)

- Кодировка pipe на Windows для русского вывода (findings-robust R-11)
- Мёртвый статус «verifying» — допускается verify, но не присваивается (findings-code m1); можно выкинуть

## Дистрибуция

- Валидация `zcode plugins validate` в CI
- Пятиязычный README — после первых внешних пользователей
