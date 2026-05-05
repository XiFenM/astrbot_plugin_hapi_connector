# 更新日志

## v2.3.0 — Playbook 历史学习

新增 F13 Playbook 功能，让 AstrBot 从你过往与 Claude Code 的对话中自动学习项目规范。

1. **新增 `hapi_coding_learn_history` 工具**：分析远端机器上 `~/.claude/projects/` 下的 JSONL 历史记录，提炼有效做法、应避免的模式、项目约定和常用工作流，生成 Playbook
2. **新增 `/hapi learn [session]` 命令**：等价于 LLM 工具，可手动触发
3. **Playbook 持久化**：以 `machine_id:work_dir` 为 key 存储在 KV，插件重启后自动恢复
4. **Playbook 自动注入**：后续对话中 Playbook 内容自动注入 AstrBot 系统提示，无需手动配置
5. **分段总结支持**：对话记录超长时，按可配置的段大小（默认 10 万字符）分段，串行（携带前段摘要）或并行总结，最终合并为一份 Playbook
6. **新增配置项** `playbook_segment_size`（每段字符数上限）和 `playbook_parallel`（是否并行）
7. **Skills 文档更新**：SKILL.md 和 ADVANCED.md 新增历史学习章节，引导 AstrBot 主动调用

---

## v2.2.0 — 二期改进（F1–F8, F12）

全面提升信息可读性、用户体验和 LLM 智能化。

**F1 消息智能格式化**
- 工具参数不再显示原始 JSON，改为按工具类型定制的可读摘要（Write: 文件名+行数，Edit: 新旧对比，Bash: 命令文本，Agent: 描述）
- 统一噪音过滤：`token_count`、`thinking`、`rate_limit_event`、`ready` 类事件不再推送给用户

**F4 任务完成通知增强**
- 完成通知新增耗时统计（"X 分 Y 秒"）
- 附加最后 2 条 agent 消息摘要
- 根据关键词自动推断状态（✅ 完成 / ⚠️ 出错 / 📋 其他）

**F5 Auto Decision 透明度增强**
- auto 模式通知包含：工具摘要 + 置信度 + 决策理由
- 上报用户时附带上报原因说明

**F6 审批超时两阶段提醒**
- 新增 `approval_timeout` 配置项（默认 60 秒）
- 超时前 15 秒：自动推送 "请尽快处理" 提醒
- 超时后：直接通知用户（不经 LLM 转述）

**F7 SSE 断连即时通知**
- 首次断连后延迟 3 秒推送通知（避免瞬断噪音）
- 快速恢复时不推送，降低打扰

**F8 审批通知附加上下文**
- 权限请求通知中新增 `💭 上下文：AI 当时说的最后一句话`，帮助判断操作意图

**F12 机器与目录发现**
- 新增 `hapi_coding_list_machines` 工具：列出所有在线机器及历史工作目录
- 新增 `hapi_coding_list_session_paths` 工具：浏览当前 session 的远端目录结构

---

## v2.1.0 — LLM 智能决策系统

新增三模式 LLM 决策系统，可由 AstrBot 自动分析权限请求并代替用户审批。

1. **三种模式**：
   - `off`（默认）：关闭，所有请求推送给用户手动处理
   - `suggest`：辅助模式，LLM 分析并给出建议，用户手动决策
   - `auto`：全自动模式，LLM 代替用户审批，高风险或低置信度操作仍上报
2. **新增配置项**：`auto_decision_mode`、`auto_decision_max_history`（最大历史消息数）、`auto_decision_confidence_threshold`（置信度阈值 1-10）
3. **覆盖范围扩展**：决策系统不仅处理 HAPI 工具审批，同时覆盖 AstrBot LLM 工具调用审批

---

## v2.0.6 — 新增 `/hapi resume` 指令

新增 `/hapi resume [序号|ID前缀]` 命令，用于恢复已归档（archived）的 inactive session。

---

## v2.0.5 — Codex 思考深度支持

1. 新增 Codex 会话创建时的思考深度选项（需 HAPI 服务端 >= 0.16.2）

---

## v2.0.0 — 自然语言操作远程会话

**此版本提供了 AstrBot 原生 Function Calling 能力的集成，现在你可以用自然语言管理远程 vibe 会话了**

1. **新增 LLM 工具支持**：为 AstrBot 提供 10 个工具，实现 AI 代理远程管理 HAPI coding sessions
   - 查询类工具（4个）：获取 session 列表、状态、配置、可用命令
   - 操作类工具（6个）：发送消息、切换 session、创建 session、停止消息、修改配置、执行任意 HAPI 命令
   - 所有操作类工具均复用审批命令和审批逻辑，需管理员审批，支持 `/hapi a`、`/hapi deny`、戳一戳快速批准

2. **审批机制优化**
   - 序号管理系统：每个待审批请求分配唯一序号，删除后自动回收复用
   - 优化审批通知格式：显示"当前共 x 个待审批，此请求审批序号：x"

---

## v1.6.0 — 多会话通知管理机制改进

1. 修复 Codex SSE 完成态判定，修复部分情况会出现的 Codex 延迟通知问题
2. 支持多窗口推送机制（群聊、私聊、不同管理员账户之间通知互相隔离）

详见 [窗口隔离特性介绍](docs/session-isolation.md)

---

## v1.5.1 — 命令体验优化 & 文件上传支持

1. 新增 `/hapi clean [路径前缀]` 命令，批量清理已归档 sessions
2. SSE 连接支持最大重试次数限制，避免无限重连
3. 优化所有命令输出格式与提示文本
4. 修复手机端心跳空消息导致交互式命令异常退出的问题
5. 支持 `hapi upload` 命令，快捷发送时可直接附图上传

---

## v1.5.0 — 文件列表 & 文件下载

1. 新增 `/hapi files [路径]` 命令，浏览远端 session 工作目录
2. 新增 `/hapi download <路径>` 命令（别名 `dl`），下载远端文件并发送到聊天，支持图片预览
3. 大文件（>10MB）下载前自动弹出确认提示

---

## v1.4.3

1. 新增 Cloudflare Zero Trust Access 认证配置支持
2. 新增 CF Access 配置指南文档（含截图）

---

## v1.4.0 — 交互视觉优化

1. 工具调用提醒统一改为 `🛠️ 工具名: 参数` 格式
2. `TodoWrite` 工具调用渲染为任务清单，支持 ✅ / 🔄 / ⬜ 状态符号

---

## v1.3.1

1. 新增上下文压缩支持：检测到 `Prompt is too long` 时自动发送 `/compact` 并恢复会话

---

## v1.3.0 — 自动化托管支持

1. 新增忙时托管审批功能（`auto_approve_enabled`，默认关闭）
2. 新增 `/hapi remote` 命令，切换会话到 remote 远程托管模式
3. 修复 `/hapi msg` 超长消息自动分片问题

---

## v1.2.3

1. 新增待审批请求超时提醒（`remind_pending` + `remind_interval`）

---

## v1.2.1

1. 新增 `AskUserQuestion` 类型权限请求的识别与处理
2. 新增 `/hapi answer`、`/hapi allow` 命令

---

## v1.2.0 — 基础功能完善

1. 统一语义标签格式推送（`[Message]`、`[Function Calling]`、`[System]`）
2. `/hapi msg` 改为按交互轮数计算
3. 新增 `/hapi abort` 命令（别名 `stop`）
