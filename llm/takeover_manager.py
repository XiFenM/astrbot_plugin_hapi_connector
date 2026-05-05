"""Takeover 全盘接管模式 — LLM 自动规划并执行多步骤任务"""

import asyncio
import json
import time
import uuid

from astrbot.api import logger

from ..ops import session_ops
from . import takeover_prompts as prompts


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


def _create_task(data: dict, order: int = 0) -> dict:
    """从 LLM 输出创建任务 dict"""
    subtasks = []
    for i, st in enumerate(data.get("subtasks", []) or []):
        subtasks.append(_create_task(st, order=i))
    return {
        "id": _short_id(),
        "title": data.get("title", "未命名任务"),
        "description": data.get("description", ""),
        "status": "pending",
        "result_summary": None,
        "subtasks": subtasks,
        "order": order,
    }


def _find_next_pending(tasks: list[dict]) -> dict | None:
    """深度优先查找下一个 pending 任务"""
    for task in tasks:
        if task["status"] == "pending":
            # 如果有子任务，优先执行子任务
            if task["subtasks"]:
                sub = _find_next_pending(task["subtasks"])
                if sub:
                    return sub
                # 所有子任务已完成，跳过父任务本身（子任务完成即视为父任务完成）
                all_done = all(s["status"] in ("done", "skipped") for s in task["subtasks"])
                if all_done:
                    task["status"] = "done"
                    task["result_summary"] = "子任务全部完成"
                    continue
            return task
    return None


def _find_task_by_id(tasks: list[dict], task_id: str) -> dict | None:
    """递归查找任务"""
    for task in tasks:
        if task["id"] == task_id:
            return task
        found = _find_task_by_id(task["subtasks"], task_id)
        if found:
            return found
    return None


def _insert_after(tasks: list[dict], after_id: str, new_task: dict) -> bool:
    """在指定任务之后插入新任务（同级）"""
    for i, task in enumerate(tasks):
        if task["id"] == after_id:
            tasks.insert(i + 1, new_task)
            return True
        if _insert_after(task["subtasks"], after_id, new_task):
            return True
    return False


def _count_tasks(tasks: list[dict]) -> tuple[int, int]:
    """统计 (总数, 已完成数)"""
    total = done = 0
    for t in tasks:
        if t["subtasks"]:
            st, sd = _count_tasks(t["subtasks"])
            total += st
            done += sd
        else:
            total += 1
            if t["status"] in ("done", "skipped"):
                done += 1
    return total, done


def _format_plan_text(plan: dict, with_status: bool = True) -> str:
    """格式化计划为可读文本"""
    lines = []
    total, done = _count_tasks(plan["tasks"])
    pct = int(done / total * 100) if total > 0 else 0
    filled = pct // 10
    bar = "█" * filled + "░" * (10 - filled)

    lines.append(f"🎯 目标: {plan['goal']}")
    lines.append(f"📊 进度: [{done}/{total}] {bar} {pct}%")
    lines.append(f"📋 状态: {plan['status']}")
    lines.append("")

    def _fmt(tasks, indent=0):
        for t in tasks:
            prefix = "  " * indent
            if with_status:
                icons = {"pending": "⬜", "running": "🔄", "done": "✅",
                         "failed": "❌", "skipped": "⏭️"}
                icon = icons.get(t["status"], "⬜")
                lines.append(f"{prefix}{icon} {t['title']}")
                if t["result_summary"] and t["status"] in ("done", "failed"):
                    lines.append(f"{prefix}   └ {t['result_summary']}")
            else:
                lines.append(f"{prefix}• {t['title']}")
                if t["description"]:
                    lines.append(f"{prefix}  {t['description'][:100]}")
            if t["subtasks"]:
                _fmt(t["subtasks"], indent + 1)

    _fmt(plan["tasks"])
    return "\n".join(lines)


