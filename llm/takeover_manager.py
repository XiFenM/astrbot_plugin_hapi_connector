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
    """格式化计划为可读文本。

    显示规则：
    - 父任务（有子任务）：渲染为分组标题 📂/📁，附带 (已完成数/总数)；不计入进度条
    - 叶子任务：用 ⬜🔄✅❌⏭️ 表示状态，计入进度条 [N/M]
    这样进度条 [N/M] 与可见的勾选项数一一对应。
    """
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
            if t["subtasks"]:
                # 父任务：分组标题（不计入 [N/M]）
                sub_total, sub_done = _count_tasks(t["subtasks"])
                folder_icon = "📁" if sub_done == sub_total else "📂"
                lines.append(
                    f"{prefix}{folder_icon} {t['title']} ({sub_done}/{sub_total})")
                if not with_status and t.get("description"):
                    lines.append(f"{prefix}  {t['description'][:100]}")
                _fmt(t["subtasks"], indent + 1)
            else:
                # 叶子任务
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

    _fmt(plan["tasks"])
    return "\n".join(lines)


def _format_response_for_evaluation(messages: list[dict],
                                     max_total: int = 8000) -> str:
    """把 HAPI 拉回的 raw 消息列表精简为 LLM 评估用的紧凑文本。

    取舍策略（针对"是否完成任务"的评估场景）：
    - 保留：模型 text 块（"做了什么/完成了什么"的话语）、final summary、event message
    - 保留：工具调用动作摘要（执行了什么命令/编辑了哪些文件/读了哪些文件），
            但不含具体内容（Edit 的 old/new、Write 的 content 都丢）
    - 丢弃：thinking / reasoning / agent_reasoning / token_count 等噪音
    - 丢弃：tool_result（工具执行的具体输出，对评估"是否完成"不必要）

    safety cap: format 后仍超 max_total，截首尾保留。
    """
    parts: list[str] = []
    counter = {"edit": 0}  # Edit/MultiEdit/Write 累计序号，供 inspect_edits 引用
    for msg in sorted(messages, key=lambda m: m.get("seq", 0)):
        inner = msg.get("content", {}).get("content")
        if isinstance(inner, str):
            if inner.strip():
                parts.append(inner.strip())
        elif isinstance(inner, list):
            for block in inner:
                t = _extract_block_for_eval(block, counter)
                if t:
                    parts.append(t)
        elif isinstance(inner, dict):
            t = _extract_block_for_eval(inner, counter)
            if t:
                parts.append(t)

    if not parts:
        return "（无可解析的回应内容）"

    joined = "\n".join(parts)

    # safety cap：极端长任务（百次工具调用）兜底裁剪
    if len(joined) > max_total:
        head = max_total // 2
        tail = max_total - head
        omitted = len(joined) - head - tail
        joined = (f"{joined[:head]}\n\n... [中间省略 {omitted} 字] ...\n\n"
                  f"{joined[-tail:]}")

    return joined


def _extract_block_for_eval(block: dict, counter: dict | None = None) -> str | None:
    """从单个 block 提取评估用文本。返回 None 表示丢弃。

    counter: 可变 dict，用于跨调用累计 Edit/Write/MultiEdit 编号。
             形如 {"edit": int}。caller 不传则不编号（独立调用场景）。
    """
    if not isinstance(block, dict):
        return None
    btype = block.get("type", "")

    # 模型文本：保留全部
    if btype == "text":
        text = (block.get("text") or "").strip()
        return text or None

    # 工具调用：只保留动作摘要，不含具体内容
    if btype in ("tool_use", "tool-call"):
        return _format_tool_call_for_eval(block, counter)

    # final summary
    if btype == "summary":
        text = block.get("summary") or ""
        return f"[Summary] {text[:500]}" if text else None

    # 系统事件（如 compaction completed）：保留 message 类
    if btype == "event":
        ev = block.get("data", {})
        if isinstance(ev, dict) and ev.get("type") == "message":
            msg = ev.get("message", "")
            if msg:
                return f"[System] {msg[:200]}"
        return None

    # 包装类型递归
    if btype in ("output", "input", "codex"):
        data = block.get("data")
        if isinstance(data, dict):
            return _extract_block_for_eval(data, counter)
        if isinstance(data, list):
            sub = [_extract_block_for_eval(b, counter) for b in data]
            sub_clean = [s for s in sub if s]
            return "\n".join(sub_clean) if sub_clean else None
        return None

    # 其他全部丢：thinking / reasoning / tool_result / token_count / ready / ...
    return None


