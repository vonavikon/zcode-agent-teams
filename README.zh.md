# agent-teams：ZCode 的智能体团队

把 [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) 的做法移植到 ZCode 的机制上：多个智能体基于一份共享的任务列表在同一份代码库上工作，验收环节内建在循环里。

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) · [Русский](README.ru.md) · **中文** · [Español](README.es.md) · [Français](README.fr.md)

## 包含什么

- **共享任务列表**：`.agent-teams/<team>/tasks.json` 保存状态、依赖和文件范围。所有修改都在同一把状态锁下进行，并原子写入（自 v0.2.0 起）
- **Mailbox 消息**：agent 之间直接互发消息，不经过 lead。文件位置：`.agent-teams/<team>/inbox/<agent>.json`
- **事件日志与锁**：`events.jsonl` 记录每一次状态流转（它是 Langfuse 报告的数据源）。`locks/` 目录存放状态锁（O_EXCL；过期锁会被自动回收）
- **轮次上限**：反复验收失败的任务会进入 `blocked`，由 lead 通过 `release` 取回。在 v0.2.x 中，任务在第三次验收失败时被阻塞（见 [BACKLOG.md](BACKLOG.md)）
- **验收门禁（exit-2 循环）**：executor 不能自己关闭任务。verifier 子智能体按实际文件逐条核对验收标准。FAIL 会把反馈写入 executor 的 mailbox，工作继续进行
- **Langfuse 分析**：`team.py langfuse` 上传本次运行：每个团队一条 trace，每个任务一个 span，每个判定一个 score。通过前的轮次、各 lens 的耗时和成本都能在界面里看到

角色：lead 是 ZCode 的主会话（负责规划、派生和汇总），teammate 是子智能体，verifier 是只读的子智能体。

## 安装

在 ZCode 中：Plugin Marketplace → Add → Add Plugin Marketplace → 填写本仓库的目录（marketplace.json 位于根目录）。然后 Personal → zcode-agent-teams → agent-teams → Install。

Skill 会以 `agent-teams` 的名字出现在列表中。Langfuse 报告需要环境变量 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` 和 `LANGFUSE_HOST`。支持 self-hosted 实例：trace 写入 OTLP 端点 `/api/public/otel/v1/traces`，score 写入 `/api/public/scores`，因此 events_only 模式就足够了。

## 工作方式

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

## 用法示例

- **N 个 lens 做评审**：针对同一个对象做 security、performance 和 coverage 评审，每个 lens 写入自己的输出文件
- **竞争性假设**：N 个独立调查者研究同一个 bug，输入互不重叠，辩论单独作为一个任务。防止锚定在第一个假设上
- **按层并行实现**：按互不重叠的文件拆分，`deps` 决定各批次的先后顺序

## 什么时候不需要团队

任务有先后顺序、只改一个文件、依赖很多：一个会话或一个子智能体更划算。每多一个 teammate，token 消耗就线性增加。

## 目录结构

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

## 许可证

MIT
