"""LLM 工具集成 - 为 LLM 提供 HAPI Coding Session 交互能力"""

import asyncio
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger
from . import session_ops
from . import formatters


class LLMIntegration:
    """LLM 工具集成管理器"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.client = plugin.client
        self.state_mgr = plugin.state_mgr
        self.pending_mgr = plugin.pending_mgr
        self.sessions_cache = plugin.sessions_cache

    # ──── 工具可见性控制 ────

    async def on_llm_request_hook(self, event: AstrMessageEvent, request: ProviderRequest):
        """根据权限和窗口状态动态控制工具可见性，并注入会话上下文"""
        # 1. 权限检查：非管理员移除所有工具
        is_admin = self.plugin._is_admin(event)
        if not is_admin:
            self._remove_hapi_tools(request, keep_basic=False)
            return

        # 2. 上下文检查：窗口无可见 session 时只保留基础工具
        visible_sessions = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
        if not visible_sessions:
            self._remove_hapi_tools(request, keep_basic=True)
            return

        # 3. F2/F3: 注入动态会话状态和能力信息到 LLM 系统提示
        self._inject_session_context(request, visible_sessions, event)

    async def _fetch_and_store_caps(self, sid: str):
        """异步抓取并缓存会话的能力配置（fire-and-forget）"""
        try:
            caps = await session_ops.fetch_session_capabilities(self.client, sid)
            self.state_mgr.set_capabilities(sid, caps)
            logger.info("[caps] 已抓取会话能力 sid=%s skills=%d cmds=%d mcp=%d",
                        sid[:8], len(caps.get("skills", [])),
                        len(caps.get("slash_commands", [])),
                        len(caps.get("mcp_servers", [])))
        except Exception as e:
            logger.warning("[caps] 抓取会话能力失败 sid=%s: %s", sid[:8], e)

    def _inject_session_context(self, request: ProviderRequest,
                                visible_sessions: list[dict],
                                event):
        """将当前会话状态和能力信息注入 LLM 系统提示"""
        lines = ["\n[HAPI 当前状态]"]
        for i, s in enumerate(visible_sessions[:5], 1):
            sid = s.get("id", "?")
            meta = s.get("metadata", {})
            title = (meta.get("summary") or {}).get("text", "(无标题)")
            flavor = meta.get("flavor", "?")
            is_thinking = s.get("thinking", False)
            is_active = s.get("active", False)
            state = "thinking" if is_thinking else ("active" if is_active else "idle")
            lines.append(f"  [{i}] {title[:30]} | {flavor} | {state} | {sid[:8]}")

            # 附加能力详情
            caps = self.state_mgr.get_capabilities(sid)
            if caps:
                if caps.get("mcp_servers"):
                    lines.append(f"      MCP 服务: {', '.join(caps['mcp_servers'])}")
                if caps.get("skills"):
                    lines.append("      技能:")
                    for sk in caps["skills"]:
                        name = sk.get("name", "?")
                        desc = sk.get("description", "")
                        lines.append(f"        - {name}: {desc[:60]}" if desc else f"        - {name}")
                if caps.get("slash_commands"):
                    lines.append(f"      可用命令 ({len(caps['slash_commands'])} 个):")
                    for c in caps["slash_commands"]:
                        name = c.get("name", "?")
                        desc = c.get("description", "")
                        source = c.get("source", "")
                        suffix = f" [{source}]" if source else ""
                        lines.append(f"        /{name}{suffix}: {desc[:50]}" if desc else f"        /{name}{suffix}")
                if caps.get("claude_md_summary"):
                    lines.append(f"      项目描述: {caps['claude_md_summary'][:200]}")

        # F13: 附加 playbook（历史学习经验，按 machine:path 去重）
        seen_playbook_keys: set[str] = set()
        for s in visible_sessions[:5]:
            work_dir = s.get("metadata", {}).get("path", "")
            machine_id = s.get("machineId", "")
            pb_key = self._playbook_key(machine_id, work_dir)
            if not pb_key or pb_key in seen_playbook_keys:
                continue
            playbook = self.state_mgr.get_playbook(pb_key)
            if playbook:
                seen_playbook_keys.add(pb_key)
                proj_name = work_dir.rstrip("/").split("/")[-1] if work_dir else pb_key[:20]
                lines.append(f"\n[HAPI 历史经验 - {proj_name}]")
                lines.append(playbook)

        injected_text = "\n".join(lines)
        if hasattr(request, 'system_prompt'):
            request.system_prompt = (request.system_prompt or "") + injected_text

    def _remove_hapi_tools(self, request: ProviderRequest, keep_basic: bool = False):
        """移除所有 hapi_coding 工具

        Args:
            keep_basic: 是否保留基础工具（list_sessions/list_commands/execute_command）
        """
        if not hasattr(request, 'func_tool') or not request.func_tool:
            return

        # 基础工具（始终可用，即使无可见 session）
        basic_tools = {
            "hapi_coding_list_sessions",
            "hapi_coding_list_commands",
            "hapi_coding_execute_command",
            "hapi_coding_create_session",
            "hapi_coding_send_message",
            "hapi_coding_list_machines",       # F12: 查看在线机器
            "hapi_coding_list_session_paths",    # F12: 浏览机器目录
        }

        # 所有工具
        all_tools = {
            "hapi_coding_get_status",
            "hapi_coding_list_sessions",
            "hapi_coding_message_history",
            "hapi_coding_get_config_status",
            "hapi_coding_list_commands",
            "hapi_coding_send_message",
            "hapi_coding_switch_session",
            "hapi_coding_create_session",
            "hapi_coding_change_config",
            "hapi_coding_stop_message",
            "hapi_coding_execute_command",
            "hapi_coding_list_machines",       # F12
            "hapi_coding_list_session_paths",    # F12
            "hapi_coding_learn_history",          # F13
        }

        # 决定要移除的工具
        tools_to_remove = all_tools - basic_tools if keep_basic else all_tools

        for tool_name in tools_to_remove:
            request.func_tool.remove_tool(tool_name)

    # ──── 审批机制 ────

    async def _require_approval(self, tool_name: str, args: dict, event: AstrMessageEvent) -> tuple[bool, str]:
        """请求审批并等待结果

        Returns:
            (approved, reason): approved=True表示批准，reason说明原因（"approved"/"denied"/"timeout"/"notification_failed"/"auto_decided"）
        """
        # ── Auto Decision 检查 ──
        auto_decision_mgr = getattr(self.plugin.sse_listener, '_auto_decision_mgr', None)
        suggestion_prefix = None
        if auto_decision_mgr is not None:
            try:
                result = await auto_decision_mgr.try_auto_decide_llm_tool(tool_name, args, event)
                if result.handled:
                    # auto 模式已决策
                    approved = result.action == "approve"
                    action_text = "✅ 已自动批准" if approved else "❌ 已自动拒绝"
                    await event.send(MessageChain().message(
                        f"🤖 [LLM 自动决策] AstrBot 工具: {tool_name}\n  {action_text}"
                    ))
                    return approved, "auto_decided"
                # suggest 模式的建议文本
                suggestion_prefix = result.suggestion_text
            except Exception as e:
                logger.warning("LLM 工具 Auto Decision 异常: %s", e)

        # LLM 工具审批使用窗口 ID 作为 key，而不是 session ID
        window_id = event.unified_msg_origin

        # 添加到 pending 队列（伪装成 HAPI 权限请求）
        req_id, future, index = self.pending_mgr.add_llm_tool_request(window_id, tool_name, args)

        # 计算当前待审批总数（LLM 工具审批不受窗口限制，统计所有待审批）
        items = self.pending_mgr.flatten_pending(None, None)
        total = len(items)

        # 计算窗口数量
        visible_sids = {s.get("id") for s in self.state_mgr.visible_sessions_for_window(event, self.sessions_cache) if s.get("id")}
        visible_sids.add(event.unified_msg_origin)
        window_items = self.pending_mgr.flatten_pending(event, visible_sids)
        window_total = len(window_items)

        # 发送通知到当前窗口
        args_str = ", ".join(f"{k}={v}" for k, v in args.items())
        msg = f"""🤖 Astrbot 工具调用请求
  {tool_name}
  参数: {args_str}

