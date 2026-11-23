# agent-teams : équipes d'agents pour ZCode

Un portage de l'approche [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) vers la mécanique de ZCode : plusieurs agents travaillent sur une même base de code à partir d'une liste de tâches partagée, et l'acceptation fait partie de la boucle.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) · [Русский](README.ru.md) · [中文](README.zh.md) · [Español](README.es.md) · **Français**

## Ce que contient le plugin

- **Liste de tâches partagée** : `.agent-teams/<team>/tasks.json` contient les statuts, les dépendances et les ensembles de fichiers. Chaque mutation passe par un verrou d'état unique et s'écrit de façon atomique (depuis v0.2.0)
- **Messages en boîte aux lettres (mailbox)** : les agents s'écrivent directement, sans passer par le lead. Fichiers : `.agent-teams/<team>/inbox/<agent>.json`
- **Journal et verrous** : `events.jsonl` enregistre chaque changement de statut (c'est la source du rapport Langfuse). Le répertoire `locks/` contient le verrou d'état (O_EXCL ; les verrous périmés sont récupérés automatiquement)
- **Limite de tours** : une tâche qui échoue encore et encore à la vérification passe en `blocked` et revient au lead via `release`. En v0.2.x le blocage arrive au troisième échec de vérification (voir [BACKLOG.md](BACKLOG.md))
- **Porte d'acceptation (boucle exit-2)** : un exécutant ne clôt pas sa propre tâche. Un sous-agent vérificateur contrôle chaque critère sur les fichiers réels. Un FAIL dépose le feedback dans la boîte aux lettres de l'exécutant et le travail continue
- **Analytique Langfuse** : `team.py langfuse` envoie l'exécution : une trace par équipe, un span par tâche, un score par verdict. Les tours avant réussite, le temps par lens et le coût sont visibles dans l'interface

Rôles : le lead est la session ZCode principale (il planifie, lance les sous-agents et synthétise), les teammates sont des sous-agents et les vérificateurs sont des sous-agents en lecture seule.

## Installation

Dans ZCode : Plugin Marketplace → Add → Add Plugin Marketplace → indiquer le répertoire de ce dépôt (marketplace.json est à la racine). Puis Personal → zcode-agent-teams → agent-teams → Install.

La skill apparaît dans la liste sous le nom `agent-teams`. Le rapport Langfuse nécessite les variables d'environnement `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` et `LANGFUSE_HOST`. Les instances self-hosted fonctionnent : les traces partent vers le endpoint OTLP `/api/public/otel/v1/traces` et les scores vers `/api/public/scores`, donc le mode events_only suffit.

## Fonctionnement

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

## Recettes

- **Revue en N lenses (lens)** : sécurité, performance et couverture sur un même objet, chaque lens écrit dans son propre fichier de sortie
- **Hypothèses concurrentes** : N enquêteurs indépendants sur un même bug, les entrées ne se recoupent pas et le débat est une tâche à part. Protège contre l'ancrage sur la première hypothèse
- **Implémentation parallèle par couches** : un découpage en fichiers disjoints, `deps` fixe l'ordre des vagues

## Quand une équipe n'est pas nécessaire

Tâches séquentielles, modifications d'un seul fichier, beaucoup de dépendances : une session ou un seul sous-agent revient moins cher. Les tokens croissent linéairement avec chaque teammate.

## Structure

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

## Licence

MIT
