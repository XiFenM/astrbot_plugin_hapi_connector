"""LLM 决策系统：三种模式处理 Claude Code 的审批请求和提问。

模式：
- auto:    LLM 全自动决策，自动审批/回答，通知用户结果
- suggest: LLM 辅助决策，分析后给建议，用户最终决策
- off:     完全手动（不实例化此类）
"""

import json
from dataclasses import dataclass

from astrbot.api import logger

from . import approval_ops, session_ops
from .formatters import extract_text_preview, is_question_request, session_label_short


@dataclass
class DecisionResult:
    """决策结果"""

    handled: bool  # True = 已完全处理，跳过正常通知
    suggestion_text: str | None = None  # suggest 模式：追加到通知前


# ──── 高风险检测 ────

HIGH_RISK_PATTERNS = frozenset({
    "rm -rf", "rm -r", "rmdir",
    "git push --force", "git push -f", "git reset --hard",
    "drop table", "drop database", "truncate table",
    "sudo ", "chmod 777",
    "mkfs", "format c:",
    "--no-verify",
})

HIGH_RISK_TOOLS = frozenset({
    "DeleteFile", "RemoveDirectory",
})


def _is_high_risk(req: dict) -> bool:
    """检测请求是否涉及高风险操作（硬编码规则，不走 LLM）"""
    tool = req.get("tool", "")
    if tool in HIGH_RISK_TOOLS:
        return True
    args = req.get("arguments", {})
    args_str = json.dumps(args, ensure_ascii=False).lower() if isinstance(args, dict) else str(args).lower()
    return any(pattern in args_str for pattern in HIGH_RISK_PATTERNS)