当前总共 {total} 个待审批，当前对话窗口共 {window_total} 个待审批，此请求审批序号 {index}

审批指令:
  /hapi a        全部批准
  /hapi allow <序号>  批准单个
  /hapi deny     全部拒绝
  /hapi deny <序号> 拒绝单个
  /hapi pending   查看完整列表"""

        # suggest 模式：在通知前附加建议
        if suggestion_prefix:
            msg = suggestion_prefix + msg

        notification_sent = False
        try:
            chain = MessageChain().message(msg)
            platform = event.get_platform_name()
            if platform == "telegram":
                try:
                    from astrbot.core.platform.sources.telegram.tg_event import TelegramInlineKeyboard
                    chain.chain.append(TelegramInlineKeyboard(buttons=[
                        [(f"✅ 批准 #{index}", f"hapi_approve:{index}"),
                         (f"❌ 拒绝 #{index}", f"hapi_deny:{index}")],
                        [("✅ 全部批准", "hapi_approve:all"),
                         ("❌ 全部拒绝", "hapi_deny:all")],
                    ]))
                except Exception as e:
                    logger.warning("构建 LLM 工具审批 keyboard 失败: %s", e)
            elif platform == "discord":
                try:
                    from astrbot.core.platform.sources.discord.components import DiscordButton, DiscordView
                    chain.chain.append(DiscordView(components=[
                        DiscordButton(label=f"✅ 批准 #{index}", custom_id=f"hapi_approve:{index}", style="success"),
                        DiscordButton(label=f"❌ 拒绝 #{index}", custom_id=f"hapi_deny:{index}", style="danger"),
                        DiscordButton(label="✅ 全部批准", custom_id="hapi_approve:all", style="success"),
                        DiscordButton(label="❌ 全部拒绝", custom_id="hapi_deny:all", style="danger"),
                    ]))
                except Exception as e:
                    logger.warning("构建 Discord 审批按钮失败: %s", e)
            await event.send(chain)
            notification_sent = True
        except Exception as e:
            logger.warning(f"LLM 工具审批通知发送失败: {e}")

        # 如果通知发送失败，立即返回拒绝
        if not notification_sent:
            self.pending_mgr.remove_entry(window_id, req_id)
            logger.error(f"LLM 工具 {tool_name} 审批通知发送失败，自动拒绝")
            return False, "notification_failed"

        # 等待审批结果（可配置超时，两阶段：T-15s 提醒 + 超时直接通知）
        timeout = max(5, self.plugin.config.get("approval_timeout", 60))
        try:
            if timeout > 15:
                # 阶段 1：等待至 T-15 秒（shield 保护 future 不被 wait_for 取消）
                try:
                    approved = await asyncio.wait_for(asyncio.shield(future), timeout=timeout - 15)
                    return (True, "approved") if approved else (False, "denied")
                except asyncio.TimeoutError:
                    # T-15 提醒
                    try:
                        await event.send(MessageChain().message(
                            f"⏰ 审批提醒：{tool_name}\n15 秒后超时，请及时回复 /hapi a 批准或 /hapi deny 拒绝"
                        ))
                    except Exception:
                        pass
                # 阶段 2：最后 15 秒
                approved = await asyncio.wait_for(future, timeout=15)
                return (True, "approved") if approved else (False, "denied")
            else:
                approved = await asyncio.wait_for(future, timeout=timeout)
                return (True, "approved") if approved else (False, "denied")
        except asyncio.TimeoutError:
            # 超时，清理请求
            self.pending_mgr.remove_entry(window_id, req_id)
            logger.warning(f"LLM 工具 {tool_name} 审批超时（{timeout}秒无响应）")
            # 如果处于忙时托管时段，超时默认允许
            if self.plugin.sse_listener._auto_approve_enabled and self.plugin.sse_listener._in_auto_approve_window():
                logger.info(f"忙时托管时段，自动批准 {tool_name}")
                return True, "auto_approved"
            # 直接通知用户（不经 LLM 转述）
            try:
                await event.send(MessageChain().message(
                    f"⏰ 审批超时：{tool_name}\n已自动取消此操作。如需继续，请重新发起请求。"
                ))
            except Exception:
                pass
            return False, "timeout"
        except asyncio.CancelledError:
            # 任务被取消，清理并返回拒绝，不再传播异常
            self.pending_mgr.remove_entry(window_id, req_id)
            logger.warning(f"LLM 工具 {tool_name} 审批被取消")
            return False, "cancelled"

    def _effective_sid(self, event: AstrMessageEvent) -> str | None:
        """统一解析当前工具应作用的 session。"""
        return self.state_mgr.effective_sid(event)

    @staticmethod
    def _missing_session_text() -> str:
        return (
            "当前没有可操作的 session。请先调用 hapi_coding_list_sessions 查看会话，"
            "再用 hapi_coding_switch_session 切换，或先创建一个新 session。"
        )

    # ──── 查询类工具（无需审批）────

    async def tool_get_status(self, event: AstrMessageEvent):
        '''获取当前交互中的 HAPI session 的状态信息。'''
        sid = self._effective_sid(event)
        if not sid:
            yield self._missing_session_text()
            return

        try:
            detail = await session_ops.fetch_session_detail(self.client, sid)
            yield formatters.format_session_status(detail)
        except Exception as e:
            yield f"获取状态失败: {e}"

    async def tool_list_sessions(self, event: AstrMessageEvent, window: str = "", path: str = "", agent: str = ""):
        '''列出 HAPI 的可交互 session 列表。

        Args:
            window(string): 按聊天窗口过滤（默认为空表示当前窗口，设为 'all' 查询所有聊天窗口，用户没有明确要求时一般置空）
            path(string): 按路径搜索
            agent(string): 按代理类型过滤（claude/codex/gemini/opencode）
        '''
        # 当前窗口无session时，自动查询所有session
        visible_sessions = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
        if not visible_sessions and window == "":
            window = "all"
            auto_switched = True
        else:
            auto_switched = False

        if window == "all":
            sessions = self.sessions_cache
        else:
            sessions = visible_sessions

        # 过滤
        if path:
            sessions = [s for s in sessions if path.lower() in s.get("metadata", {}).get("path", "").lower()]
        if agent:
            sessions = [s for s in sessions if s.get("metadata", {}).get("flavor", "").lower() == agent.lower()]

        if not sessions:
            yield "没有找到符合条件的 session"
            return

        # 复用 formatters.format_session_list，但移除 emoji
        current_sid = self._effective_sid(event)
        text = formatters.format_session_list(sessions, current_sid, self.sessions_cache, header_current_window=event.unified_msg_origin)

        # 替换 emoji 为文字
        text = text.replace("📁", "[目录]")
        text = text.replace("🏷️", "ID:")
        text = text.replace("💭", "[思考中]")
        text = text.replace("🟢", "[运行中]")
        text = text.replace("⚪", "[已关闭]")
        text = text.replace("🤖", "")
        text = text.replace("⚠️", "[待审批]")
        text = text.replace("💡", "提示:")

        # 如果自动切换到all，添加提示
        if auto_switched:
            text = "提示：当前窗口无可见session，已自动查询所有窗口的session\n\n" + text

        yield text

    async def tool_message_history(self, event: AstrMessageEvent, rounds: int = 1):
        '''查询当前交互中的 session 的历史消息。

        Args:
            rounds(number): 查询最近几轮消息（默认 1 轮）
        '''
        sid = self._effective_sid(event)
        if not sid:
            yield self._missing_session_text()
            return

        try:
            # 多取消息以保证覆盖 N 轮
            fetch_limit = min(rounds * 80, 500)
            msgs = await session_ops.fetch_messages(self.client, sid, limit=fetch_limit)
            all_rounds = formatters.split_into_rounds(msgs)
            # 取最后 N 轮
            selected = all_rounds[-rounds:]
            if not selected:
                yield "暂无消息记录"
                return

            # 格式化所有轮次
            lines = []
            total = len(selected)
            for i, round_msgs in enumerate(selected, 1):
                text = formatters.format_round(round_msgs, i, total)
                lines.append(text)

            yield "\n\n".join(lines)
        except Exception as e:
            yield f"获取消息失败: {e}"

    async def tool_get_config_status(self, event: AstrMessageEvent):
        '''获取当前插件配置状态及可修改项说明。'''
        output_level = self.plugin.config.get("output_level", "simple")
        auto_approve = self.plugin.sse_listener._auto_approve_enabled
        auto_start = self.plugin.sse_listener._auto_approve_start
        auto_end = self.plugin.sse_listener._auto_approve_end
        remind = self.plugin.sse_listener._remind_enabled
        remind_interval = self.plugin.sse_listener._remind_interval
        quick_prefix = self.plugin.config.get("quick_prefix", ">")

        info = f"""当前配置状态:

