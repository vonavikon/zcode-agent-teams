# agent-teams : équipes d'agents pour ZCode

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) · [Русский](README.ru.md) · [中文](README.zh.md) · [Español](README.es.md) · **Français**

Un portage de l'approche [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) vers la mécanique de ZCode où le cycle d'acceptation est imposé par le moteur et non par la discipline des agents : plusieurs agents travaillent dans des worktrees git isolés sur une même base de code et chaque résultat passe une porte vérifiée par la machine avant intégration.

## Ce que contient le plugin

- **Machine à états et état SQLite** : chaque changement de statut est une transition protégée à chaîne de hash dans `state.db` ; aucune mise à jour perdue, piste d'audit complète, sauvegardes et migrations automatiques
- **Espaces de travail isolés** : un worktree et une branche git par tâche, commit de base enregistré, le travail non commité survit aux crashes et aux releases
- **Contrôle des écritures** : le diff réel est comparé au `files_write` de la tâche ; toute écriture hors ensemble fait tomber la porte
- **Exécution immuable** : le submit exige un worktree propre, épingle un instantané immuable (git ref + SQLite) et la vérification porte sur cet instantané ; ce qui est accepté est octet pour octet ce qui est intégré
- **Porte d'acceptation machine** : le moteur exécute lui-même les critères à commande ; un critère obligatoire échoué annule un PASS. Un PASS exige en plus un rapport de vérification structuré (ou un override du lead audité). L'exécutant ne valide jamais son propre travail : les verdicts exigent le token du lead, les soumissions le token d'exécution
- **Porte d'intégration et apply** : les SHA acceptés fusionnent en ordre DAG dans une branche d'intégration avec contrôle facultatif ; apply est une étape séparée en fast-forward qui refuse un projet sale ou une base déplacée
- **Récupération et fencing** : timeouts de lease, de heartbeat et de la tentative entière ; les tentatives perdues sont isolées (token mort, worktree en quarantaine conservé) et remises en file dans les limites
- **Observabilité** : tokens tirés des notifications des sous-agents, coût avec des indicateurs de qualité explicites, métriques de run dont la comparaison équipe contre agent unique, un rapport HTML autonome et un export Langfuse idempotent
- **Tableau de bord en direct pour un responsable non technique** : `team.py ui` ouvre dans le navigateur une page en lecture seule avec le plan, ce que fait chaque spécialiste, ce qui attend la décision du responsable et pourquoi, en mots simples ; rien sur cette page ne peut changer le déroulement

Rôles : le lead est la session ZCode principale ; teammates et vérificateurs sont des sous-agents (les profils de rôle sont dans `agents/`).

## Installation

Dans ZCode : Plugin Marketplace → Add → Add Plugin Marketplace → indiquer le répertoire de ce dépôt (marketplace.json à la racine). Puis Personal → zcode-agent-teams → agent-teams → Install.

La skill apparaît sous le nom `agent-teams`. Le moteur exige Python 3.10+ et rien d'autre (stdlib seule). L'export Langfuse requiert en plus `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` et éventuellement `LANGFUSE_HOST` (le self-hosted fonctionne ; les traces passent par le endpoint OTLP, le mode events_only suffit).

## Fonctionnement

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

## Recettes

- **Revue en N lenses** : sécurité, performance et couverture sur un même objet, chaque lens écrit son propre fichier
- **Hypothèses concurrentes** : N enquêteurs indépendants sur un même bug, le débat est une tâche à part. Protège contre l'ancrage sur la première hypothèse
- **Implémentation parallèle par couches** : découpage en fichiers disjoints, `deps` fixe l'ordre des vagues, la porte d'intégration fusionne le résultat

## Quand une équipe n'est pas nécessaire

Tâches séquentielles, modifications d'un seul fichier, beaucoup de dépendances : une session ou un seul sous-agent revient moins cher. Les tokens croissent linéairement avec chaque teammate ; le gain réel se lit dans `metrics`.

## Structure

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

## Compatibilité

Les équipes créées par le moteur v0.2 (fichiers JSON sans `state.db`) continuent de fonctionner via `team_v02.py`. Les nouvelles équipes utilisent `team.py` et le moteur SQLite. Les miroirs v0.2 (`tasks.json`, `inbox/`, `events.jsonl`) sont toujours écrits à chaque mutation.

## Licence

MIT