def _format_tool_call_for_eval(block: dict, counter: dict | None = None) -> str:
    """工具调用 → 一行动作摘要，不含具体内容。
    Edit/MultiEdit/Write 会带 [E#] 前缀，供 LLM 评估时通过 inspect_edits 引用。
    """
    name = block.get("name", "?")
    inp = block.get("input", {}) or {}
    if not isinstance(inp, dict):
        return f"🛠️ {name}"

    if name == "Bash":
        cmd = inp.get("command", "")
        if isinstance(cmd, str) and cmd:
            cmd_clean = cmd.replace("\n", " ").strip()[:200]
            return f"🛠️ Bash: {cmd_clean}"
        return "🛠️ Bash"

    if name in ("Edit", "MultiEdit", "Write"):
        fp = inp.get("file_path", "?")
        base = fp.rsplit("/", 1)[-1] if "/" in fp else fp
        if counter is not None:
            counter["edit"] = counter.get("edit", 0) + 1
            return f"🛠️ [E{counter['edit']}] {name}: {base}"
        return f"🛠️ {name}: {base}"

    if name == "Read":
        fp = inp.get("file_path", "?")
        base = fp.rsplit("/", 1)[-1] if "/" in fp else fp
        return f"🛠️ Read: {base}"

    if name in ("Grep", "Glob"):
        pat = inp.get("pattern", "?")
        path = inp.get("path", "")
        return f"🛠️ {name}: /{pat}/{' in ' + path if path else ''}"

    if name == "Agent":
        desc = inp.get("description") or inp.get("prompt") or ""
        return f"🛠️ Agent: {str(desc)[:80]}"

    if name == "TodoWrite":
        todos = inp.get("todos") or []
        done = sum(1 for t in todos if isinstance(t, dict)
                   and t.get("status") == "completed")
        return f"🛠️ TodoWrite ({done}/{len(todos)} 完成)"

    return f"🛠️ {name}"


def _iter_edit_blocks(messages: list[dict]):
    """按 _format_response_for_evaluation 同样的顺序产出 Edit/MultiEdit/Write 块。
    yield (index, name, input_dict)，index 从 1 开始。
    """
    idx = 0
    for msg in sorted(messages, key=lambda m: m.get("seq", 0)):
        inner = msg.get("content", {}).get("content")
        blocks = []
        if isinstance(inner, list):
            blocks = inner
        elif isinstance(inner, dict):
            blocks = [inner]
        for b in _flatten_blocks(blocks):
            if not isinstance(b, dict):
                continue
            if b.get("type") not in ("tool_use", "tool-call"):
                continue
            name = b.get("name", "")
            if name not in ("Edit", "MultiEdit", "Write"):
                continue
            idx += 1
            inp = b.get("input", {}) or {}
            if isinstance(inp, dict):
                yield idx, name, inp


def _flatten_blocks(blocks: list) -> list:
    """递归展开 output/input/codex 包装，返回扁平 block 列表。
    与 _extract_block_for_eval 的递归路径保持一致以维持索引同步。
    """
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            out.append(b)
            continue
        btype = b.get("type", "")
        if btype in ("output", "input", "codex"):
            data = b.get("data")
            if isinstance(data, list):
                out.extend(_flatten_blocks(data))
            elif isinstance(data, dict):
                out.extend(_flatten_blocks([data]))
        else:
            out.append(b)
    return out