output_level (SSE推送级别): {output_level}
  - silence: 仅推送权限请求和任务完成提醒
  - simple: 仅推送 agent 文本消息，不包含复杂的工具调用信息
  - summary: 任务完成时推送最近的 agent 消息
  - detail: 实时推送所有新消息（信息量较大）

auto_approve_enabled (忙时自动审批): {'开启' if auto_approve else '关闭'}
  时间段: {auto_start} - {auto_end}
  值: true/false

remind_pending (定时提醒待审批): {'开启' if remind else '关闭'}
  间隔: {remind_interval} 秒
  值: true/false

quick_prefix (快捷前缀): {quick_prefix}
  用于快速发送消息，如 "> 消息内容\""""
        yield info

    async def tool_list_commands(self, event: AstrMessageEvent, topic: str = ""):
        '''列出所有可用的 HAPI 指令。

        Args:
            topic(string): 帮助主题（可选，默认显示常用帮助）
        '''
        yield formatters.get_help_text(topic)

    async def tool_list_machines(self, event: AstrMessageEvent):
        '''列出所有在线机器及其信息，以及最近使用过的工作目录。用于创建会话前了解可用环境。'''
        try:
            machines = await session_ops.fetch_machines(self.client)
        except Exception as e:
            yield f"获取机器列表失败: {e}"
            return

        if not machines:
            yield "没有在线的机器"
            return

        lines = ["在线机器:"]
        for m in machines:
            mid = m.get("id", "?")
            meta = m.get("metadata", {})
            host = meta.get("host", "unknown")
            plat = meta.get("platform", "?")
            lines.append(f"  - {mid}: {host} ({plat})")

        try:
            recent = await session_ops.fetch_recent_paths(self.client)
            if recent:
                lines.append("\n最近使用过的工作目录:")
                for p in recent[:10]:
                    lines.append(f"  - {p}")
        except Exception:
            pass

        yield "\n".join(lines)

    async def tool_list_session_paths(self, event: AstrMessageEvent):
        '''列出所有已有 session 正在使用的工作目录路径，供创建新 session 时参考。'''
        if not self.sessions_cache:
            yield "当前没有任何 session，无历史路径可参考。可直接指定工作目录（Claude Code 会自动创建不存在的目录）。"
            return

        seen: set[str] = set()
        lines = ["已有 session 的工作目录:"]
        for s in self.sessions_cache:
            meta = s.get("metadata", {})
            path = meta.get("path", "")
            if not path or path in seen:
                continue
            seen.add(path)
            name = meta.get("name", "") or s.get("id", "?")[:8]
            host = meta.get("host", "")
            suffix = f"  ({host})" if host else ""
            lines.append(f"  {path}{suffix}  [{name}]")

        if len(lines) == 1:
            yield "已有 session 均无工作目录信息。可直接指定路径，Claude Code 会自动创建不存在的目录。"
            return

        yield "\n".join(lines)

    # ──── F13: 历史学习 ────

    @staticmethod
    def _playbook_key(machine_id: str, work_dir: str) -> str:
        """构造 playbook 的唯一 key：machine_id:work_dir"""
        mid = (machine_id or "").strip()
        wd = (work_dir or "").strip()
        if not wd:
            return ""
        return f"{mid}:{wd}" if mid else wd

    async def tool_learn_from_history(self, event: AstrMessageEvent, session_target: str = ""):
        '''分析指定 session 的 Claude Code 历史对话，学习有效的工作模式并生成 playbook。'''
        # 解析目标 session
        sid = None
        if session_target.strip():
            # 尝试序号或 ID 前缀
            target = session_target.strip()
            if target.isdigit():
                visible = self.state_mgr.visible_sessions_for_window(event, self.sessions_cache)
                idx = int(target) - 1
                if 0 <= idx < len(visible):
                    sid = visible[idx].get("id")
            if not sid:
                for s in self.sessions_cache:
                    if s.get("id", "").startswith(target):
                        sid = s["id"]
                        break
        if not sid:
            sid = self._effective_sid(event)
        if not sid:
            yield "未找到目标 session，请先切换到一个 session 或指定 session ID"
            return

        # 获取 session 工作目录
        session = next((s for s in self.sessions_cache if s.get("id") == sid), None)
        if not session:
            yield "session 信息不可用"
            return
        work_dir = session.get("metadata", {}).get("path", "")
        if not work_dir:
            yield "该 session 没有工作目录信息"
            return

        yield f"📚 正在查找 {work_dir} 的 Claude Code 历史记录..."

        # 获取可用于文件操作的 sid（原 session 可能不活跃）
        machine_id = session.get("machineId", "")
        file_sid = sid  # 默认用原 session
        temp_sid: str | None = None  # 临时 session，用完归档

        # 先用原 session 探测文件系统是否可访问
        try:
            probe = await session_ops.list_directory(self.client, file_sid, "/root")
        except Exception:
            probe = []

        if not probe and machine_id:
            # 原 session 不可用，在目标机器的 / 创建临时 session
            logger.info("[playbook] 原 session 文件系统不可访问，创建临时 session...")
            yield "🔄 正在创建临时会话以访问文件系统..."
            ok, msg, new_sid = await session_ops.spawn_session(
                self.client, machine_id, "/", "claude",
            )
            if ok and new_sid:
                file_sid = new_sid
                temp_sid = new_sid
                logger.info("[playbook] 临时 session 已创建: %s", new_sid[:8])
            else:
                logger.warning("[playbook] 临时 session 创建失败: %s", msg)
                yield f"⚠️ 无法创建临时会话访问文件系统: {msg}"
                return

        try:
            # 定位历史目录
            history_dir = await session_ops.find_cc_history_dir(self.client, file_sid, work_dir)
            if not history_dir:
                yield "未找到该工作目录的 Claude Code 历史记录（路径: ~/.claude/projects/）"
                return

            # 读取对话
            conversations = await session_ops.read_cc_conversations(
                self.client, file_sid, history_dir,
            )
            if not conversations:
                yield "历史目录下没有找到对话记录"
                return
        finally:
            # 清理临时 session
            if temp_sid:
                logger.info("[playbook] 归档临时 session %s", temp_sid[:8])
                try:
                    await session_ops.archive_session(self.client, temp_sid)
                except Exception as e:
                    logger.warning("[playbook] 临时 session 归档失败: %s", e)

        # 格式化对话内容
        formatted_convs = self._format_conversations(conversations)
        combined = "\n\n".join(formatted_convs)
        total_len = len(combined)

        yield f"📖 找到 {len(conversations)} 个历史对话，内容总长度 {total_len:,} 字符"

        # 长度超过阈值时分段总结（从插件配置读取，默认 100000）
        segment_threshold = self.plugin.config.get("playbook_segment_size", 100000)
        if total_len > segment_threshold:
            segment_count = (total_len + segment_threshold - 1) // segment_threshold
            yield f"⚠️ 内容较长（{total_len:,} 字符），将分 {segment_count} 段逐步总结，每段携带前段摘要以保持连贯性。"

        # 调用 LLM 分析（自动处理分段）
        playbook = await self._analyze_history_with_llm(
            formatted_convs, work_dir, event, segment_threshold,
        )
        if not playbook:
            yield "LLM 分析失败，请稍后重试"
            return

        # 存储 playbook（按 machine_id:work_dir，独立于 session 生命周期）
        machine_id = session.get("machineId", "")
        pb_key = self._playbook_key(machine_id, work_dir)
        self.state_mgr.set_playbook(pb_key, playbook)
        await self.state_mgr.persist_playbook(pb_key)
        await self.state_mgr.persist_playbook_index()
        logger.info("[playbook] 已生成 key=%s len=%d", pb_key, len(playbook))

        yield f"✅ Playbook 已生成并保存！\n\n{playbook}"

    @staticmethod
    def _format_conversations(conversations: list[dict]) -> list[str]:
        """将对话列表格式化为 LLM 可读的文本段落列表（每个对话一个段落）"""
        formatted = []
        for conv in conversations:
            lines = [f"=== 对话: {conv['filename']} ==="]
            for turn in conv["turns"]:
                if turn["role"] == "user":
                    lines.append(f"[用户指令] {turn['text']}")
                elif turn["role"] == "tool_use":
                    lines.append(f"[工具调用] {turn['tool']}: {turn.get('input_preview', '')}")
                elif turn["role"] == "assistant":
                    lines.append(f"[助手回复] {turn['text']}")
            formatted.append("\n".join(lines))
        return formatted

    _PLAYBOOK_SYSTEM_PROMPT = (
        "你是编程助手使用模式分析专家。分析用户与 AI 编程助手（Claude Code）的历史对话，"
        "提炼出可复用的工作模式。输出简洁实用的 playbook，供另一个 AI 在代理用户向 Claude Code 发送指令时参考。\n\n"
        "输出格式（中文）：\n"
        "## 有效做法\n- 列出用户的好指令模式（具体、可操作）\n\n"
        "## 应避免\n- 列出导致返工或低效的模式\n\n"
        "## 项目约定\n- 列出该项目的隐含规范（代码风格、测试、提交习惯等）\n\n"
        "## 常用工作流\n- 列出用户常见的任务模式（如：先读文件→再修改→跑测试→提交）"
    )

    async def _analyze_history_with_llm(self, formatted_convs: list[str],
                                        work_dir: str,
                                        event: AstrMessageEvent,
                                        segment_threshold: int = 100000) -> str | None:
        """调用 LLM 分析历史对话，生成 playbook。

        内容超过 segment_threshold 时自动分段：串行逐段总结，
        每段携带前段摘要以保持连贯性。
        """
        try:
            umo = event.unified_msg_origin
            prov = self.plugin.context.get_using_provider(umo=umo)
            if not prov:
                logger.warning("[playbook] 未找到可用的 LLM provider")
                return None
        except Exception as e:
            logger.warning("[playbook] 获取 LLM provider 失败: %s", e)
            return None

        combined = "\n\n".join(formatted_convs)

        # 进度通知辅助
        async def _notify(text: str):
            try:
                await event.send(MessageChain().message(text))
            except Exception:
                pass

        # ── 短内容：一次性总结 ──
        if len(combined) <= segment_threshold:
            await _notify("🔍 正在分析对话记录...")
            return await self._llm_summarize_segment(
                prov, work_dir, combined, prev_summary=None,
            )

        # ── 长内容：按行分段，超过阈值时在完整行边界切断 ──
        all_lines = combined.split("\n")
        segments: list[str] = []
        current_lines: list[str] = []
        current_len = 0
        for line in all_lines:
            line_len = len(line) + 1  # +1 for \n
            if current_len + line_len > segment_threshold and current_lines:
                segments.append("\n".join(current_lines))
                current_lines = []
                current_len = 0
            current_lines.append(line)
            current_len += line_len
        if current_lines:
            segments.append("\n".join(current_lines))

        logger.info("[playbook] 分 %d 段总结，总长 %d 字符", len(segments), len(combined))

        # 根据配置选择串行（携带上段摘要）或并行总结
        parallel = self.plugin.config.get("playbook_parallel", False)

        if parallel:
            # ── 并行：所有段同时总结，最后合并 ──
            logger.info("[playbook] 并行总结 %d 段...", len(segments))
            await _notify(f"🔍 正在并行分析 {len(segments)} 段内容...")
            tasks = [
                self._llm_summarize_segment(
                    prov, work_dir, seg, prev_summary=None,
                    segment_info=f"第 {i + 1}/{len(segments)} 段",
                )
                for i, seg in enumerate(segments)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            all_segment_summaries = [
                r for r in results if isinstance(r, str) and r
            ]
            await _notify(f"✔ 并行分析完成，{len(all_segment_summaries)}/{len(segments)} 段成功")
        else:
            # ── 串行：逐段携带前段摘要 ──
            all_segment_summaries: list[str] = []
            prev_summary: str | None = None
            for i, segment in enumerate(segments):
                logger.info("[playbook] 正在总结第 %d/%d 段 (%d 字符)...", i + 1, len(segments), len(segment))
                await _notify(f"🔍 正在分析第 {i + 1}/{len(segments)} 段...")
                result = await self._llm_summarize_segment(
                    prov, work_dir, segment, prev_summary=prev_summary,
                    segment_info=f"第 {i + 1}/{len(segments)} 段",
                )
                if result:
                    prev_summary = result
                    all_segment_summaries.append(result)
                else:
                    logger.warning("[playbook] 第 %d 段总结失败，使用前段结果", i + 1)

        if not all_segment_summaries:
            return None

        # 只有一段时直接返回
        if len(all_segment_summaries) == 1:
            return all_segment_summaries[0]

        # 最终合并：将所有段的 playbook 一起总结成最终版本
        logger.info("[playbook] 正在合并 %d 段摘要生成最终 playbook...", len(all_segment_summaries))
        await _notify(f"📝 正在合并 {len(all_segment_summaries)} 段分析结果生成最终 playbook...")
        merged = await self._llm_merge_summaries(prov, work_dir, all_segment_summaries)
        # 合并失败时回退到最后一段的结果
        return merged or all_segment_summaries[-1]

    async def _llm_summarize_segment(self, prov, work_dir: str,
                                     segment_text: str,
                                     prev_summary: str | None = None,
                                     segment_info: str = "") -> str | None:
        """对单个段落调用 LLM 生成/更新 playbook"""
        if prev_summary:
            prompt = (
                f"以下是用户在项目 {work_dir} 中与 Claude Code 的历史对话记录（{segment_info}）。\n\n"
                f"前面内容的分析结果：\n{prev_summary}\n\n"
                f"---\n\n"
                f"请结合上述已有分析和以下新内容，生成更完整的 playbook：\n\n{segment_text}"
            )
        else:
            prompt = f"以下是用户在项目 {work_dir} 中与 Claude Code 的历史对话记录：\n\n{segment_text}"

        try:
            resp = await prov.text_chat(
                system_prompt=self._PLAYBOOK_SYSTEM_PROMPT,
                prompt=prompt,
            )
            return resp.completion_text.strip() or None
        except Exception as e:
            logger.warning("[playbook] LLM 分析失败: %s", e)
            return None

    async def _llm_merge_summaries(self, prov, work_dir: str,
                                   summaries: list[str]) -> str | None:
        """将多段 playbook 摘要合并为最终版本"""
        numbered = "\n\n".join(
            f"--- 第 {i + 1} 段分析 ---\n{s}" for i, s in enumerate(summaries)
        )
        prompt = (
            f"以下是对项目 {work_dir} 的历史对话分 {len(summaries)} 段分析后的结果。\n"
            "请将所有段的分析合并为一份完整的最终 playbook，去除重复内容，保留所有独特的洞察：\n\n"
            f"{numbered}"
        )
        try:
            resp = await prov.text_chat(
                system_prompt=self._PLAYBOOK_SYSTEM_PROMPT,
                prompt=prompt,
            )
            return resp.completion_text.strip() or None
        except Exception as e:
            logger.warning("[playbook] 合并摘要失败: %s", e)
            return None

    # ──── 操作类工具（需要审批）────

    # ──── send_message 辅助方法 ────

    async def _fetch_completion_response(self, sid: str, pre_send_seq: int) -> str:
        """拉取 Claude Code 在 pre_send_seq 之后的回复，格式化为文本返回给 LLM。"""
        max_response_len = 4000
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=50)
            new_msgs = [
                m for m in messages
                if m.get("seq", 0) > pre_send_seq
                and m.get("content", {}).get("role") != "user"
            ]

            if not new_msgs:
                return "消息已发送，Claude Code 已处理完成。"

            texts = []
            for msg in sorted(new_msgs, key=lambda m: m.get("seq", 0)):
                text = formatters.extract_text_preview(msg.get("content", {}), max_len=0)
                if text:
                    texts.append(text)

            if not texts:
                return "消息已发送，Claude Code 已处理完成。"

            response = "\n\n".join(texts)
            if len(response) > max_response_len:
                response = response[:max_response_len] + "\n...(内容过长已截断，可调用 hapi_coding_message_history 查看完整内容)"

            return f"消息已发送，Claude Code 处理完成。回复:\n\n{response}"
        except Exception as e:
            logger.warning(f"[tool_send_message] 获取回复失败: {e}")
            return f"消息已发送，Claude Code 已处理完成，但获取回复失败: {e}"

    # ──── 操作类工具（需要审批）————续 ────

    async def tool_send_message(self, event: AstrMessageEvent, message: str):
        '''向当前 session 发送消息。消息发送后立即返回，Claude Code 完成处理后系统会自动推送结果。
        请勿在调用后轮询 get_status 或 message_history，结果会自动送达。

        Args:
            message(string): 要发送的消息内容
        '''
        sid = self._effective_sid(event)
        if not sid:
            yield self._missing_session_text()
            return

        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_send_message", {"message": message}, event)
        logger.debug(f"[tool_send_message] approved={approved}, reason={reason}")
        if not approved:
            if reason == "timeout":
                yield "操作已超时取消，已直接通知用户。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作已被用户拒绝，请停止工具调用，先交流清楚问题"
            return

        # 记录发送前的消息序号
        sse = self.plugin.sse_listener
        async with sse._lock:
            pre_send_seq = sse.session_states.get(sid, {}).get("lastSeq", 0)

        # 执行发送
        logger.info(f"[tool_send_message] sending to sid={sid[:8]}, msg_len={len(message)}")
        ok, result = await session_ops.send_message(self.client, sid, message)
        if not ok:
            yield f"发送失败: {result}"
            return

        # 存储完成回调上下文，供 SSE listener 完成时调用 LLM 回复
        import copy, time
        sse._pending_llm_completions[sid] = {
            "pre_send_seq": pre_send_seq,
            "ts": time.monotonic(),
        }
        # 启动超时监控
        asyncio.create_task(self._send_timeout_watch(sid))
        logger.info(f"[tool_send_message] sent, registered completion callback sid={sid[:8]}")

        yield ("消息已发送给 Claude Code，正在后台处理中。\n"
               "[重要] 请勿调用 hapi_coding_get_status 或 hapi_coding_message_history 轮询进度。"
               "Claude Code 完成后系统会自动将结果推送给用户。\n"
               "请直接回复用户：任务已提交给 Claude Code，完成后会自动通知结果。")

    async def _send_timeout_watch(self, sid: str, timeout: int = 300):
        """超时监控：如果 Claude Code 在 timeout 秒内未完成，推送超时通知并清理。"""
        await asyncio.sleep(timeout)
        sse = self.plugin.sse_listener
        ctx = sse._pending_llm_completions.pop(sid, None)
        if not ctx:
            return  # 已正常完成，无需处理
        logger.info(f"[tool_send_message] timeout for sid={sid[:8]}")
        await sse._push_notification(
            f"⏱️ Claude Code 任务超时（{timeout}秒内未完成）。\n"
            "可使用 /hapi msg 查看当前状态。", sid)

    async def tool_switch_session(self, event: AstrMessageEvent, target: str):
        '''切换到指定的 session。

        Args:
            target(string): session 序号（如 "1"）或 session ID（如 "abc12345"）
        '''
        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_switch_session", {"target": target}, event)
        if not approved:
            if reason == "timeout":
                yield "操作已超时取消，已直接通知用户。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作已被用户拒绝，请停止工具调用，先交流清楚问题"
            return

        # 复用 cmd_sw 逻辑，提取消息内容返回给 LLM
        async for result in self.plugin.cmd_handlers.cmd_sw(event, target):
            # result 是 MessageChain，提取文本内容
            if hasattr(result, 'chain'):
                for seg in result.chain:
                    if hasattr(seg, 'text'):
                        yield seg.text
            else:
                yield str(result)

    async def tool_create_session(self, event: AstrMessageEvent, directory: str, agent: str,
                                   machine_id: str = "", session_type: str = "simple", yolo: bool = False,
                                   model_reasoning_effort: str = ""):
        '''创建新的 coding session。

        Args:
            directory(string): 工作目录路径
            agent(string): 代理类型（claude/codex/gemini/opencode）
            machine_id(string): 机器 ID（可选，管理多机器时必填）
            session_type(string): session 类型（simple/worktree，默认 simple）
            yolo(boolean): 是否自动批准所有权限（默认 false）
            model_reasoning_effort(string): 仅 Codex 可选；留空表示继承 Codex 默认设置，可选 none/minimal/low/medium/high/xhigh
        '''
        # 获取机器列表
        try:
            machines = await session_ops.fetch_machines(self.client)
        except Exception as e:
            yield f"获取机器列表失败: {e}"
            return

        if not machines:
            yield "没有在线的机器"
            return

        agent = (agent or "").strip().lower()
        from .constants import AGENTS
        if agent not in AGENTS:
            yield f"不支持的 agent: {agent}，可选: {', '.join(AGENTS)}"
            return

        # 处理 machine_id
        if not machine_id:
            if len(machines) == 1:
                machine_id = machines[0].get("id")
            else:
                lines = ["有多个机器在线，请指定 machine_id:"]
                for m in machines:
                    mid = m.get("id", "?")
                    meta = m.get("metadata", {})
                    host = meta.get("host", "unknown")
                    plat = meta.get("platform", "?")
                    lines.append(f"  - {mid}: {host} ({plat})")
                yield "\n".join(lines)
                return

        normalized_effort = (model_reasoning_effort or "").strip().lower()
        if agent == "codex":
            from .constants import CODEX_REASONING_EFFORT_VALUES
            inherit_aliases = {"", "inherit", "default", "auto"}
            if normalized_effort in inherit_aliases:
                normalized_effort = ""
            elif normalized_effort not in CODEX_REASONING_EFFORT_VALUES:
                yield "Codex 的 model_reasoning_effort 只能是留空(继承默认配置)或 none/minimal/low/medium/high/xhigh"
                return
        elif normalized_effort:
            yield "只有 Codex 支持 model_reasoning_effort；其他代理请留空"
            return

        approval_payload = {"machine_id": machine_id, "directory": directory,
                            "agent": agent, "session_type": session_type, "yolo": yolo}
        if agent == "codex":
            approval_payload["model_reasoning_effort"] = normalized_effort or "inherit"

        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_create_session",
                                           approval_payload, event)
        if not approved:
            if reason == "timeout":
                yield "操作已超时取消，已直接通知用户。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作已被用户拒绝，请停止工具调用，先交流清楚问题"
            return

        # 执行创建
        logger.info(f"[tool_create_session] spawning session dir={directory} agent={agent}")
        ok, msg, sid = await session_ops.spawn_session(self.client, machine_id, directory, agent, session_type, yolo, model_reasoning_effort=normalized_effort or None)
        if ok and sid:
            await self.state_mgr.capture_window(sid, event.unified_msg_origin, agent)
            # F2: 异步抓取会话能力（不阻塞创建流程）
            asyncio.create_task(self._fetch_and_store_caps(sid))
            logger.debug(f"[tool_create_session] success sid={sid[:8]}")
            yield f"✅ 已创建 session: {sid[:8]}"
        else:
            logger.debug(f"[tool_create_session] failed: {msg}")
            yield f"创建失败: {msg}"

    async def tool_change_config(self, event: AstrMessageEvent, config_name: str, value: str):
        '''修改插件配置项。必须先调用 hapi_coding_get_config_status 查看可修改项。

        Args:
            config_name(string): 配置项名称
            value(string): 新值
        '''
        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_change_config",
                                           {"config_name": config_name, "value": value}, event)
        if not approved:
            if reason == "timeout":
                yield "操作已超时取消，已直接通知用户。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作已被用户拒绝，请停止工具调用，先交流清楚问题"
            return

        # 执行修改
        if config_name == "output_level":
            if value not in ["silence", "summary", "simple", "detail"]:
                yield "output_level 只能是 silence/summary/simple/detail"
                return
            self.plugin.sse_listener.output_level = value
            self.plugin.config["output_level"] = value
            self.plugin.config.save_config()
            yield f"✅ 已设置 {config_name} = {value}"
        elif config_name == "auto_approve_enabled":
            bool_val = value.lower() in ["true", "1", "yes", "on", "开启"]
            self.plugin.sse_listener._auto_approve_enabled = bool_val
            self.plugin.config["auto_approve_enabled"] = bool_val
            self.plugin.config.save_config()
            yield f"✅ 已设置 {config_name} = {bool_val}"
        elif config_name == "remind_pending":
            bool_val = value.lower() in ["true", "1", "yes", "on", "开启"]
            self.plugin.sse_listener._remind_enabled = bool_val
            self.plugin.config["remind_pending"] = bool_val
            self.plugin.config.save_config()
            yield f"✅ 已设置 {config_name} = {bool_val}"
        elif config_name == "quick_prefix":
            self.plugin._quick_prefix = value
            self.plugin.config["quick_prefix"] = value
            self.plugin.config.save_config()
            yield f"✅ 已设置 {config_name} = {value}"
        else:
            yield f"不支持的配置项: {config_name}，请先调用 hapi_coding_get_config_status 查看可用配置"

    async def tool_stop_message(self, event: AstrMessageEvent):
        '''停止当前 session 的消息生成。'''
        sid = self._effective_sid(event)
        if not sid:
            yield self._missing_session_text()
            return

        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_stop_message", {"session_id": sid[:8]}, event)
        if not approved:
            if reason == "timeout":
                yield "操作已超时取消，已直接通知用户。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作已被用户拒绝，请停止工具调用，先交流清楚问题"
            return

        # 执行停止
        ok, msg = await session_ops.abort_session(self.client, sid)
        if ok:
            await self.plugin._refresh_sessions()
        yield msg

    async def tool_execute_command(self, event: AstrMessageEvent, command: str):
        '''直接执行hapi相关指令。当用户希望执行hapi相关指令操作时，使用此工具，而不是使用默认的shell。使用前请务必调用 hapi_coding_list_commands 查看指令格式和参数说明，错误的指令可能导致不可预料的后果。

        Args:
            command(string): 完整的 /hapi 指令（不含 /hapi 前缀）
        '''
        # 请求审批
        approved, reason = await self._require_approval("hapi_coding_execute_command", {"command": command}, event)
        if not approved:
            if reason == "timeout":
                yield "操作已超时取消，已直接通知用户。"
            elif reason == "notification_failed":
                yield "操作失败：无法发送审批通知到用户。请检查是否已绑定 session。"
            else:
                yield "操作已被用户拒绝，请停止工具调用，先交流清楚问题"
            return

        # 执行命令
        results = []
        async for result in self.plugin.cmd_handlers.cmd_hapi_router(event, f"/hapi {command}"):
            await event.send(result)

            # 提取文本
            if hasattr(result, 'chain'):
                text_parts = []
                for seg in result.chain:
                    if hasattr(seg, 'text'):
                        text_parts.append(seg.text)
                if text_parts:
                    results.append("".join(text_parts))

        # 检测交互式命令
        cmd_name = command.strip().split()[0] if command.strip() else ""
        interactive_cmds = ['create', 'delete', 'rename', 'archive', 'perm', 'model', 'output', 'prune']

        if cmd_name in interactive_cmds:
            yield "这是一条交互式命令，用户已自行完成交互设置，你可以自行思考和查看操作结果"
        elif results:
            yield "\n\n".join(results)
        else:
            yield "命令执行完成"

