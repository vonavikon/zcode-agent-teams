# agent-teams：ZCode 的智能体团队

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) · [Русский](README.ru.md) · **中文** · [Español](README.es.md) · [Français](README.fr.md)

把 [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) 的做法移植到 ZCode，并由引擎而不是智能体的自律来约束验收循环：多个智能体在互相隔离的 git worktree 中开发同一份代码库，每个结果在集成前都要通过机器校验的门禁。

## 包含什么

- **状态机与 SQLite 状态**：任务状态的每次变更都是 `state.db` 中带 hash 链的保护性迁移；不会丢失更新，审计轨迹完整，自动备份与迁移
- **隔离的工作区**：每个任务一个 git worktree 和分支，记录 base commit，未提交的工作在崩溃和 release 后仍然保留
- **写入控制**：实际 diff 与任务的 `files_write` 比对；集合之外的任何写入都会让门禁失败
- **机器验收门禁**：带验证命令的准则由引擎亲自执行；必需准则失败会推翻 PASS 判定。executor 从不自行验收：判定需要 lead 令牌，提交需要 execution 令牌
- **集成门禁**：被接受的分支合并进集成分支，可选的检查命令在合并后的树中运行；冲突和回归让 run 保持 active，项目分支不受影响
- **恢复机制**：租约、心跳、重启上限、丢失 executor 的自动重新入队
- **可观测性**：来自子智能体通知的 token 用量、带可信度标记的成本、包含「团队对比单智能体」的运行指标、自包含的 HTML 报告和幂等的 Langfuse 导出

角色：lead 是 ZCode 主会话；teammate 和 verifier 是子智能体（角色档案在 `agents/`）。

## 安装

在 ZCode 中：Plugin Marketplace → Add → Add Plugin Marketplace → 填写本仓库的目录（marketplace.json 位于根目录）。然后 Personal → zcode-agent-teams → agent-teams → Install。

Skill 会以 `agent-teams` 的名字出现。引擎只需要 Python 3.10+，无其他依赖（仅标准库）。Langfuse 导出还需要 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`，以及可选的 `LANGFUSE_HOST`（支持 self-hosted；trace 走 OTLP 端点，events_only 模式即可）。

## 工作方式

```
lead: tasks.json (validated: DAG, paths, write overlaps)
  └─ team.py init                     prints the LEAD TOKEN once
  └─ spawn teammates (Agent tool, background)      ← 2-5 agents
       └─ teammate: claim (execution token, worktree) → work → submit
            └─ lead spawns a verifier (read-only) on each VERIFICATION REQUEST
                 ├─ engine runs command-backed criteria itself
                 ├─ PASS → team.py verify --verdict pass --lead-token ...
                 └─ FAIL → verify --verdict fail --feedback "..." → executor fixes
                      (2nd FAIL by default → blocked → release or handle)
  └─ team.py integrate --lead-token ... [--check CMD] [--apply]
  └─ team.py status → all completed; report --out run.html; metrics; langfuse

```

## 用法示例

- **N 个 lens 做评审**：针对同一个对象做 security、performance 和 coverage 评审，每个 lens 写入自己的输出文件
- **竞争性假设**：N 个独立调查者研究同一个 bug，辩论单独作为一个任务。防止锚定在第一个假设上
- **按层并行实现**：按互不重叠的文件拆分，`deps` 决定各批次顺序，集成门禁负责合并结果

## 什么时候不需要团队

任务有先后顺序、只改一个文件、依赖很多：一个会话或一个子智能体更划算。每多一个 teammate，token 消耗线性增加；实际收益看 `metrics`。

## 目录结构

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

## 兼容性

由 v0.2 引擎创建的团队（没有 `state.db` 的 JSON 文件）仍通过 `team_v02.py` 运行。新团队使用 `team.py` 和 SQLite 引擎。v0.2 镜像（`tasks.json`、`inbox/`、`events.jsonl`）在每次变更时仍然会写入。

## 许可证

MIT