class AutoDecisionManager:
    """管理 LLM 决策流程"""

    def __init__(self, plugin, mode: str = "auto"):
        self._plugin = plugin
        self.client = plugin.client
        self.mode = mode  # "auto" | "suggest"
        # 累积决策历史 {sid: [{"description": ..., "action": ..., "reasoning": ...}]}
        self._decision_history: dict[str, list[dict]] = {}

    # ════════════════════════════════════════
    # 公开入口
    # ════════════════════════════════════════

    async def try_auto_decide(self, sid: str, rid: str, req: dict) -> DecisionResult:
        """处理 AskUserQuestion 请求。

        Returns:
            DecisionResult — handled=True 表示已提交回答（auto 模式）
        """
        try:
            # 1. 构建上下文
            conversation_summary = await self._build_conversation_summary(sid)
            decision_history = self._get_decision_history(sid)

            # 2. 构建 prompt
            system_prompt = self._build_system_prompt_question()
            question_prompt = self._build_question_prompt(req, conversation_summary, decision_history)

            # 3. 调用 LLM
            response_text = await self._call_llm(system_prompt, question_prompt, sid)
            if not response_text:
                logger.warning("[AutoDecision] LLM 返回空响应 (sid=%s)", sid[:8])
                return DecisionResult(handled=False)

            # 4. 解析响应
            answers, reasoning, confidence = self._parse_question_response(response_text, req)

            # 5. 置信度检查
            threshold = self._plugin.config.get("auto_decision_confidence_threshold", 7)
            if answers is None or confidence < threshold:
                logger.info(
                    "[AutoDecision] 置信度不足: confidence=%s, threshold=%d (sid=%s)",
                    confidence, threshold, sid[:8],
                )
                if self.mode == "suggest" and reasoning:
                    suggestion = self._format_suggestion(
                        sid, req, "escalate", reasoning, confidence, is_question=True,
                        answers=answers,
                    )
                    return DecisionResult(handled=False, suggestion_text=suggestion)
                return DecisionResult(handled=False)

            # ── suggest 模式：只给建议，不提交 ──
            if self.mode == "suggest":
                suggestion = self._format_suggestion(
                    sid, req, "answer", reasoning, confidence, is_question=True,
                    answers=answers,
                )
                return DecisionResult(handled=False, suggestion_text=suggestion)

            # ── auto 模式：提交回答 ──
            success, msg = await approval_ops.answer_question(self.client, sid, rid, answers)
            if not success:
                logger.warning("[AutoDecision] 提交回答失败: %s (sid=%s)", msg, sid[:8])
                return DecisionResult(handled=False)

            # 记录决策
            args = req.get("arguments") or {}
            questions = args.get("questions", [])
            question_text = "; ".join(q.get("question", "") for q in questions)
            answer_text = "; ".join(
                f"Q{k}: {', '.join(v)}" for k, v in sorted(answers.items())
            )
            self._record_decision(sid, question_text, "answer", reasoning)

            # 通知用户
            await self._notify_user_question(sid, req, answers, reasoning)

            logger.info("[AutoDecision] 自动决策成功 (sid=%s, confidence=%d)", sid[:8], confidence)
            return DecisionResult(handled=True)

        except Exception as e:
            logger.warning("[AutoDecision] 问题决策异常: %s (sid=%s)", e, sid[:8])
            return DecisionResult(handled=False)

    async def try_auto_decide_approval(self, sid: str, rid: str, req: dict) -> DecisionResult:
        """处理工具权限审批请求。

        Returns:
            DecisionResult — handled=True 表示已提交审批结果（auto 模式）
        """
        try:
            tool = req.get("tool", "unknown")

            # 1. 高风险检测 — 直接上报
            if _is_high_risk(req):
                logger.info("[AutoDecision] 高风险操作，上报用户: %s (sid=%s)", tool, sid[:8])
                if self.mode == "suggest":
                    return DecisionResult(
                        handled=False,
                        suggestion_text=self._format_high_risk_warning(sid, req),
                    )
                return DecisionResult(handled=False)

            # 2. 构建上下文
            conversation_summary = await self._build_conversation_summary(sid)
            decision_history = self._get_decision_history(sid)

            # 3. 构建 prompt
            system_prompt = self._build_system_prompt_approval()
            approval_prompt = self._build_approval_prompt(req, conversation_summary, decision_history)

            # 4. 调用 LLM
            response_text = await self._call_llm(system_prompt, approval_prompt, sid)
            if not response_text:
                logger.warning("[AutoDecision] LLM 返回空响应 (sid=%s)", sid[:8])
                return DecisionResult(handled=False)

            # 5. 解析响应
            action, reasoning, confidence = self._parse_approval_response(response_text)

            # 6. 置信度检查
            threshold = self._plugin.config.get("auto_decision_confidence_threshold", 7)
            if confidence < threshold or action == "escalate":
                logger.info(
                    "[AutoDecision] 审批上报: action=%s, confidence=%s, threshold=%d (sid=%s)",
                    action, confidence, threshold, sid[:8],
                )
                if self.mode == "suggest":
                    suggestion = self._format_suggestion(
                        sid, req, action, reasoning, confidence, is_question=False,
                    )
                    return DecisionResult(handled=False, suggestion_text=suggestion)
                return DecisionResult(handled=False)

            # ── suggest 模式：只给建议 ──
            if self.mode == "suggest":
                suggestion = self._format_suggestion(
                    sid, req, action, reasoning, confidence, is_question=False,
                )
                return DecisionResult(handled=False, suggestion_text=suggestion)

            # ── auto 模式：执行审批 ──
            if action == "approve":
                ok, msg = await session_ops.approve_permission(self.client, sid, rid)
                if not ok:
                    logger.warning("[AutoDecision] 批准失败: %s (sid=%s)", msg, sid[:8])
                    return DecisionResult(handled=False)
                self._record_decision(sid, f"工具: {tool}", "approve", reasoning)
                await self._notify_user_approval(sid, req, "approve", reasoning)
                logger.info("[AutoDecision] 自动批准 %s (sid=%s, confidence=%d)", tool, sid[:8], confidence)
                return DecisionResult(handled=True)

            elif action == "deny":
                ok, msg = await session_ops.deny_permission(self.client, sid, rid)
                if not ok:
                    logger.warning("[AutoDecision] 拒绝失败: %s (sid=%s)", msg, sid[:8])
                    return DecisionResult(handled=False)
                self._record_decision(sid, f"工具: {tool}", "deny", reasoning)
                await self._notify_user_approval(sid, req, "deny", reasoning)
                logger.info("[AutoDecision] 自动拒绝 %s (sid=%s, confidence=%d)", tool, sid[:8], confidence)
                return DecisionResult(handled=True)

            return DecisionResult(handled=False)

        except Exception as e:
            logger.warning("[AutoDecision] 审批决策异常: %s (sid=%s)", e, sid[:8])
            return DecisionResult(handled=False)

    # ════════════════════════════════════════
    # 上下文构建
    # ════════════════════════════════════════

    async def _build_conversation_summary(self, sid: str) -> str:
        """从 hapi API 拉取最近消息，格式化为文本摘要。"""
        max_history = self._plugin.config.get("auto_decision_max_history", 30)
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=max_history)
        except Exception as e:
            logger.warning("[AutoDecision] 获取消息历史失败: %s", e)
            return "(无法获取对话历史)"

        if not messages:
            return "(无对话历史)"

        lines = []
        for msg in messages:
            content = msg.get("content", {})
            role = content.get("role", "unknown")
            text = extract_text_preview(content, max_len=500)
            if text is None:
                continue

            if role in ("user", "human"):
                lines.append(f"[User]: {text}")
            elif role in ("agent", "assistant"):
                lines.append(f"[Assistant]: {text}")
            else:
                lines.append(f"[{role}]: {text}")

        return "\n".join(lines) if lines else "(无可显示的对话历史)"

    def _get_decision_history(self, sid: str) -> list[dict]:
        return self._decision_history.get(sid, [])

    def _record_decision(self, sid: str, description: str, action: str, reasoning: str):
        """记录一次决策（上限 20 条/session）。"""
        if sid not in self._decision_history:
            self._decision_history[sid] = []
        self._decision_history[sid].append({
            "description": description,
            "action": action,
            "reasoning": reasoning,
        })
        if len(self._decision_history[sid]) > 20:
            self._decision_history[sid] = self._decision_history[sid][-20:]

    # ════════════════════════════════════════
    # LLM 调用
    # ════════════════════════════════════════

    async def _call_llm(self, system_prompt: str, prompt: str, sid: str) -> str | None:
        """通过官方 API 调用 LLM，带用户对话上下文。"""
        try:
            targets = self._plugin.state_mgr.select_notification_targets(
                sid, self._plugin.sessions_cache
            )
            umo = targets[0] if targets else None
            if not umo:
                logger.warning("[AutoDecision] 无可用通知目标")
                return None

            context = self._plugin.context
            provider_id = await context.get_current_chat_provider_id(umo=umo)
            if not provider_id:
                logger.warning("[AutoDecision] 未找到可用的 LLM provider")
                return None

            # 读取用户与机器人的对话上下文
            history_contexts = None
            try:
                conv_mgr = context.conversation_manager
                conv_id = await conv_mgr.get_curr_conversation_id(umo)
                if conv_id:
                    conversation = await conv_mgr.get_conversation(umo, conv_id)
                    if conversation and conversation.history:
                        history_contexts = json.loads(conversation.history)
            except Exception as e:
                logger.debug("[AutoDecision] 读取对话上下文失败（非致命）: %s", e)

            llm_resp = await context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=system_prompt,
                contexts=history_contexts,
            )
            return llm_resp.completion_text.strip() or None
        except Exception as e:
            logger.warning("[AutoDecision] LLM 调用失败: %s", e)
            return None

    # ════════════════════════════════════════
    # System Prompts
    # ════════════════════════════════════════

    def _build_system_prompt_question(self) -> str:
        return (
            "你是编程任务决策助手。根据用户的原始任务和 AI 编程助手的对话历史，替用户回答 AI 编程助手提出的问题。\n\n"
            "规则：\n"
            "1. 根据上下文选择最符合用户意图的选项\n"
            "2. 涉及安全、删除数据、生产环境部署等高风险操作 → 必须 ESCALATE\n"
            "3. 纯技术选择（框架、库、代码风格等） → 可以根据上下文自信决策\n"
            "4. 不确定用户意图 → 必须 ESCALATE\n\n"
            "严格按以下 JSON 格式回复，不要包含其他内容：\n"
            "```json\n"
            '{"action": "answer"|"escalate", "confidence": 1-10, '
            '"reasoning": "简短的决策理由", '
            '"answers": {"0": ["选项label或自定义文本"]}}\n'
            "```\n\n"
            'action 为 "escalate" 时 answers 可省略。\n'
            "confidence 表示确信程度：1=完全不确定，10=非常确定。"
        )

    def _build_system_prompt_approval(self) -> str:
        return (
            "你是编程任务安全审计助手。根据用户的原始任务和 AI 编程助手的对话历史，"
            "判断是否应该批准 AI 编程助手请求的工具使用权限。\n\n"
            "规则：\n"
            "1. 与当前任务直接相关的读取操作（Read, ListFiles, Grep, Glob 等） → 通常批准\n"
            "2. 与当前任务相关的写入操作（Write, Edit 等） → 根据上下文判断合理性\n"
            "3. 命令执行（Bash, Execute 等） → 仔细审查命令内容是否安全\n"
            "4. 涉及删除文件、rm -rf、生产环境部署、git push --force、"
            "修改系统配置等高风险操作 → 必须 ESCALATE\n"
            "5. 不确定操作是否安全或与任务无关 → 必须 ESCALATE\n\n"
            "严格按以下 JSON 格式回复，不要包含其他内容：\n"
            "```json\n"
            '{"action": "approve"|"deny"|"escalate", "confidence": 1-10, '
            '"reasoning": "简短的决策理由"}\n'
            "```\n\n"
            "confidence 表示确信程度：1=完全不确定，10=非常确定。"
        )

    # ════════════════════════════════════════
    # Prompt 构建
    # ════════════════════════════════════════

    def _build_question_prompt(self, req: dict, conversation_summary: str,
                               decision_history: list[dict]) -> str:
        args = req.get("arguments") or {}
        questions = args.get("questions", []) if isinstance(args, dict) else []

        q_lines = []
        for qi, q in enumerate(questions):
            q_lines.append(f"问题 {qi}:")
            if q.get("header"):
                q_lines.append(f"  标签: {q['header']}")
            if q.get("question"):
                q_lines.append(f"  问题: {q['question']}")
            opts = q.get("options", [])
            if opts:
                q_lines.append("  可选项:")
                for i, opt in enumerate(opts, 1):
                    desc = f" -- {opt['description']}" if opt.get("description") else ""
                    q_lines.append(f"    [{i}] {opt['label']}{desc}")
                q_lines.append("    也可以自定义输入文本作为回答")

        history_text = self._format_decision_history(decision_history)

        parts = ["=== 对话历史 ===", conversation_summary]
        if history_text:
            parts.extend(["", "=== 之前的决策记录 ===", history_text])
        parts.extend([
            "",
            "=== 当前需要回答的问题 ===",
            "\n".join(q_lines),
            "",
            "请根据以上上下文回答。answers 的 key 是问题序号字符串（从 \"0\" 开始），"
            "value 是列表，包含选中的选项 label 或自定义文本。"
            "如果问题提供了选项，优先从选项中选择，使用选项的 label 字段值。",
        ])
        return "\n".join(parts)

    def _build_approval_prompt(self, req: dict, conversation_summary: str,
                               decision_history: list[dict]) -> str:
        tool = req.get("tool", "unknown")
        args = req.get("arguments", {})
        args_str = json.dumps(args, ensure_ascii=False, indent=2) if isinstance(args, dict) else str(args)

        history_text = self._format_decision_history(decision_history)

        parts = ["=== 对话历史 ===", conversation_summary]
        if history_text:
            parts.extend(["", "=== 之前的决策记录 ===", history_text])
        parts.extend([
            "",
            "=== 当前需要审批的工具请求 ===",
            f"工具名称: {tool}",
            f"参数:\n{args_str}",
            "",
            "请判断是否应该批准此工具使用请求。",
        ])
        return "\n".join(parts)

    def _format_decision_history(self, decision_history: list[dict]) -> str:
        if not decision_history:
            return ""
        lines = ["之前的决策记录:"]
        for i, d in enumerate(decision_history, 1):
            lines.append(f"  {i}. {d['description']}")
            lines.append(f"     操作: {d['action']}")
            lines.append(f"     理由: {d['reasoning']}")
        return "\n".join(lines)

    # ════════════════════════════════════════
    # 响应解析
    # ════════════════════════════════════════

    def _extract_json(self, response_text: str) -> dict | None:
        """从 LLM 响应中提取 JSON 对象。"""
        text = response_text.strip()
        # 剥离 markdown code fence
        if "```json" in text:
            start = text.index("```json") + 7
            end_marker = text.find("```", start)
            text = text[start:end_marker].strip() if end_marker != -1 else text[start:].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end_marker = text.find("```", start)
            text = text[start:end_marker].strip() if end_marker != -1 else text[start:].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("[AutoDecision] JSON 解析失败: %s", text[:200])
            return None

    def _parse_question_response(
        self, response_text: str, req: dict
    ) -> tuple[dict[str, list[str]] | None, str, int]:
        """解析问题决策的 LLM 响应。

        Returns: (answers, reasoning, confidence)
        """
        data = self._extract_json(response_text)
        if not data:
            return None, "JSON parse error", 0

        action = data.get("action", "escalate")
        confidence = int(data.get("confidence", 0))
        reasoning = data.get("reasoning", "")

        if action == "escalate":
            return None, reasoning, confidence

        raw_answers = data.get("answers", {})
        if not isinstance(raw_answers, dict):
            return None, reasoning, 0

        args = req.get("arguments") or {}
        questions = args.get("questions", []) if isinstance(args, dict) else []

        answers: dict[str, list[str]] = {}
        for qi in range(len(questions)):
            key = str(qi)
            raw = raw_answers.get(key, [])
            if isinstance(raw, str):
                raw = [raw]
            if not isinstance(raw, list) or not raw:
                return None, f"缺少问题 {qi} 的回答", 0
            answers[key] = [str(item) for item in raw]

        return answers, reasoning, confidence

    def _parse_approval_response(self, response_text: str) -> tuple[str, str, int]:
        """解析审批决策的 LLM 响应。

        Returns: (action, reasoning, confidence)
            action: "approve" | "deny" | "escalate"
        """
        data = self._extract_json(response_text)
        if not data:
            return "escalate", "JSON parse error", 0

        action = data.get("action", "escalate")
        if action not in ("approve", "deny", "escalate"):
            action = "escalate"
        confidence = int(data.get("confidence", 0))
        reasoning = data.get("reasoning", "")
        return action, reasoning, confidence

    # ════════════════════════════════════════
    # 用户通知
    # ════════════════════════════════════════

    async def _notify_user_question(self, sid: str, req: dict,
                                    answers: dict[str, list[str]], reasoning: str):
        """通知用户自动回答的结果（auto 模式）。"""
        label = session_label_short(sid, self._plugin.sessions_cache)
        args = req.get("arguments") or {}
        questions = args.get("questions", []) if isinstance(args, dict) else []

        lines = [f"🤖 [LLM 自动决策] {label}"]
        for qi, q in enumerate(questions):
            q_text = q.get("question", "(未知问题)")
            lines.append("")
            lines.append(f"  ❓ {q_text}")
            answer_list = answers.get(str(qi), [])
            if answer_list:
                lines.append(f"  ✅ 回答: {', '.join(answer_list)}")
        if reasoning:
            lines.append("")
            lines.append(f"  💡 理由: {reasoning}")

        await self._plugin.sse_listener._push_notification("\n".join(lines), sid)

    async def _notify_user_approval(self, sid: str, req: dict, action: str, reasoning: str):
        """通知用户自动审批的结果（auto 模式）。"""
        label = session_label_short(sid, self._plugin.sessions_cache)
        tool = req.get("tool", "unknown")
        action_text = "✅ 已自动批准" if action == "approve" else "❌ 已自动拒绝"

        lines = [
            f"🤖 [LLM 自动决策] {label}",
            f"  🔧 工具: {tool}",
            f"  {action_text}",
        ]
        if reasoning:
            lines.append(f"  💡 理由: {reasoning}")

        await self._plugin.sse_listener._push_notification("\n".join(lines), sid)

    def _format_suggestion(self, sid: str, req: dict, action: str,
                           reasoning: str, confidence: int,
                           is_question: bool,
                           answers: dict[str, list[str]] | None = None) -> str:
        """格式化 suggest 模式的建议文本（追加到正常通知前）。"""
        label = session_label_short(sid, self._plugin.sessions_cache)
        type_label = "问题" if is_question else "权限请求"

        action_labels = {
            "approve": "建议批准 ✅",
            "deny": "建议拒绝 ❌",
            "answer": "建议回答",
            "escalate": "建议人工处理 ⚠️",
        }

        lines = [
            f"🤖 [LLM 分析] {label}",
            f"  📋 类型: {type_label}",
            f"  💡 建议: {action_labels.get(action, action)}",
            f"  📊 置信度: {confidence}/10",
        ]

        # 问题模式下展示建议的回答
        if is_question and answers and action == "answer":
            args = req.get("arguments") or {}
            questions = args.get("questions", []) if isinstance(args, dict) else []
            for qi, q in enumerate(questions):
                answer_list = answers.get(str(qi), [])
                if answer_list:
                    q_text = q.get("question", f"问题 {qi}")
                    lines.append(f"  📝 {q_text} → {', '.join(answer_list)}")

        if reasoning:
            lines.append(f"  💬 理由: {reasoning}")

        lines.append("")  # 空行分隔建议和原始通知
        return "\n".join(lines)

    def _format_high_risk_warning(self, sid: str, req: dict) -> str:
        """格式化高风险操作警告（suggest 模式）。"""
        label = session_label_short(sid, self._plugin.sessions_cache)
        tool = req.get("tool", "unknown")
        return (
            f"🤖 [LLM 分析] {label}\n"
            f"  ⚠️ 检测到高风险操作: {tool}\n"
            f"  💡 建议: 强烈建议人工审核\n"
        )
