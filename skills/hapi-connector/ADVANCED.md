# HAPI 高级用法

## 发送指令的最佳实践

### 好的指令
- "在 /src/api/payment.py 中，将 process_payment 函数重构为异步，保持接口兼容"
- "项目根目录下运行 pytest，修复所有失败的测试"
- "查看 /src/config.ts 的内容，找出 API_BASE_URL 的定义位置"

### 差的指令
- "帮我改一下代码"（太模糊，应指明文件和需求）
- "写一个完整的电商系统"（范围太大，应拆分为多个小任务）

## 会话能力利用

创建会话后，系统会自动抓取该会话的能力配置（MCP servers、slash commands、skills）。
构造指令时可以利用这些能力，例如：
- 会话有 postgres MCP → "用 MCP 查询 users 表的最近 10 条记录"
- 会话有 /commit command → "完成后用 /commit 提交"
- 会话有 /review-pr command → "用 /review-pr 审查 PR #123"

## 多会话并行

可以同时维护多个会话处理不同任务：
- 用 `hapi_coding_list_sessions` 查看所有会话状态
- 用 `hapi_coding_switch_session` 切换活跃会话
- 用 `hapi_coding_send_message` 向当前会话发送

## 配置管理

- `hapi_coding_get_config_status`: 查看当前插件配置
- `hapi_coding_change_config`: 修改配置（如输出级别、自动审批等）

## 输出级别

- **silence**: 仅推送权限请求和任务完成
- **simple**: 推送 agent 文本消息（默认）
- **summary**: 任务完成时推送摘要
- **detail**: 实时推送所有消息（信息量大）
