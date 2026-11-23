# agent-teams: equipos de agentes para ZCode

Una adaptación del enfoque de [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) a la mecánica de ZCode: varios agentes trabajan sobre una base de código a partir de una lista de tareas compartida, y la aceptación forma parte del ciclo.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) · [Русский](README.ru.md) · [中文](README.zh.md) · **Español** · [Français](README.fr.md)

## Qué incluye

- **Lista de tareas compartida**: `.agent-teams/<team>/tasks.json` guarda estados, dependencias y conjuntos de archivos. Todas las mutaciones se ejecutan bajo un único bloqueo de estado y se escriben de forma atómica (desde v0.2.0)
- **Mensajes de buzón (mailbox)**: los agentes se escriben directamente entre sí, sin pasar por el lead. Archivos: `.agent-teams/<team>/inbox/<agent>.json`
- **Registro de eventos y bloqueos**: `events.jsonl` registra cada cambio de estado (es la fuente del informe de Langfuse). El directorio `locks/` contiene el bloqueo de estado (O_EXCL; los bloqueos obsoletos se recuperan automáticamente)
- **Límite de rondas**: una tarea que falla la verificación una y otra vez pasa a `blocked` y vuelve al lead a través de `release`. En v0.2.x el bloqueo se produce en el tercer fallo de verificación (ver [BACKLOG.md](BACKLOG.md))
- **Puerta de aceptación (ciclo exit-2)**: un ejecutor no cierra su propia tarea. Un subagente verificador comprueba cada criterio contra los archivos reales. Un FAIL deja el feedback en el buzón del ejecutor y el trabajo continúa
- **Analítica con Langfuse**: `team.py langfuse` sube la ejecución: una traza por equipo, un span por tarea, una puntuación por veredicto. Las rondas hasta aprobar, el tiempo por lente y el coste se ven en la interfaz

Roles: el lead (líder) es la sesión principal de ZCode, planifica, lanza subagentes y resume; los teammates son subagentes y los verificadores son subagentes de solo lectura.

## Instalación

En ZCode: Plugin Marketplace → Add → Add Plugin Marketplace → indica el directorio de este repositorio (marketplace.json está en la raíz). Después Personal → zcode-agent-teams → agent-teams → Install.

La skill aparece en la lista como `agent-teams`. El informe de Langfuse necesita las variables de entorno `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` y `LANGFUSE_HOST`. Las instancias self-hosted funcionan: las trazas van al endpoint OTLP `/api/public/otel/v1/traces` y las puntuaciones a `/api/public/scores`, así que basta el modo events_only.

## Cómo funciona

```
lead: tasks.json (non-overlapping files_write, acceptance criteria)
  └─ team.py init
  └─ spawn teammates (Agent tool, background)      ← 2-5 agents
       └─ teammate: claim → work → "VERIFICATION REQUEST"
            └─ lead spawns a verifier (read-only)
                 ├─ PASS → team.py verify --verdict pass
                 └─ FAIL → verify --verdict fail --feedback "..."
                      └─ feedback to mailbox → SendMessage to executor → fix
  └─ team.py status → all completed
  └─ team.py langfuse → trace + spans + scores
```

## Recetas

- **Revisión con N lentes (lens)**: seguridad, rendimiento y cobertura sobre un mismo objeto; cada lente escribe en su propio archivo de salida
- **Hipótesis en competencia**: N investigadores independientes del mismo bug, las entradas no se solapan y el debate es una tarea aparte. Evita anclarse en la primera hipótese
- **Implementación en paralelo por capas**: una división en archivos que no se solapan; `deps` fija el orden de las oleadas

## Cuándo no hace falta un equipo

Tareas secuenciales, ediciones de un solo archivo, muchas dependencias: una sesión o un subagente sale más barato. Los tokens crecen de forma lineal con cada compañero de equipo.

## Estructura

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

## Licencia

MIT
