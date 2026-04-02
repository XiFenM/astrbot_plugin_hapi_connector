"""通知推送和去重管理"""

import html as html_lib
import time
from astrbot.api.event import MessageChain
from astrbot.api import logger


class NotificationManager:
    """处理 SSE 事件通知的推送和去重"""

    def __init__(self, context, state_mgr):
        self.context = context
        self.state_mgr = state_mgr
        self._recent_notifications: dict[tuple[str, str, str], float] = {}
        self._event_cache: dict[str, any] = {}

    @staticmethod
    def notification_body_key(text: str) -> str:
        """Normalize label variants so duplicate notifications collapse to one body."""
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].startswith("💬 ") and lines[1].startswith("📂 ") and lines[2].startswith("🤖 "):
            lines = lines[3:]
        elif lines and lines[0].startswith("🏷️ "):
            lines = lines[1:]
        return "\n".join(line.rstrip() for line in lines).strip() or text.strip()

    @staticmethod
    def is_request_notification(text: str) -> bool:
        return "待审批" in text and ("/hapi a" in text or "/hapi answer" in text)

    def should_skip_duplicate(self, umo: str, session_id: str, text: str) -> bool:
        """Drop short-interval duplicate notifications for the same target/session/body."""
        if self.is_request_notification(text):
            return False

        now = time.monotonic()
        dedupe_window = 2.5
        expire_before = now - 30
        for key, ts in list(self._recent_notifications.items()):
            if ts < expire_before:
                self._recent_notifications.pop(key, None)

        body_key = self.notification_body_key(text)
        cache_key = (umo, session_id or "", body_key)
        last_sent = self._recent_notifications.get(cache_key)
        if last_sent is not None and now - last_sent <= dedupe_window:
            logger.info("跳过重复通知: sid=%s umo=%s", (session_id or "global")[:8], umo[:20])
            return True

        self._recent_notifications[cache_key] = now
        return False

    @staticmethod
    def split_message(text: str, max_len: int = 4200) -> list[str]:
        """按行边界将长消息分片"""
        chunks = []
        current = ""
        for line in text.split("\n"):
            if current and len(current) + 1 + len(line) > max_len:
                chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _to_telegram_html(text: str, use_expandable: bool = True) -> str:
        """将通知文本转换为 Telegram HTML 格式，包含来源标注和 expandable blockquote。

        Args:
            use_expandable: True 时正文放入可折叠 blockquote；False 时正文直接展示（审批通知需保持可见）。
        """
        escaped = html_lib.escape(text)
        body = escaped.replace("\n", "\n")  # HTML 中换行保持原样
        if use_expandable:
            return f"📡 <b>来自 Claude Code</b>\n<blockquote expandable>{body}</blockquote>"
        else:
            return f"📡 <b>来自 Claude Code</b>\n{body}"

    async def push_notification(self, text: str, session_id: str, sessions_cache: list[dict],
                                approval_indices: list[int] | None = None):
        """推送通知到单个目标窗口，优先走 session 当前路由。

        Args:
            approval_indices: 待审批请求的序号列表，非空时在 Telegram 平台追加内联审批按钮。
        """
        targets = self.state_mgr.select_notification_targets(session_id, sessions_cache)

        if targets:
            for umo in targets:
                if self.should_skip_duplicate(umo, session_id, text):
                    continue
                chunks = self.split_message(text) if len(text) > 4200 else [text]
                is_telegram = umo.startswith("telegram")
                is_discord = umo.startswith("discord")

                for i, chunk in enumerate(chunks):
                    is_last = i == len(chunks) - 1

                    if is_telegram:
                        # Telegram：HTML expandable blockquote 区分 SSE 通知
                        # 审批通知不折叠（需要用户立即看到内容以进行审批操作）
                        use_expandable = not bool(approval_indices)
                        html_content = self._to_telegram_html(chunk, use_expandable=use_expandable)
                        chain = MessageChain()
                        try:
                            from astrbot.core.platform.sources.telegram.tg_event import (
                                TelegramHTMLText, TelegramInlineKeyboard,
                            )
                            chain.chain.append(TelegramHTMLText(html=html_content))
                            if is_last and approval_indices:
                                buttons = []
                                for idx in approval_indices:
                                    buttons.append([
                                        (f"✅ 批准 #{idx}", f"hapi_approve:{idx}"),
                                        (f"❌ 拒绝 #{idx}", f"hapi_deny:{idx}"),
                                    ])
                                buttons.append([
                                    ("✅ 全部批准", "hapi_approve:all"),
                                    ("❌ 全部拒绝", "hapi_deny:all"),
                                ])
                                chain.chain.append(TelegramInlineKeyboard(buttons=buttons))
                        except Exception as e:
                            logger.warning("构建 Telegram HTML 通知失败，回退纯文本: %s", e)
                            chain = MessageChain().message(chunk)
                    elif is_discord:
                        # Discord：使用 Embed + 按钮区分 SSE 通知
                        chain = MessageChain()
                        try:
                            from astrbot.core.platform.sources.discord.components import (
                                DiscordEmbed, DiscordButton, DiscordView,
                            )
                            # description 上限 4096，超长时截断并提示
                            desc = chunk
                            if len(desc) > 4096:
                                desc = desc[:4080] + "\n…（内容过长已截断）"
                            embed = DiscordEmbed(
                                title="📡 来自 Claude Code",
                                description=desc,
                                color=0x7C3AED,  # 紫色侧边栏
                            )
                            chain.chain.append(embed)
                            # 审批按钮
                            if is_last and approval_indices:
                                buttons = []
                                for idx in approval_indices:
                                    buttons.append(DiscordButton(
                                        label=f"✅ 批准 #{idx}",
                                        custom_id=f"hapi_approve:{idx}",
                                        style="success",
                                    ))
                                    buttons.append(DiscordButton(
                                        label=f"❌ 拒绝 #{idx}",
                                        custom_id=f"hapi_deny:{idx}",
                                        style="danger",
                                    ))
                                buttons.append(DiscordButton(
                                    label="✅ 全部批准",
                                    custom_id="hapi_approve:all",
                                    style="success",
                                ))
                                buttons.append(DiscordButton(
                                    label="❌ 全部拒绝",
                                    custom_id="hapi_deny:all",
                                    style="danger",
                                ))
                                chain.chain.append(DiscordView(components=buttons))
                        except Exception as e:
                            logger.warning("构建 Discord Embed 通知失败，回退纯文本: %s", e)
                            chain = MessageChain().message(chunk)
                    else:
                        chain = MessageChain().message(chunk)

                    try:
                        await self.context.send_message(umo, chain)
                    except Exception:
                        cached_event = self._event_cache.get(umo)
                        if cached_event:
                            try:
                                await cached_event.send(chain)
                            except Exception as e:
                                logger.warning("推送到窗口失败 (umo=%s): %s", umo[:20], e)
                                break
                        else:
                            break
                        break
            return

        if session_id:
            sess = next((s for s in sessions_cache if s["id"] == session_id), None)
            flavor = sess.get("metadata", {}).get("flavor", "unknown") if sess else "unknown"
            logger.error("Session %s [%s] 无绑定窗口且无默认窗口，推送失败", session_id[:8], flavor)
        else:
            logger.error("全局通知无可用默认窗口，推送失败")