def _lookup_edit_details(messages: list[dict], indices: list[int],
                         max_per_field: int = 800) -> str:
    """根据 [E#] 索引返回指定编辑块的完整内容。

    - Edit: file_path + old_string + new_string（各截断到 max_per_field）
    - MultiEdit: file_path + 每条 sub-edit（前 5 条）
    - Write: file_path + content (前 max_per_field 字)

    indices 中找不到的索引会标"未找到"。
    """
    requested = set(int(i) for i in indices if isinstance(i, (int, str))
                    and str(i).strip().isdigit())
    if not requested:
        return "（未指定有效的编辑索引）"

    parts = []
    found = set()
    for idx, name, inp in _iter_edit_blocks(messages):
        if idx not in requested:
            continue
        found.add(idx)
        fp = inp.get("file_path", "?")
        if name == "Edit":
            old = (inp.get("old_string") or "")[:max_per_field]
            new = (inp.get("new_string") or "")[:max_per_field]
            parts.append(f"[E{idx}] Edit {fp}\n  -OLD:\n{old}\n  +NEW:\n{new}")
        elif name == "MultiEdit":
            edits = inp.get("edits") or []
            lines = [f"[E{idx}] MultiEdit {fp} ({len(edits)} 处子编辑):"]
            for j, e in enumerate(edits[:5]):
                if not isinstance(e, dict):
                    continue
                old = (e.get("old_string") or "")[:300]
                new = (e.get("new_string") or "")[:300]
                lines.append(f"  #{j+1} -OLD: {old}")
                lines.append(f"  #{j+1} +NEW: {new}")
            if len(edits) > 5:
                lines.append(f"  ...（还有 {len(edits)-5} 处省略）")
            parts.append("\n".join(lines))
        elif name == "Write":
            content = (inp.get("content") or "")[:max_per_field]
            line_count = (inp.get("content") or "").count("\n") + 1
            parts.append(
                f"[E{idx}] Write {fp} ({line_count} 行)\n  CONTENT:\n{content}")

    missing = requested - found
    if missing:
        parts.append(f"（未找到的编辑索引: {sorted(missing)}）")

    return "\n\n".join(parts) if parts else "（无匹配的编辑详情）"


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
        # 重入保护：标记 _execute_next_task 当前正在为哪些 sid 跑
        # 避免 pause→resume 间隙的并发触发同时派发两条任务指令
        self._executing_sids: set[str] = set()

    # ════════════════════════════════════════
    # 状态查询
    # ════════════════════════════════════════

    def is_active(self, sid: str) -> bool:
        plan = self._plans.get(sid)
        return plan is not None and plan["status"] == "executing"

    def get_plan(self, sid: str) -> dict | None:
        return self._plans.get(sid)

    def lookup_task(self, sid: str, task_id: str) -> dict | None:
        plan = self._plans.get(sid)
        if not plan:
            return None
        return _find_task_by_id(plan["tasks"], task_id)

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
                "如需修改，请说明修改意见；确认无误后，请说「开始执行」"
                "（或直接发送 /hapi takeover start 命令）。")

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
                "如需继续修改，请说明意见；确认无误后，请说「开始执行」"
                "（或直接发送 /hapi takeover start 命令）。")

    # ════════════════════════════════════════
    # 执行控制
    # ════════════════════════════════════════

    async def control(self, sid: str, action: str) -> str:
        """统一控制入口：start / pause / resume / skip / accept / cancel"""
        plan = self._plans.get(sid)
        if not plan:
            return "❌ 当前无活跃计划"

        if action == "start":
            return await self._start(sid, plan)
        elif action == "pause":
            return await self._pause(sid, plan)
        elif action == "resume":
            return await self._resume(sid, plan)
        elif action == "skip":
            return await self._skip(sid, plan)
        elif action == "accept":
            return await self._accept(sid, plan)
        elif action == "cancel":
            return await self._cancel(sid, plan)
        else:
            return (f"❌ 未知操作: {action}，"
                    "可用: start / pause / resume / skip / accept / cancel")

    async def _start(self, sid: str, plan: dict) -> str:
        if plan["status"] != "confirming":
            return f"❌ 计划状态为 {plan['status']}，只有 confirming 状态可以开始"
        plan["status"] = "executing"
        plan["awaiting_response_since"] = None
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
        plan["awaiting_response_since"] = None
        plan["updated_at"] = time.time()
        await self._persist(sid)
        logger.info("[takeover] paused plan for sid=%s", sid[:8])
        return "⏸️ 计划已暂停。当前运行中的任务会完成，但不会自动推进下一个。\n使用 resume 恢复执行。"

    async def _resume(self, sid: str, plan: dict) -> str:
        if plan["status"] != "paused":
            return f"❌ 计划状态为 {plan['status']}，只有 paused 状态可以恢复"
        plan["status"] = "executing"
        plan["awaiting_response_since"] = None
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

        # 通知 HAPI 终止当前任务
        abort_ok, abort_msg = await session_ops.abort_session(self.client, sid)

        # 清掉本地 pending ctx，防止迟到的 completion 触发评估
        sse = self.plugin.sse_listener
        sse._pending_takeover_completions.pop(sid, None)

        plan["status"] = "cancelled"
        plan["awaiting_response_since"] = None
        plan["updated_at"] = time.time()
        await self._persist(sid)
        logger.info("[takeover] cancelled sid=%s hapi_abort=%s", sid[:8], abort_ok)

        if abort_ok:
            return "🛑 计划已取消，HAPI 端任务已中止。"
        else:
            return (f"🛑 计划已在本地取消，但 HAPI 中止失败：{abort_msg}\n"
                    "如有需要请用 /hapi stop 手动中止。")

    async def _skip(self, sid: str, plan: dict) -> str:
        """跳过当前任务，推进到下一个。"""
        if plan["status"] not in ("paused", "executing"):
            return f"❌ 计划状态为 {plan['status']}，只有 paused / executing 可以 skip"

        task_id = plan.get("current_task_id")
        task = _find_task_by_id(plan["tasks"], task_id) if task_id else None
        if not task:
            return "❌ 当前没有可跳过的任务"

        plan["awaiting_response_since"] = None
        if task["status"] == "done":
            # 任务已完成（pause 时刚好完成但未推进），等价于 resume
            plan["status"] = "executing"
            plan["updated_at"] = time.time()
            await self._persist(sid)
            asyncio.create_task(self._execute_next_task(sid))
            return "ℹ️ 当前任务已完成，直接推进下一个。"

        task["status"] = "skipped"
        task["result_summary"] = task.get("result_summary") or "用户/AI 手动跳过"
        plan["status"] = "executing"
        plan["updated_at"] = time.time()
        await self._persist(sid)
        logger.info("[takeover] skip sid=%s task=%s", sid[:8], task_id)
        asyncio.create_task(self._execute_next_task(sid))
        return f"⏭️ 已跳过任务「{task['title']}」，继续下一个。"

    async def _accept(self, sid: str, plan: dict) -> str:
        """把 HAPI 当前回应当作任务结果，走 on_task_completed 推进。"""
        if plan["status"] != "paused":
            return f"❌ 计划状态为 {plan['status']}，只有 paused 可以 accept"

        task_id = plan.get("current_task_id")
        task = _find_task_by_id(plan["tasks"], task_id) if task_id else None
        if not task:
            return "❌ 当前没有可 accept 的任务"
        if task["status"] not in ("pending", "running"):
            return f"❌ 任务状态为 {task['status']}，accept 只对 pending / running 任务有效"

        pre_send_seq = task.get("pre_send_seq")
        if pre_send_seq is None:
            return "❌ 找不到该任务的发送记录，无法 accept；建议 resume 重做。"

        # 拉 raw messages（让评估走启发式 formatter 精简）
        try:
            messages = await self.plugin.llm_integration._fetch_messages_after_seq(
                sid, pre_send_seq)
        except Exception as e:
            logger.warning("[takeover] accept fetch 失败 sid=%s: %s", sid[:8], e)
            return f"❌ 拉取 HAPI 响应失败：{e}\n建议 resume 或 skip。"

        # 启发式精简后看是否有有意义内容
        formatted = _format_response_for_evaluation(messages)
        if not messages or len(formatted.strip()) < 50:
            return ("❌ HAPI 没有返回足够内容（< 50 字符），无法 accept。\n"
                    "建议 check 后选择 resume 或 skip。")

        plan["awaiting_response_since"] = None
        plan["status"] = "executing"
        task["status"] = "running"  # on_task_completed 期望 running
        await self._persist(sid)
        logger.info("[takeover] accept sid=%s task=%s msgs=%d formatted_len=%d",
                    sid[:8], task_id, len(messages), len(formatted))
        # 走标准评估路径推进（传 raw messages 让 on_task_completed 内部 format）
        asyncio.create_task(
            self.on_task_completed(sid, messages, ctx_task_id=task_id))
        return "✅ 已采纳 HAPI 当前回应作为任务结果，正在评估并推进。"

    # ════════════════════════════════════════
    # 诊断（check —— 用户与 AI 共享的核心）
    # ════════════════════════════════════════

    async def check(self, sid: str) -> dict:
        """诊断当前 takeover 状态。返回 CheckResult dict。

        必须用实时 API 调用而非内存缓存：超时场景下本地缓存大概率也已落后。

        返回字段：
          ok: bool                — 检查是否成功（API 可达）
          reason: str             — ok=False 时的原因（"no_plan" / "hapi_unreachable"）
          plan, task: dict        — 当前 plan 和 task
          thinking, active: bool  — HAPI 实时状态
          has_output: bool        — 响应是否 ≥ 50 字符
          response_preview: str   — 响应文本前 500 字符
          recommendation: str     — "wait" / "accept" / "manual"
        """
        plan = self._plans.get(sid)
        if not plan:
            return {"ok": False, "reason": "no_plan"}

        task_id = plan.get("current_task_id")
        task = _find_task_by_id(plan["tasks"], task_id) if task_id else None

        # 调 HAPI REST API 拿 fresh 状态
        try:
            detail = await session_ops.fetch_session_detail(self.client, sid)
        except Exception as e:
            return {
                "ok": False,
                "reason": "hapi_unreachable",
                "error": str(e),
                "plan": plan,
                "task": task,
            }

        thinking = bool(detail.get("thinking", False))
        active = bool(detail.get("active", False))

        # 拉响应（pre_send_seq 来自 task，sweep 后仍保留）
        response = ""
        if task and task.get("pre_send_seq") is not None:
            try:
                response = await self.plugin.llm_integration._fetch_completion_response(
                    sid, task["pre_send_seq"])
            except Exception as e:
                logger.warning("[check] fetch_response 失败 sid=%s: %s", sid[:8], e)

        has_output = bool(response and len(response.strip()) >= 50)

        if thinking or active:
            recommendation = "wait"
        elif has_output:
            recommendation = "accept"
        else:
            recommendation = "manual"

        return {
            "ok": True,
            "plan": plan,
            "task": task,
            "thinking": thinking,
            "active": active,
            "has_output": has_output,
            "response_preview": response[:500] if response else "",
            "recommendation": recommendation,
        }

    async def check_for_user(self, sid: str) -> str:
        """用户命令 /hapi takeover check 入口：调 check 核心 + 用户文案 + 清 awaiting。"""
        result = await self.check(sid)
        await self._clear_awaiting_response_since(sid)
        return self._format_check_result_for_user(result)

    async def check_for_llm(self, sid: str) -> str:
        """LLM 工具 hapi_coding_takeover_check 入口：调 check 核心 + LLM 文案 + 清 awaiting。"""
        result = await self.check(sid)
        await self._clear_awaiting_response_since(sid)
        return self._format_check_result_for_llm(result)

    async def _clear_awaiting_response_since(self, sid: str):
        plan = self._plans.get(sid)
        if plan and plan.get("awaiting_response_since"):
            plan["awaiting_response_since"] = None
            await self._persist(sid)

    def _format_check_result_for_user(self, result: dict) -> str:
        if not result["ok"]:
            if result["reason"] == "no_plan":
                return "当前 session 无 takeover 计划。"
            if result["reason"] == "hapi_unreachable":
                return (f"❌ 无法访问 HAPI：{result.get('error', '')}\n"
                        "可能 HAPI 服务下线或网络异常，建议人工检查。")
            return f"❌ 诊断失败：{result.get('reason')}"

        plan = result["plan"]
        task = result["task"]
        title = task["title"] if task else "（无当前任务）"
        plan_text = _format_plan_text(plan, with_status=True)

        state_line = "🟢 thinking" if result["thinking"] else (
            "🟢 active（执行工具）" if result["active"] else "⚪ idle")

        rec_text = {
            "wait": "建议再等等（HAPI 仍在工作）；可调 resume 让 takeover 重新等待。",
            "accept": "建议 accept（HAPI 看起来已完成）；如有疑虑可手动检查 /hapi msg。",
            "manual": "建议人工判断（HAPI 空闲但无输出，可能已被中止/出错）。",
        }.get(result["recommendation"], "")

        preview = result.get("response_preview", "")
        preview_block = ""
        if preview:
            head = preview[:200].replace("\n", " ")
            preview_block = f"\n\n📝 HAPI 响应预览（前 200 字）：\n> {head}"

        return (f"=== Takeover 诊断 ===\n{plan_text}\n\n"
                f"=== HAPI 当前情况 ===\n"
                f"当前任务: {title}\n"
                f"HAPI 状态: {state_line}\n"
                f"有有效输出: {'是' if result['has_output'] else '否'}"
                f"{preview_block}\n\n"
                f"=== 推荐 ===\n{rec_text}\n\n"
                f"可选: /hapi takeover [resume|accept|skip|cancel]")

    def _format_check_result_for_llm(self, result: dict) -> str:
        if not result["ok"]:
            return (f"check_failed reason={result['reason']} "
                    f"error={result.get('error', '')}")

        plan = result["plan"]
        task = result["task"]
        title = task["title"] if task else "(none)"
        return (
            f"plan_status={plan['status']} "
            f"task_title={title!r} "
            f"hapi_thinking={result['thinking']} "
            f"hapi_active={result['active']} "
            f"has_output={result['has_output']} "
            f"recommendation={result['recommendation']}\n"
            f"response_preview: {result.get('response_preview', '')[:300]}")

    # ════════════════════════════════════════
    # Sweep 超时回调（由 sse_listener 周期调用）
    # ════════════════════════════════════════

    async def on_sweep_timeout(self, sid: str, task_id: str):
        """单任务超过 30 min 未完成，安全暂停并启动 5min 用户响应窗口。"""
        plan = self._plans.get(sid)
        if not plan or plan["status"] != "executing":
            return  # 已 cancel / completed / paused，不动
        task = _find_task_by_id(plan["tasks"], task_id) if task_id else None
        if task and task["status"] == "running":
            task["status"] = "pending"  # 回滚，让 resume 可重做
        plan["status"] = "paused"
        plan["awaiting_response_since"] = time.time()  # 启动 5min 计时
        plan["updated_at"] = time.time()
        await self._persist(sid)

        title = task["title"] if task else "未知任务"
        logger.warning("[takeover] sweep timeout sid=%s task=%s title=%r",
                       sid[:8], task_id, title)
        await self._notify_user(
            sid,
            f"⚠️ 任务「{title}」超过 30 分钟未完成，计划已自动暂停。\n"
            f"\n"
            f"建议先用 /hapi takeover check 诊断 HAPI 当前状态，\n"
            f"然后选择处理方式（accept / resume / skip / cancel）。\n"
            f"\n"
            f"5 分钟内未响应将自动通知 AstrBot AI 接管处理。")

    async def on_user_response_timeout(self, sid: str):
        """暂停后 5 分钟用户未响应，通知 AI 接管。"""
        plan = self._plans.get(sid)
        if not plan or not plan.get("awaiting_response_since"):
            return  # 已被用户/前一轮清掉
        if plan["status"] != "paused":
            return  # 状态变了（用户已动）

        plan["awaiting_response_since"] = None  # 清掉防重复通知
        plan["updated_at"] = time.time()
        await self._persist(sid)

        task_id = plan.get("current_task_id")
        task = _find_task_by_id(plan["tasks"], task_id) if task_id else None
        title = task["title"] if task else "未知任务"
        task_id_str = task_id or "unknown"
        logger.warning("[takeover] user response timeout sid=%s task=%s",
                       sid[:8], task_id_str)

        # 通知用户 AI 已接管
        await self._notify_user(
            sid,
            f"⚠️ 5 分钟内未收到响应，AstrBot AI 已接管处理「{title}」的超时情况。")

        # 给 AstrBot 推一条 user-style 消息，触发 LLM 决策
        umo = plan.get("umo")
        if umo:
            prompt = (
                f"[Takeover 超时升级]\n"
                f"Plan {plan['id']} 的任务「{title}」（task_id={task_id_str}）"
                f"超过 30 分钟未完成，且用户在 5 分钟内未响应。\n"
                f"\n"
                f"请按以下步骤处理：\n"
                f"1. 调用 hapi_coding_takeover_check 诊断 HAPI 当前状态\n"
                f"2. 根据返回的 recommendation 字段决定：\n"
                f"   - wait: 调用 hapi_coding_takeover_control(action='resume') 重启等待\n"
                f"   - accept: 调用 hapi_coding_takeover_control(action='accept') 采纳结果\n"
                f"   - manual: 用户人工介入更合适，向用户解释当前情况并等待指令")
            try:
                await self.plugin.sse_listener._send_user_message(umo, prompt)
            except Exception as e:
                logger.warning("[takeover] 给 LLM 推升级 prompt 失败 sid=%s: %s",
                               sid[:8], e)

    # ════════════════════════════════════════
    # 执行循环（核心）
    # ════════════════════════════════════════

    async def _execute_next_task(self, sid: str):
        """找到下一个 pending 任务，构建指令，发送给 HAPI。

        重入保护：同一 sid 同一时刻只允许一个 _execute_next_task 在跑。
        若 pause→resume 在 LLM 调用期间撞车，第二次调用直接放弃；
        前一个完成后会通过 SSE→on_task_completed→_execute_next_task 链自然推进。
        """
        if sid in self._executing_sids:
            logger.debug("[takeover] _execute_next_task 已为 sid=%s 在跑，跳过重入",
                         sid[:8])
            return
        self._executing_sids.add(sid)
        try:
            await self._execute_next_task_impl(sid)
        finally:
            self._executing_sids.discard(sid)

    async def _execute_next_task_impl(self, sid: str):
        """实际执行逻辑（被 _execute_next_task 包装做重入保护）。"""
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

        # 进度提示：LLM 调用可能耗时 5-15s，先告诉用户系统在工作
        await self._notify_user(sid, f"🤖 正在为「{task['title']}」构造执行指令…")

        # 构建具体指令
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

        # 更新状态（task 上记录发送上下文，sweep 后 check/accept 仍可用）
        task["status"] = "running"
        task["pre_send_seq"] = pre_send_seq
        task["sent_at"] = time.time()
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

    async def on_task_completed(self, sid: str,
                                response: "str | list[dict]",
                                ctx_task_id: str | None = None):
        """HAPI 任务完成后的评估循环（由 sse_listener 调用）。

        response: 既可以是已格式化好的字符串（回退路径），也可以是 raw messages
                  list[dict]。后者会用 _format_response_for_evaluation 启发式精简
                  并保留原始消息供 inspect_edits drill-down 使用。
        ctx_task_id: 注册回调时记录的 task_id，防止 cancel→新 plan 间隙的旧 completion
                     被错误评估到新任务。
        """
        # raw messages → 启发式精简文本，同时保留 raw 供 inspect_edits 使用
        if isinstance(response, list):
            raw_messages = response
            response = _format_response_for_evaluation(raw_messages)
        else:
            raw_messages = None

        plan = self._plans.get(sid)
        if not plan:
            return

        current_task_id = plan.get("current_task_id")
        if ctx_task_id and ctx_task_id != current_task_id:
            logger.warning(
                "[takeover] discard stale completion: ctx_task_id=%s current=%s status=%s",
                ctx_task_id, current_task_id, plan["status"])
            return

        task = _find_task_by_id(plan["tasks"], current_task_id) if current_task_id else None
        if not task:
            logger.warning("[takeover] completed but task not found: %s", current_task_id)
            return

        # 进度提示：评估也要调 LLM，几秒内会有结果
        await self._notify_user(sid, f"🔍 「{task['title']}」执行完毕，正在评估结果…")

        # 调用 LLM 评估（raw_messages 支持 inspect_edits drill-down）
        evaluation = await self._evaluate_task(sid, plan, task, response,
                                                raw_messages=raw_messages)

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
                        sid: str = "", umo: str = "",
                        json_mode: bool = False) -> str | None:
        """调用 LLM。json_mode=True 时强制 JSON 输出。"""
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

            kwargs = {
                "chat_provider_id": provider_id,
                "prompt": user_prompt,
                "system_prompt": system_prompt,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            llm_resp = await context.llm_generate(**kwargs)
            return llm_resp.completion_text.strip() or None
        except Exception as e:
            logger.warning("[takeover] LLM call failed (json=%s): %s",
                           json_mode, e)
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

    async def _evaluate_task(self, sid: str, plan: dict, task: dict,
                             response: str,
                             raw_messages: list[dict] | None = None,
                             max_inspections: int = 2) -> dict:
        """LLM 评估任务结果。支持 inspect_edits 多轮 drill-down。

        raw_messages: 原始消息列表（含 Edit/Write 完整 input），用于响应
                       inspect_edits 请求。None 时不允许 inspect。
        max_inspections: inspect_edits 最多生效次数。
        """
        plan_summary = _format_plan_text(plan, with_status=True)
        base_user = prompts.EVALUATION_USER.format(
            goal=plan["goal"],
            plan_summary=plan_summary,
            task_title=task["title"],
            task_description=task["description"],
            response=response)

        inspections_done = 0
        extra_context = ""

        while True:
            resp = await self._call_llm(
                prompts.EVALUATION_SYSTEM,
                base_user + extra_context,
                sid=sid, json_mode=True)
            parsed = self._parse_json(resp) if resp else None
            if not parsed:
                break  # 解析失败 → 走 fallback

            # inspect_edits drill-down 路径
            wants_inspect = parsed.get("next_action") == "inspect_edits"
            indices = parsed.get("inspect_edit_indices") or []
            if (wants_inspect and raw_messages
                    and isinstance(indices, list) and indices
                    and inspections_done < max_inspections):
                details = _lookup_edit_details(raw_messages, indices)
                inspections_done += 1
                remaining = max_inspections - inspections_done
                extra_context = (
                    f"\n\n=== 编辑详情（第 {inspections_done}/"
                    f"{max_inspections} 次查看）===\n{details}\n\n"
                    f"剩余查看次数: {remaining}。请基于以上详情给出最终判断。"
                    + (" 不要再请求 inspect_edits。" if remaining == 0 else ""))
                logger.info(
                    "[takeover] evaluation inspecting edits %s (round %d)",
                    indices, inspections_done)
                continue

            # budget 用尽但 LLM 仍要求 inspect：把 next_action 修正为 continue
            if wants_inspect:
                logger.warning(
                    "[takeover] evaluation still requests inspect_edits "
                    "(done=%d budget=%d), forcing continue",
                    inspections_done, max_inspections)
                parsed["next_action"] = "continue"
                if not parsed.get("task_status"):
                    parsed["task_status"] = "done"
                if not parsed.get("task_summary"):
                    parsed["task_summary"] = "（评估器请求查看更多细节，预算耗尽，按 continue 处理）"

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