def _completed_summary(tasks: list[dict]) -> str:
    """生成已完成任务的摘要（用于构建下一个指令的上下文）"""
    parts = []
    for t in tasks:
        if t["status"] == "done":
            summary = t["result_summary"] or "已完成"
            parts.append(f"- {t['title']}: {summary}")
        if t["subtasks"]:
            sub = _completed_summary(t["subtasks"])
            if sub:
                parts.append(sub)
    return "\n".join(parts)


class TakeoverManager:
    """全盘接管管理器：规划、执行、评估、状态机"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.client = plugin.client
        self.state_mgr = plugin.state_mgr
        self._plans: dict[str, dict] = {}  # sid → plan dict
        self._max_tasks = plugin.config.get("takeover_max_tasks", 20)

    # ════════════════════════════════════════
    # 状态查询
    # ════════════════════════════════════════

    def is_active(self, sid: str) -> bool:
        plan = self._plans.get(sid)
        return plan is not None and plan["status"] == "executing"

    def get_plan(self, sid: str) -> dict | None:
        return self._plans.get(sid)

    def format_plan_status(self, plan: dict) -> str:
        return _format_plan_text(plan, with_status=True)

    # ════════════════════════════════════════
    # 规划阶段
    # ════════════════════════════════════════

    async def create_plan(self, sid: str, umo: str, goal: str) -> str:
        """调用 LLM 生成任务计划，返回格式化文本"""
        # 不允许同一 session 有多个计划
        existing = self._plans.get(sid)
        if existing and existing["status"] in ("executing", "paused"):
            return (f"❌ 当前 session 已有活跃计划（状态: {existing['status']}）。\n"
                    "请先取消（cancel）或等待完成后再创建新计划。")

        # 获取上下文
        session_context = await self._get_session_context(sid)
        playbook = self._get_playbook(sid)

        system = prompts.PLAN_GENERATION_SYSTEM.format(max_tasks=self._max_tasks)
        user = prompts.PLAN_GENERATION_USER.format(
            goal=goal, session_context=session_context, playbook=playbook or "（无）")

        resp = await self._call_llm(system, user, umo=umo)
        if not resp:
            return "❌ LLM 生成计划失败，请稍后重试"

        tasks_data = self._parse_plan_json(resp)
        if not tasks_data:
            return f"❌ LLM 返回的计划格式无法解析:\n{resp[:300]}"

        tasks = [_create_task(t, order=i) for i, t in enumerate(tasks_data)]
        plan = {
            "id": f"plan_{_short_id()}",
            "sid": sid,
            "umo": umo,
            "goal": goal,
            "status": "confirming",
            "tasks": tasks,
            "current_task_id": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self._plans[sid] = plan
        await self._persist(sid)

        text = _format_plan_text(plan, with_status=False)
        return (f"📋 已生成任务计划：\n\n{text}\n\n"
                "如需修改，请说明修改意见；确认无误后，请说「开始执行」。")

    async def modify_plan(self, sid: str, umo: str, feedback: str) -> str:
        """根据用户反馈修改计划"""
        plan = self._plans.get(sid)
        if not plan:
            return "❌ 当前无计划可修改"
        if plan["status"] not in ("confirming", "paused"):
            return f"❌ 当前计划状态为 {plan['status']}，无法修改（需先暂停）"

        current_plan_text = _format_plan_text(plan, with_status=False)
        system = prompts.PLAN_MODIFICATION_SYSTEM.format(max_tasks=self._max_tasks)
        user = prompts.PLAN_MODIFICATION_USER.format(
            goal=plan["goal"], current_plan=current_plan_text, feedback=feedback)

        resp = await self._call_llm(system, user, umo=umo)
        if not resp:
            return "❌ LLM 修改计划失败"

        tasks_data = self._parse_plan_json(resp)
        if not tasks_data:
            return f"❌ LLM 返回的计划格式无法解析:\n{resp[:300]}"

        plan["tasks"] = [_create_task(t, order=i) for i, t in enumerate(tasks_data)]
        plan["updated_at"] = time.time()
        await self._persist(sid)

        text = _format_plan_text(plan, with_status=False)
        return (f"📋 已更新任务计划：\n\n{text}\n\n"
                "如需继续修改，请说明意见；确认无误后，请说「开始执行」。")

    # ════════════════════════════════════════
    # 执行控制
    # ════════════════════════════════════════

    async def control(self, sid: str, action: str) -> str:
        """统一控制入口：start / pause / resume / cancel"""
        plan = self._plans.get(sid)
        if not plan:
            return "❌ 当前无活跃计划"

        if action == "start":
            return await self._start(sid, plan)
        elif action == "pause":
            return await self._pause(sid, plan)
        elif action == "resume":
            return await self._resume(sid, plan)
        elif action == "cancel":
            return await self._cancel(sid, plan)
        else:
            return f"❌ 未知操作: {action}，可用: start / pause / resume / cancel"

    async def _start(self, sid: str, plan: dict) -> str:
        if plan["status"] != "confirming":
            return f"❌ 计划状态为 {plan['status']}，只有 confirming 状态可以开始"
        plan["status"] = "executing"
        plan["updated_at"] = time.time()
        await self._persist(sid)
        logger.info("[takeover] starting plan for sid=%s, tasks=%d", sid[:8], len(plan["tasks"]))
        # 异步启动执行，不阻塞工具返回
        asyncio.create_task(self._execute_next_task(sid))
        total, _ = _count_tasks(plan["tasks"])
        return f"▶️ 计划已开始执行！共 {total} 个任务，进度会自动推送。"

    async def _pause(self, sid: str, plan: dict) -> str:
        if plan["status"] != "executing":
            return f"❌ 计划状态为 {plan['status']}，只有 executing 状态可以暂停"
        plan["status"] = "paused"
        plan["updated_at"] = time.time()
        await self._persist(sid)
        logger.info("[takeover] paused plan for sid=%s", sid[:8])
        return "⏸️ 计划已暂停。当前运行中的任务会完成，但不会自动推进下一个。\n使用 resume 恢复执行。"

    async def _resume(self, sid: str, plan: dict) -> str:
        if plan["status"] != "paused":
            return f"❌ 计划状态为 {plan['status']}，只有 paused 状态可以恢复"
        plan["status"] = "executing"
        plan["updated_at"] = time.time()
        await self._persist(sid)
        logger.info("[takeover] resuming plan for sid=%s", sid[:8])
        # 如果有 running 的任务（断点恢复），等它完成；否则执行下一个
        current = _find_task_by_id(plan["tasks"], plan.get("current_task_id", ""))
        if current and current["status"] == "running":
            return "▶️ 计划已恢复。当前任务仍在运行中，完成后会自动推进。"
        asyncio.create_task(self._execute_next_task(sid))
        return "▶️ 计划已恢复执行！"

    async def _cancel(self, sid: str, plan: dict) -> str:
        if plan["status"] in ("completed", "cancelled"):
            return f"计划已是 {plan['status']} 状态"
        plan["status"] = "cancelled"
        plan["updated_at"] = time.time()
        await self._persist(sid)
        logger.info("[takeover] cancelled plan for sid=%s", sid[:8])
        return "🛑 计划已取消。当前运行中的 HAPI 任务不会被中断。"

    # ════════════════════════════════════════
    # 执行循环（核心）
    # ════════════════════════════════════════

    async def _execute_next_task(self, sid: str):
        """找到下一个 pending 任务，构建指令，发送给 HAPI"""
        plan = self._plans.get(sid)
        if not plan or plan["status"] != "executing":
            return

        task = _find_next_pending(plan["tasks"])
        if not task:
            # 所有任务完成
            plan["status"] = "completed"
            plan["updated_at"] = time.time()
            await self._persist(sid)
            progress = _format_plan_text(plan, with_status=True)
            await self._notify_user(sid, f"🎉 所有任务已完成！\n\n{progress}")
            return

        # ��建具体指令
        instruction = await self._build_instruction(sid, plan, task)
        if not instruction:
            plan["status"] = "paused"
            await self._persist(sid)
            await self._notify_user(sid, f"❌ 构建任务指令失败，计划已暂停。\n任务: {task['title']}")
            return

        # 记录发送前序号
        sse = self.plugin.sse_listener
        async with sse._lock:
            pre_send_seq = sse.session_states.get(sid, {}).get("lastSeq", 0)

        # 发送给 HAPI
        ok, _ = await session_ops.send_message(self.client, sid, instruction)
        if not ok:
            plan["status"] = "paused"
            await self._persist(sid)
            await self._notify_user(sid, f"❌ 任务发送失败，计划已暂停。\n任务: {task['title']}")
            return

        # 注册完成回调（独立字典）
        sse._pending_takeover_completions[sid] = {
            "pre_send_seq": pre_send_seq,
            "ts": time.monotonic(),
            "task_id": task["id"],
        }

        # 更新状态
        task["status"] = "running"
        plan["current_task_id"] = task["id"]
        plan["updated_at"] = time.time()
        await self._persist(sid)

        # 通知用户
        total, done = _count_tasks(plan["tasks"])
        pct = int(done / total * 100) if total > 0 else 0
        preview = instruction[:200] + ("…" if len(instruction) > 200 else "")
        await self._notify_user(
            sid,
            f"▶️ [{done + 1}/{total}] 正在执行: {task['title']}\n\n"
            f"📝 发送指令:\n> {preview}")

    async def on_task_completed(self, sid: str, response: str):
        """HAPI 任务完成后的评估循环（由 sse_listener 调用）"""
        plan = self._plans.get(sid)
        if not plan:
            return

        task_id = plan.get("current_task_id")
        task = _find_task_by_id(plan["tasks"], task_id) if task_id else None
        if not task:
            logger.warning("[takeover] completed but task not found: %s", task_id)
            return

        # 调用 LLM 评估
        evaluation = await self._evaluate_task(sid, plan, task, response)

        # 更新��务状态
        task["status"] = evaluation.get("task_status", "done")
        task["result_summary"] = evaluation.get("task_summary", "")
        plan["updated_at"] = time.time()

        # 通知用户
        icons = {"done": "✅", "failed": "❌", "partial": "⚠️"}
        icon = icons.get(task["status"], "📋")
        await self._notify_user(
            sid,
            f"{icon} 任务完成: {task['title']}\n"
            f"  结果: {task['result_summary']}\n"
            f"  判断: {evaluation.get('reasoning', '')}")

        # 根据评估决定下一步
        next_action = evaluation.get("next_action", "continue")

        if evaluation.get("goal_achieved"):
            plan["status"] = "completed"
            await self._persist(sid)
            progress = _format_plan_text(plan, with_status=True)
            await self._notify_user(sid, f"🎉 目标已达成！\n\n{progress}")
            return

        if next_action == "insert_task" and evaluation.get("inserted_task"):
            new_task = _create_task(evaluation["inserted_task"])
            _insert_after(plan["tasks"], task["id"], new_task)
            await self._notify_user(sid, f"📋 插入临时任务: {new_task['title']}")

        if next_action == "retry":
            task["status"] = "pending"

        if next_action == "complete":
            plan["status"] = "completed"
            await self._persist(sid)
            progress = _format_plan_text(plan, with_status=True)
            await self._notify_user(sid, f"🎉 计划执行完毕！\n\n{progress}")
            return

        # 继续���行（如果未被暂停/取消）
        if plan["status"] == "executing":
            await self._persist(sid)
            await self._execute_next_task(sid)
        else:
            await self._persist(sid)
            logger.info("[takeover] plan no longer executing (status=%s), stopping loop", plan["status"])

    async def on_compaction_completed(self, sid: str):
        """压缩完成后恢复当前任务（由 sse_listener 调用）"""
        plan = self._plans.get(sid)
        if not plan or plan["status"] != "executing":
            return

        task = _find_task_by_id(plan["tasks"], plan.get("current_task_id", ""))
        if not task or task["status"] != "running":
            return

        # 构建恢复指令
        resume = await self._build_resume_instruction(sid, plan, task)
        if not resume:
            await self._notify_user(sid, f"⚠️ 压缩后恢复指令生成失败，请手动恢复。\n当前任务: {task['title']}")
            return

        sse = self.plugin.sse_listener
        async with sse._lock:
            pre_send_seq = sse.session_states.get(sid, {}).get("lastSeq", 0)

        ok, _ = await session_ops.send_message(self.client, sid, resume)
        if ok:
            sse._pending_takeover_completions[sid] = {
                "pre_send_seq": pre_send_seq,
                "ts": time.monotonic(),
                "task_id": task["id"],
            }

        umo = plan.get("umo")
        if umo:
            preview = resume[:150] + ("…" if len(resume) > 150 else "")
            await sse._send_user_message(
                umo,
                f"上下文压缩完成，已恢复当前任务: {task['title']}\n> {preview}")

    # ════════════════════════════════════════
    # 持久化 & 恢复
    # ════════════════════════════════════════

    async def _persist(self, sid: str):
        self.state_mgr.set_takeover_plan(sid, self._plans.get(sid))
        await self.state_mgr.persist_takeover_plan(sid)

    def recover_from_restart(self):
        """启动恢复：从 state_mgr 加载计划，executing → paused"""
        loaded = self.state_mgr.get_all_takeover_plans()
        for sid, plan in loaded.items():
            self._plans[sid] = plan
            if plan["status"] == "executing":
                plan["status"] = "paused"
                task = _find_task_by_id(plan["tasks"], plan.get("current_task_id", ""))
                if task and task["status"] == "running":
                    task["status"] = "pending"
                logger.info("[takeover] recovered plan sid=%s, downgraded to paused", sid[:8])

    # ════════════════════════════════════════
    # LLM 调用
    # ════════════════════════════════════════

    async def _call_llm(self, system_prompt: str, user_prompt: str,
                        sid: str = "", umo: str = "") -> str | None:
        """调用 LLM，复用 auto_decision 的模式"""
        try:
            if not umo and sid:
                targets = self.plugin.state_mgr.select_notification_targets(
                    sid, self.plugin.sessions_cache)
                umo = targets[0] if targets else None
            if not umo:
                return None

            context = self.plugin.context
            provider_id = await context.get_current_chat_provider_id(umo=umo)
            if not provider_id:
                return None

            llm_resp = await context.llm_generate(
                chat_provider_id=provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
            )
            return llm_resp.completion_text.strip() or None
        except Exception as e:
            logger.warning("[takeover] LLM call failed: %s", e)
            return None

    async def _call_llm_json(self, system_prompt: str, user_prompt: str,
                             sid: str = "", umo: str = "") -> str | None:
        """调用 LLM 并强制 JSON 输出"""
        try:
            if not umo and sid:
                targets = self.plugin.state_mgr.select_notification_targets(
                    sid, self.plugin.sessions_cache)
                umo = targets[0] if targets else None
            if not umo:
                return None

            context = self.plugin.context
            provider_id = await context.get_current_chat_provider_id(umo=umo)
            if not provider_id:
                return None

            llm_resp = await context.llm_generate(
                chat_provider_id=provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
                response_format={"type": "json_object"},
            )
            return llm_resp.completion_text.strip() or None
        except Exception as e:
            logger.warning("[takeover] LLM JSON call failed: %s", e)
            return None

    # ════════════════════════════════════════
    # 指令构建 & 评估
    # ════════════════════════════════════════

    async def _build_instruction(self, sid: str, plan: dict, task: dict) -> str | None:
        completed = _completed_summary(plan["tasks"])
        user = prompts.INSTRUCTION_USER.format(
            goal=plan["goal"],
            completed_summary=completed or "（尚无已完成任务）",
            task_title=task["title"],
            task_description=task["description"])
        return await self._call_llm(prompts.INSTRUCTION_SYSTEM, user, sid=sid)

    async def _evaluate_task(self, sid: str, plan: dict, task: dict, response: str) -> dict:
        plan_summary = _format_plan_text(plan, with_status=True)
        user = prompts.EVALUATION_USER.format(
            goal=plan["goal"],
            plan_summary=plan_summary,
            task_title=task["title"],
            task_description=task["description"],
            response=response[:3000])
        resp = await self._call_llm_json(prompts.EVALUATION_SYSTEM, user, sid=sid)
        if resp:
            parsed = self._parse_json(resp)
            if parsed:
                return parsed
        # 解析失败时的安全默认值
        logger.warning("[takeover] evaluation parse failed, defaulting to done+continue")
        return {
            "task_status": "done",
            "task_summary": "（评估失败，默认标记完成）",
            "goal_achieved": False,
            "next_action": "continue",
            "reasoning": "LLM 评估结果解析失败",
        }

    async def _build_resume_instruction(self, sid: str, plan: dict, task: dict) -> str | None:
        completed = _completed_summary(plan["tasks"])
        user = prompts.RESUME_USER.format(
            goal=plan["goal"],
            task_title=task["title"],
            task_description=task["description"],
            completed_summary=completed or "（尚无已完成任务）")
        return await self._call_llm(prompts.RESUME_SYSTEM, user, sid=sid)

    # ════════════════════════════════════════
    # 上下文获取
    # ════════════════════════════════════════

    async def _get_session_context(self, sid: str) -> str:
        """获取 session 的简要上下文"""
        try:
            messages = await session_ops.fetch_messages(self.client, sid, limit=10)
            from ..ui.formatters import extract_text_preview
            lines = []
            for msg in messages[-5:]:
                content = msg.get("content", {})
                role = content.get("role", "?")
                text = extract_text_preview(content, max_len=100)
                if text:
                    lines.append(f"[{role}] {text}")
            return "\n".join(lines) if lines else "（无最近消息）"
        except Exception:
            return "（获取上下文失败）"

    def _get_playbook(self, sid: str) -> str | None:
        """获取 session 关联的 playbook"""
        session = next((s for s in self.plugin.sessions_cache if s.get("id") == sid), None)
        if not session:
            return None
        work_dir = session.get("metadata", {}).get("path", "")
        machine_id = session.get("machineId", "") or session.get("metadata", {}).get("machineId", "")
        if not work_dir or not machine_id:
            return None
        key = f"{machine_id}:{work_dir}"
        return self.state_mgr.get_playbook(key)

    # ════════════════════════════════════════
    # 通知
    # ════════════════════════════════════════

    async def _notify_user(self, sid: str, text: str):
        """以常规 AstrBot 消息通知用户"""
        plan = self._plans.get(sid)
        umo = plan.get("umo") if plan else None
        if not umo:
            targets = self.plugin.state_mgr.select_notification_targets(
                sid, self.plugin.sessions_cache)
            umo = targets[0] if targets else None
        if umo:
            await self.plugin.sse_listener._send_user_message(umo, text)

    # ════════════════════════════════════════
    # JSON 解析
    # ════════════════════════════════════════

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """从 LLM 响应提取 JSON"""
        text = text.strip()
        # 去 markdown 代码块
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.find("```", start)
            text = text[start:end].strip() if end != -1 else text[start:].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.find("```", start)
            text = text[start:end].strip() if end != -1 else text[start:].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 花括号提取
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass
        return None

    def _parse_plan_json(self, text: str) -> list[dict] | None:
        """从 LLM 响应解析任务列表"""
        parsed = self._parse_json(text)
        if not parsed:
            return None
        tasks = parsed.get("tasks")
        if isinstance(tasks, list) and tasks:
            return tasks
        return None
