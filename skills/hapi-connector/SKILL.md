---
name: hapi-connector
description: 通过 hapi_coding 工具调用远程 AI 编程助手（Claude Code / Codex / Gemini）执行编程任务。当用户要求编写代码、修改项目、调试程序、或提及 Claude Code / 编程助手时触发此技能。
---

# HAPI 编程助手

通过 hapi_coding 系列工具调用远程 AI 编程助手。任务异步执行，进度自动推送。

## 核心工作流

0. （可选）用 `hapi_coding_list_machines` 查看在线机器和历史工作目录
1. 用 `hapi_coding_list_sessions` 查看是否有可复用的会话
2. 有相同目录的 idle 会话 → `hapi_coding_send_message`；没有 → `hapi_coding_create_session`
3. **（推荐）首次使用某个 session 时，用 `hapi_coding_learn_history` 学习该项目的历史工作模式**
4. 发送任务后告知用户"已提交，有进展会自动通知"，**不要轮询结果**

## 会话选择

- 同一项目目录复用同一会话（保持上下文）
- 不同项目创建不同会话
- 上下文过长或主题完全不同时新建

## Agent 类型

- **claude**: 最强大，复杂编程任务首选（默认）
- **codex**: OpenAI Codex，快速代码生成
- **gemini**: Google Gemini，多模态任务
- **opencode**: 轻量级助手

## 创建会话前的环境探索

如果用户不确定在哪个目录创建会话：
1. `hapi_coding_list_machines` — 查看在线机器和最近使用的目录
2. `hapi_coding_list_session_paths` — 列出已有 session 的工作目录路径

## 历史学习

当需要向一个 session 发送编程任务时，如果系统提示中没有该 session 的历史经验（[HAPI 历史经验]），
**主动调用 `hapi_coding_learn_history`** 来学习用户的工作习惯。这样可以：
- 构造更符合用户习惯的指令
- 遵循项目特定的约定（测试、提交、代码风格）
- 避免用户已知的低效模式

## 会话管理操作

以下操作有**专用工具**，**不要**通过 `send_message` 发送指令来完成：

- **切换模型**: `hapi_coding_list_models` 查看可用模型 → `hapi_coding_switch_model` 切换（Claude/Gemini 支持）
- **上下文压缩**: `hapi_coding_compact_context` 触发压缩（上下文过长时使用）
- **停止任务**: `hapi_coding_stop_message` 停止当前任务

## 关键规则

- 用户说"帮我写/改/修XXX" → 调用编程助手，而非自己生成代码
- `send_message` 后**不要**立即查询状态，等待 SSE 自动推送
- 构造指令时要清晰具体，包含文件路径、需求细节
- 如果系统提示中显示了当前会话的能力（MCP/Skills/Commands），在指令中可以引用
- **切换模型、压缩上下文等操作使用专用工具，不要用 send_message 发送 /compact 或其他命令**

## 指令构造示例

好的指令：
- "在 /src/api/payment.py 中，将 process_payment 函数重构为异步，保持接口兼容"
- "项目根目录下运行 pytest，修复所有失败的测试"
- "用 /commit 提交当前更改"（当会话有 /commit command 时）

差的指令：
- "帮我改一下代码"（太模糊）
- "写一个完整的电商系统"（范围太大，应拆分）

## Takeover 全盘接管模式

当 auto_decision_mode = takeover 时，可使用全盘接管工作流，适合复杂多步骤目标：

1. 用户描述最终目标 → 调用 `hapi_coding_takeover_plan` 规划任务列表
2. 系统展示任务计划，用户确认或提出修改意见
3. 用户确认后 → 调用 `hapi_coding_takeover_control(action="start")` 开始执行
4. 系统自动逐步执行任务，每完成一个自动评估并推进下一个
5. 执行期间用户可随时：
   - `hapi_coding_takeover_status` 查看进度
   - `hapi_coding_takeover_control(action="pause")` 暂停
   - `hapi_coding_takeover_control(action="resume")` 恢复
   - `hapi_coding_takeover_control(action="cancel")` 取消

注意：takeover 继承 auto 模式的全部审批能力，高风险操作仍会上报。

详细用法见 [ADVANCED.md](ADVANCED.md)，问题排查见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
