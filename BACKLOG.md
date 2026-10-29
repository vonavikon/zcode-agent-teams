# Backlog

Движок (team.py) получил 30 findings в первом прогоне (examples/demo-run/findings-*.md). Ниже — принятое к работе; детали и номера строк в отчётах прогона.

## Движок, следующий релиз (v0.2.0)

- Гонки read-modify-write на tasks.json и mailbox: параллельные claim/verify/message теряют записи (findings-code M1/M2, findings-robust R-1/R-2/R-7). Фикс: командный лок `.tasks.lock` + уникальный tmp (pid+uuid) в save_tasks + атомарная запись mailbox
- Path traversal: имена team/assignee/--to/--for без валидации попадают в пути (findings-code M4, findings-robust R-6). Фикс: regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` в argparse
- Осиротевшие lock-файлы: при краше между os.open и save_tasks задача недоступна, команды unlock/release нет (findings-code M3, findings-robust R-3). Фикс: снятие лока при verify pass + подкоманда release + детекция протухшего лока
- Windows PermissionError на replace при занятом файле (findings-robust R-4). Фикс: ретрай-цикл
- Жёсткий парсинг events.jsonl в langfuse-экспорте роняет выгрузку на одной битой строке (findings-robust R-5). Фикс: пропускать битые с предупреждением

## Мелкое

- Мёртвый статус «verifying» — нигде не присваивается (findings-code m1)
- Лимит «максимум 2 круга» не enforcement — только текст правила (findings-robust R-9)
- Кодировка pipe на Windows для русского вывода (findings-robust R-11)
- «круг 0» в status против «с 1 круга» в verify-сообщении — разная лексика, унифицировать
- README: каталог examples/demo-run описан — теперь существует; перепроверить структуру после новых файлов

## Дистрибуция

- Публикация репо на GitHub (публичный маркет по корню репозитория)
- Валидация `zcode plugins validate` в CI
- Пятиязычный README по практике публичных репо владельца — после первых внешних пользователей
