<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_hapi_connector?name=astrbot_plugin_hapi_connector&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# HAPI Vibe Coding 遥控器

_✨ 随时随地 Vibe Coding ✨_

[![License](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-LiJinHao999-blue)](https://github.com/LiJinHao999)

</div>

## 📦 安装方法

在 AstrBot 插件市场搜索 **hapi_connector**，点击安装即可。

或手动填入仓库地址安装：

```
https://github.com/LiJinHao999/astrbot_plugin_hapi_connector
```

依赖（`aiohttp`、`aiohttp-socks`）会由 AstrBot 自动安装。

项目需要后端，项目的后端服务为 [HAPI](https://github.com/tiann/hapi)

点击查看[部署＆连接插件教程](docs/install.md)

---

## 🤝 这是干嘛用的？

这是一个**通过聊天指令远程管理 AI 编码会话的插件**。

你在外面摸鱼，电脑在家跑代码——通过这个插件，你可以在 QQ、微信、Telegram 等任意聊天平台上，直接操控跑在远端机器上的 Claude Code / Codex / Gemini / OpenCode，发消息、审批权限、切换模型，一条指令，甚至拍一拍 QQ 机器人搞定。

**它连接的后端是 [HAPI](https://github.com/tiann/hapi)**，一个统一管理多个 AI 编码代理会话的服务，是 [HAPPY CODER](https://github.com/slopus/happy) 的开源本地实现版本——**数据全部留在本地**。

只需在机器上通过 NPM 安装 HAPI，启动 AI 编码会话时加上 `hapi` 前缀，会话即自动接入 Hub 管理：

```bash
hapi claude   # Claude Code
hapi codex    # OpenAI Codex
```

如需在服务器长时间后台运行：

```bash
screen -S hapi
hapi codex    # 按 Ctrl+A, Ctrl+D 后台挂起
```

同时支持通过 AstrBot 远程启动新的 Claude Code / Codex 会话（需配置 runner 服务）。

> **一句话总结**：AI 编码会话的远程控制台，外加一个会自己做决策的 AI 助理。

![架构图展示](docs/pics/Architecture.png)

---

## ✨ 核心特性

- **无缝切换**：离开电脑后随时用手机接管，不中断 AI 工作流
- **随时随地 Vibe**：远程启动 Claude Code / Codex / Gemini CLI，随时开启新会话
- **本地部署**：极低延迟，无需公网 IP
- **多窗口隔离**：在群聊 A、B、C 和私聊中独立管理不同会话，互不干扰（[详见说明](docs/session-isolation.md)）
- **自然语言操控**：AstrBot 原生 Function Calling 集成，直接说"帮我创建一个 Claude 会话"即可
- **LLM 智能决策**：三种模式（自动/建议/关闭）——让 AstrBot 自动审批或给出建议，高风险操作仍上报用户
- **Playbook 历史学习**：分析远端 Claude Code 的历史对话记录，自动提炼项目约定和工作习惯，注入 AstrBot 系统提示
- **Agent Skills 集成**：内置 Skill 文件，引导 AstrBot 主动调用 HAPI 工具完成编程委托任务
- **审批机制**：戳一戳快速批准、忙时自动托管、超时提醒、LLM 辅助决策，灵活应对不同场景
- **文件双向传输**：上传配置 / 下载日志，小文件收发无障碍
- **兼容 QQ / 微信官方 Bot**：无法主动推送时自动 fallback 为被动回复

---

## 🧠 怎么工作的？

1. 插件启动后连接 HAPI 服务，建立 SSE 长连接监听所有事件
2. AI 有新消息、权限请求、任务完成时，按当前窗口绑定规则自动推送到对应聊天窗口
3. 你发指令 → 插件调用 HAPI REST API → 操作对应的 AI 会话
4. 快捷前缀（默认 `>`）让你不打 `/hapi to` 也能快速发消息
5. LLM 决策系统在后台分析权限请求，自动批准或给出建议
6. Playbook 功能从历史记录中学习，让 AstrBot 发出的指令更符合你的习惯

---

## ⚙️ 配置

安装后在 AstrBot 管理面板的插件配置页填写。

### 连接与认证

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `hapi_endpoint` | HAPI 服务地址，如 `http://0.0.0.0:3006` | |
| `access_token` | HAPI Access Token，支持 `token:namespace` 格式 | |
| `proxy_url` | 代理地址，支持 `socks5h://` 和 `http://` | 空 |
| `cf_access_client_id` | Cloudflare Zero Trust Service Token Client ID（[详见文档](docs/cf_access_guide.md)） | 空 |
| `cf_access_client_secret` | Cloudflare Zero Trust Service Token Client Secret | 空 |
| `jwt_lifetime` | JWT 有效期（秒） | 900 |
| `refresh_before_expiry` | JWT 提前刷新时间（秒） | 180 |

### 推送与交互

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `output_level` | SSE 推送级别：`silence` / `simple` / `summary` / `detail` | `simple` |
| `summary_msg_count` | summary 级别显示的消息条数 | 5 |
| `quick_prefix` | 快捷发送前缀字符（后跟空格使用） | `>` |
| `default_notification_window` | 强制推送到指定窗口 ID（留空则跟随 session 绑定） | 空 |
| `poke_approve` | 戳一戳自动全部审批（仅 QQ NapCat） | 开启 |

### 审批与超时

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `remind_pending` | 待审批请求超时后重复提醒 | 开启 |
| `remind_interval` | 待审批重复提醒间隔（秒） | 180 |
| `approval_timeout` | LLM 工具审批超时时间（秒），超时前 15 秒自动提醒 | 60 |
| `auto_approve_enabled` | 忙时托管：在指定时间范围内自动批准所有权限请求 | 关闭 |
| `auto_approve_start` | 忙时托管开始时间（HH:MM，24小时制） | `23:00` |
| `auto_approve_end` | 忙时托管结束时间（HH:MM，支持跨午夜） | `07:00` |

### LLM 智能决策

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `auto_decision_mode` | 决策模式：`auto`（全自动）/ `suggest`（辅助建议）/ `off`（关闭） | `off` |
| `auto_decision_max_history` | 决策时拉取的最大历史消息数 | 30 |
| `auto_decision_confidence_threshold` | 置信度阈值（1-10），低于此值上报用户 | 7 |

### SSE 重连

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `max_reconnect_attempts` | SSE 最大重连次数（0 表示无限） | 10 |

### Playbook 历史学习

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `playbook_segment_size` | 分段总结的每段字符数上限 | 100000 |
| `playbook_parallel` | 分段总结是否并行执行（串行质量更高，并行更快） | 关闭 |

---

## 🤖 LLM 工具集成（自然语言交互）

插件提供 **14 个 Function Calling 工具**，支持用自然语言管理远程会话。

### 工具列表

| 工具名 | 说明 | 需要审批 |
|--------|------|---------|
| `hapi_coding_get_status` | 获取当前 session 状态 | 否 |
| `hapi_coding_list_sessions` | 列出 session 列表（支持窗口/路径/代理过滤） | 否 |
| `hapi_coding_message_history` | 查询历史消息 | 否 |
| `hapi_coding_get_config_status` | 查看插件配置 | 否 |
| `hapi_coding_list_commands` | 列出可用指令（按主题分类） | 否 |
| `hapi_coding_list_machines` | 列出所有在线机器及历史工作目录 | 否 |
| `hapi_coding_list_session_paths` | 浏览当前 session 的远端目录结构 | 否 |
| `hapi_coding_learn_history` | 分析 Claude Code 历史记录，生成项目 Playbook | 否 |
| `hapi_coding_send_message` | 发送消息到当前 session | 是 |
| `hapi_coding_switch_session` | 切换 session | 是 |
| `hapi_coding_create_session` | 创建新 session | 是 |
| `hapi_coding_stop_message` | 停止消息生成 | 是 |
| `hapi_coding_change_config` | 修改插件配置 | 是 |
| `hapi_coding_execute_command` | 执行任意 /hapi 指令 | 是 |

**使用方式**：在 AstrBot 管理面板开启工具后，直接对话即可。如"帮我用 Claude Code 写一个排序算法"、"切换到1号session"、"学习一下这个项目的历史"。

**智能隔离**：非管理员不会注册任何工具；当前窗口没有可见 HAPI 会话时，仅保留 `list_sessions`、`list_commands`、`list_machines`、`learn_history`、`execute_command` 5 个基础工具。

**审批机制**：操作类工具需管理员审批，支持 `/hapi a` 批准、`/hapi deny` 拒绝、戳一戳快速批准。

---

## 🧠 LLM 智能决策系统

插件内置三模式 LLM 决策系统，由 AstrBot 的 LLM 自动分析权限请求：

| 模式 | 说明 |
|------|------|
| `off` | 关闭，所有请求均推送给用户手动审批（默认） |
| `suggest` | 辅助模式，LLM 分析请求并给出建议，用户手动最终决策 |
| `auto` | 全自动模式，LLM 代替用户审批，高风险或低置信度操作仍上报用户 |

`auto` 模式下，LLM 会结合会话历史、当前上下文、工具参数进行综合判断，对超出置信度阈值的请求自动批准并通知用户；对不确定的操作上报用户处理。

---

## 📚 Playbook 历史学习

`hapi_coding_learn_history` 工具（或 `/hapi learn` 命令）会：

1. 定位远端机器上对应项目的 Claude Code 历史记录（`~/.claude/projects/`）
2. 读取 JSONL 格式的对话文件，提取关键交互片段
3. 调用 LLM 分析，生成 **Playbook**（有效做法 / 应避免 / 项目约定 / 常用工作流）
4. 将 Playbook 持久化存储，并在后续对话中自动注入 AstrBot 系统提示

**好处**：AstrBot 在代理你发出指令时，会自动遵循你的项目约定（测试习惯、提交规范、代码风格），减少来回沟通。

内容过长时支持分段总结（串行/并行可配置），最终合并为一份 Playbook。

---

## 🤖 Agent Skills 集成

插件内置 Agent Skill 文件，安装后自动注册到 AstrBot 的 Skills 系统：

- **SKILL.md**：核心工作流（查看会话→复用或创建→历史学习→发送任务→等待通知）
- **ADVANCED.md**：高级用法（多会话、Playbook 生命周期、能力利用）
- **TROUBLESHOOTING.md**：问题排查（连接失败、超时、审批流程）

AstrBot 在处理"帮我用 Claude Code 写 XXX"类请求时，会自动参考这些 Skills 文件的工作流指引。

---

## ⌨️ 指令列表

所有指令以 `/hapi` 开头，**仅管理员可用**。

### 📋 会话查看

| 指令 | 说明 |
|------|------|
| `/hapi list` | 查看当前窗口可见的 session（别名 `ls`） |
| `/hapi list all` | 查看全部 session 和全局绑定状态 |
| `/hapi sw <序号或ID前缀>` | 切换当前会话 |
| `/hapi s` | 查看当前会话状态（别名 `status`） |
| `/hapi msg [轮数]` | 查看最近消息，默认 1 轮（别名 `messages`） |

### 💬 消息发送

| 指令 | 说明 |
|------|------|
| `/hapi to <序号> <内容>` | 发送消息到指定会话 |
| `> 消息内容` | 快捷发送到当前会话 |
| `>N 消息内容` | 快捷发送到第 N 个会话 |

> 快捷前缀可在配置中修改，默认为 `>`

### 🛠️ Session 管理

| 指令 | 说明 |
|------|------|
| `/hapi create` | 创建新会话（交互向导；Codex 为 6 步，其他为 5 步） |
| `/hapi abort [序号\|ID前缀]` | 中断会话，默认当前（别名 `stop`） |
| `/hapi remote` | 切换当前会话到 remote 远程托管模式 |
| `/hapi archive` | 归档当前会话 |
| `/hapi resume [序号\|ID前缀]` | 恢复已归档的 inactive session |
| `/hapi rename` | 重命名当前会话 |
| `/hapi delete` | 删除当前会话 |
| `/hapi clean [路径前缀]` | 批量清理 inactive session |

### ✅ 权限审批

| 指令 | 说明 |
|------|------|
| `/hapi pending` | 查看待审批请求列表 |
| `/hapi a` | 批准所有权限请求 + 交互式回答 question（别名 `approve`） |
| `/hapi allow [序号]` | 仅批准普通权限请求（跳过 question） |
| `/hapi answer [序号]` | 交互式回答 question 请求 |
| `/hapi deny` | 全部拒绝 |
| `/hapi deny <序号>` | 拒绝单个请求 |
| 戳一戳机器人 | 批准所有普通请求 + 交互式处理 question（仅 QQ NapCat） |

### 📁 文件操作

| 指令 | 说明 |
|------|------|
| `/hapi files [路径]` | 浏览当前 session 的远端目录 |
| `/hapi files -l [路径]` | 浏览目录并显示文件大小 |
| `/hapi find <关键词>` | 搜索当前 session 的远端文件 |
| `/hapi download <路径>` | 下载远端文件到当前聊天（别名 `dl`） |
| `/hapi upload [cancel]` | 上传文件到当前 session |

### 📚 历史学习

| 指令 | 说明 |
|------|------|
| `/hapi learn [session]` | 分析指定 session 的 Claude Code 历史记录，生成 Playbook |

### 🔧 模式与帮助

| 指令 | 说明 |
|------|------|
| `/hapi perm [模式]` | 查看/切换权限模式（不带参数则交互选择） |
| `/hapi model [模式]` | 查看/切换模型（仅 Claude，不带参数则交互选择） |
| `/hapi output [级别]` | 查看/切换 SSE 推送级别（别名 `out`） |
| `/hapi help [主题]` | 显示帮助，主题：会话 / 对话 / 审批 / 通知 / 文件 / 配置 |

---

## 📡 SSE 推送级别

| 级别 | 说明 |
|------|------|
| `silence` | 仅推送权限请求和等待输入提醒，其余静默 |
| `simple` | AI 思考完成后推送纯文本 agent 消息及系统事件 |
| `summary` | AI 思考完成后推送最近 N 条 agent 消息 |
| `detail` | 实时推送所有新消息（信息量较大） |

---

## 🤖 支持的 AI 代理

| 代理 | 可用权限模式 |
|------|-------------|
| Claude Code | `default` / `acceptEdits` / `bypassPermissions` / `plan` |
| Codex | `default` / `read-only` / `safe-yolo` / `yolo` |
| Gemini | `default` / `read-only` / `safe-yolo` / `yolo` |
| OpenCode | `default` / `yolo` |

---

## 🔔 通知路由

- **按聊天窗口隔离**：私聊、群聊之间互不影响
- **支持默认通知窗口**：`/hapi bind` 把当前窗口设为默认通知窗口
- **支持模型级默认窗口**：`/hapi bind claude|codex|gemini` 分别指定不同类型的通知窗口
- **会话绑定优先级最高**：某 session 一旦被当前窗口接管，后续通知优先回到该窗口
- **强制窗口**：配置 `default_notification_window` 可强制所有通知推到指定窗口

| 指令 | 说明 |
|------|------|
| `/hapi bind` | 设置当前窗口为默认通知窗口 |
| `/hapi bind claude` | 设置当前窗口为 Claude 的默认通知窗口 |
| `/hapi bind codex` | 设置当前窗口为 Codex 的默认通知窗口 |
| `/hapi bind gemini` | 设置当前窗口为 Gemini 的默认通知窗口 |
| `/hapi bind status` | 查看默认窗口和绑定状态 |
| `/hapi bind reset` | 清除 session 绑定和窗口状态 |
| `/hapi routes` | 查看当前生效的推送路由 |

---

## 📁 插件结构

```
astrbot_plugin_hapi_connector/
├── main.py                     # 插件入口：生命周期、工具注册、戳一戳/快捷前缀
├── _conf_schema.json           # 插件配置 schema
├── metadata.yaml               # 插件元信息
├── requirements.txt            # Python 依赖
│
├── core/                       # 基础设施
│   ├── hapi_client.py          # 异步 HAPI HTTP 客户端 + JWT 自动刷新
│   ├── cf_access.py            # Cloudflare Zero Trust Access 认证
│   └── constants.py            # 常量（权限模式、模型、Codex 思考深度等）
│
├── managers/                   # 状态与数据管理
│   ├── state_manager.py        # 用户状态（当前 session、flavor、Playbook）
│   ├── binding_manager.py      # 聊天窗口与 session 绑定
│   ├── pending_manager.py      # 待审批请求管理（序号分配、批准/拒绝）
│   └── notification_manager.py # 通知推送与消息分发
│
├── ops/                        # HAPI API 操作层
│   ├── session_ops.py          # Session CRUD + 历史记录读取
│   ├── approval_ops.py         # 审批业务逻辑
│   └── file_ops.py             # 文件查询、上传、下载
│
├── ui/                         # 用户界面
│   ├── formatters.py           # 格式化输出（工具调用、权限通知、Playbook 等）
│   ├── command_handlers.py     # 所有 /hapi 子命令处理器
│   └── create_wizard.py        # 创建会话交互式向导
│
├── llm/                        # LLM 集成
│   ├── llm_integration.py      # 14 个 Function Calling 工具 + Playbook 注入
│   └── auto_decision.py        # LLM 智能决策系统（auto/suggest/off）
│
├── sse/                        # 实时推送
│   └── sse_listener.py         # 后台 SSE 监听 + 事件分发 + 断连通知
│
├── skills/                     # Agent Skills（自动安装到 AstrBot）
│   └── hapi-connector/
│       ├── SKILL.md            # 核心工作流
│       ├── ADVANCED.md         # 高级用法
│       └── TROUBLESHOOTING.md  # 问题排查
│
├── docs/                       # 文档
│   ├── install.md
│   ├── cf_access_guide.md
│   └── session-isolation.md
│
└── tests/                      # 测试
    ├── test_auto_decision.py
    └── test_playbook.py
```

---

## 📌 TODO

- ✅ 优化输出格式，提升交互可读性（工具参数智能格式化）
- ✅ 完善部署文档与使用教程
- ✅ 支持文件上传与下载
- ✅ 支持多用户独立会话状态，通知相互隔离
- ✅ 通过 AstrBot 自然语言触发指令（14 个 Function Calling 工具）
- ✅ LLM 智能决策系统（auto/suggest/off 三模式）
- ✅ Playbook 历史学习（从 Claude Code 历史记录中提炼项目约定）
- ✅ Agent Skills 集成（引导 AstrBot 主动使用 HAPI 工具）
- ✅ 机器与目录发现（list_machines / list_session_paths）
- ✅ 审批超时两阶段提醒
- ✅ SSE 断连即时通知
- ✅ 审批通知附加上下文（AI 当时在做什么）
- [ ] Markdown / 长上下文渲染为图片（依赖库独立，可选下载）

---

## 🙏 致谢

- [HAPI](https://github.com/tiann/hapi) — 本插件连接的后端服务，由 [@tiann](https://github.com/tiann) 开发
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 跨平台聊天机器人框架

---

## 💗 友情链接

- [linuxdo 社区](https://linux.do/) — 极度优秀的 AI 知识分享社区
- [linuxdo 上关于此插件的设计思路贴](https://linux.do/t/topic/1799761)

---

## 👥 贡献指南

- 🌟 Star 本项目
- 🐛 提交 Issue 报告问题
- 💡 提出新功能建议
- 🔧 提交 Pull Request 改进代码
