# agent-teams: equipos de agentes para ZCode

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) · [Русский](README.ru.md) · [中文](README.zh.md) · **Español** · [Français](README.fr.md)

Una adaptación del enfoque de [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) a la mecánica de ZCode en la que el ciclo de aceptación lo impone el motor y no la disciplina de los agentes: varios agentes trabajan en worktrees de git aislados sobre una base de código común y cada resultado pasa una puerta verificada por máquina antes de integrarse.

## Qué incluye

- **Máquina de estados y estado en SQLite**: cada cambio de estado de una tarea es una transición protegida con cadena de hash en `state.db`; sin actualizaciones perdidas, auditoría completa, copias y migraciones automáticas
- **Espacios de trabajo aislados**: un worktree y una rama de git por tarea, con el commit base registrado; el trabajo sin confirmar sobrevive a caídas y releases
- **Control de escrituras**: el diff real se compara con el `files_write` de la tarea; cualquier escritura fuera del conjunto tumba la puerta
- **Ejecución inmutable**: el submit exige un worktree limpio, fija una instantánea inmutable (git ref + SQLite) y la verificación se hace sobre esa instantánea; lo aprobado es byte a byte lo integrado
- **Puerta de aceptación mecánica**: el motor ejecuta los criterios con comando; un criterio obligatorio fallido anula un PASS. Un PASS exige además un informe de verificación estructurado (o un override del lead auditado). El ejecutor nunca verifica su propio trabajo: los veredictos exigen el token del lead, los envíos el token de ejecución
- **Puerta de integración y apply**: los SHA aceptados se fusionan en orden DAG en una rama de integración con comprobación opcional; apply es un paso aparte de fast-forward que rechaza un proyecto sucio o una base movida
- **Recuperación y fencing**: timeouts de lease, heartbeat y del intento completo; los intentos perdidos se aislan (token muerto, worktree en cuarentena conservado) y vuelven a la cola dentro de los límites
- **Observabilidad**: tokens desde las notificaciones de los subagentes, coste con indicadores de calidad explícitos, métricas de la ejecución incluida la comparación equipo frente a un solo agente, un informe HTML autosuficiente y una exportación idempotente a Langfuse
- **Tablero en vivo para un responsable no técnico**: `team.py ui` abre en el navegador una página de solo lectura: el plan como esquema, cómo fue el tiempo, qué hace ahora cada especialista, qué espera la decisión del responsable y por qué, hasta qué punto se puede confiar en los resultados, el gasto frente al presupuesto, los problemas y cómo terminó cada uno, y los archivos de cada resultado tal como se aceptaron, en palabras claras; nada en ella puede cambiar la ejecución
- **Un presupuesto que se respeta**: acuerde un presupuesto para el equipo y un límite por tarea; cuando se agota, no empieza trabajo nuevo (código de salida 2, `BUDGET_LIMIT`), los ejecutores en marcha no se detienen y el tablero dice por qué

Roles: el lead es la sesión principal de ZCode; los teammates y verificadores son subagentes (los perfiles de rol están en `agents/`).

## Instalación

En ZCode: Plugin Marketplace → Add → Add Plugin Marketplace → indica el directorio de este repositorio (marketplace.json está en la raíz). Después Personal → zcode-agent-teams → agent-teams → Install.

La skill aparece como `agent-teams`. El motor necesita Python 3.10+ y nada más (solo stdlib). La exportación a Langfuse requiere además `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` y opcionalmente `LANGFUSE_HOST` (self-hosted funciona; las trazas van al endpoint OTLP, el modo events_only basta).

## Cómo funciona

```
lead: tasks.json (validated: DAG, paths, write overlaps)
  └─ team.py init                     prints the LEAD TOKEN once
  └─ spawn teammates (Agent tool, background)      ← 2-5 agents
       └─ teammate: claim (execution token, worktree) → work → submit
            └─ lead spawns a verifier (read-only) on each VERIFICATION REQUEST
                 ├─ engine runs command-backed criteria itself
                 ├─ PASS → verify --verdict pass --report report.json --lead-token ...
                 └─ FAIL → verify --verdict fail --feedback "..." → executor fixes
                      (2nd FAIL by default → blocked → release or handle)
  └─ team.py integrate --lead-token ... [--check CMD] [--apply]
  └─ team.py status → all completed; report --out run.html; metrics; langfuse
  └─ team.py ui → live read-only board in the browser for the owner

```

## Recetas

- **Revisión con N lentes**: seguridad, rendimiento y cobertura sobre un mismo objeto; cada lente escribe su propio archivo
- **Hipótesis en competencia**: N investigadores independientes del mismo bug; el debate es una tarea aparte. Evita anclarse en la primera hipótesis
- **Implementación en paralelo por capas**: división en archivos disjuntos, `deps` fija el orden de las oleadas y la puerta de integración fusiona el resultado

## Cuándo no hace falta un equipo

Tareas secuenciales, ediciones de un solo archivo, muchas dependencias: una sesión o un subagente sale más barato. Los tokens crecen linealmente con cada teammate; la ganancia real está en `metrics`.

## Estructura

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
                                     + dashboard/ (the board page)
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

## Compatibilidad

Los equipos creados por el motor v0.2 (archivos JSON sin `state.db`) siguen funcionando con `team_v02.py`. Los equipos nuevos usan `team.py` y el motor SQLite. Los espejos v0.2 (`tasks.json`, `inbox/`, `events.jsonl`) se siguen escribiendo en cada mutación.

## Licencia

MIT
