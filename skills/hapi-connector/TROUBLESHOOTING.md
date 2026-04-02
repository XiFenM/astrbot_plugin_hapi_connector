# HAPI 问题排查

## 常见问题

### 会话创建失败
- 检查 hapi 服务是否在线：用 `hapi_coding_list_sessions` 测试连接
- 确认工作目录路径正确：用 `hapi_coding_list_session_paths` 查看已有 session 的工作目录作为参考
- 确认 machine_id 正确：用 `hapi_coding_list_machines` 查看在线机器

### 消息发送无响应
- 用 `hapi_coding_get_status` 检查会话状态
- 如果状态为 "thinking" 说明正在执行中，等待即可
- 如果有待审批请求，告知用户需要处理审批
- 不要轮询！系统会自动推送结果

### 审批流程
- auto_decision 模式开启时，低风险操作会自动批准
- 高风险操作（rm -rf、sudo 等）始终需要人工审批
- 用户可以通过按钮或 `/hapi a` 命令批准
- `/hapi deny` 拒绝操作

### 会话连接断开
- SSE 连接会自动重连，通常无需干预
- 如果长时间未恢复，建议用户检查 hapi 服务状态
- 已有的会话数据不会丢失

### 无法看到会话
- 确认当前窗口已绑定到正确的会话：`/hapi routes`
- 尝试列出所有窗口的会话：`hapi_coding_list_sessions(window="all")`
- 使用 `/hapi bind` 重新绑定
