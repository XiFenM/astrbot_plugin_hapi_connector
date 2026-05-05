"""Session 操作函数：异步封装多步 API 调用"""

import asyncio
import base64
import json
import re
import time

from astrbot.api import logger
from ..core.hapi_client import AsyncHapiClient


async def fetch_sessions(client: AsyncHapiClient) -> list[dict]:
    """获取所有 session 列表"""
    resp = await client.get("/api/sessions")
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    return data.get("sessions", [])


async def fetch_session_detail(client: AsyncHapiClient, sid: str) -> dict:
    """获取单个 session 详情"""
    resp = await client.get(f"/api/sessions/{sid}")
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    return data.get("session", data)


async def fetch_messages(client: AsyncHapiClient, sid: str, limit: int = 10) -> list[dict]:
    """获取 session 的最近消息"""
    resp = await client.get(f"/api/sessions/{sid}/messages", params={"limit": limit})
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    return data.get("messages", [])


async def send_message(client: AsyncHapiClient, sid: str, text: str) -> tuple[bool, str]:
    """发送消息到 session，返回 (成功, 描述)"""
    resp = await client.post(f"/api/sessions/{sid}/messages", json={"text": text})
    if resp.ok:
        resp.release()
        return True, f"已发送 -> [{sid[:8]}]"
    else:
        body = await resp.text()
        resp.release()
        return False, f"发送失败: {resp.status} {body[:200]}"


async def send_message(client: AsyncHapiClient, sid: str, text: str,
                       attachments: list[dict] | None = None) -> tuple[bool, str]:
    """Send a message to a session, optionally with uploaded attachments."""
    payload = {"text": text}
    if attachments:
        payload["attachments"] = attachments

    resp = await client.post(f"/api/sessions/{sid}/messages", json=payload)
    if resp.ok:
        resp.release()
        if attachments:
            return True, f"sent -> [{sid[:8]}] ({len(attachments)} attachments)"
        return True, f"sent -> [{sid[:8]}]"

    body = await resp.text()
    resp.release()
    return False, f"send failed: {resp.status} {body[:200]}"


async def set_permission_mode(client: AsyncHapiClient, sid: str, mode: str) -> tuple[bool, str]:
    """设置权限模式"""
    resp = await client.post(f"/api/sessions/{sid}/permission-mode", json={"mode": mode})
    if resp.ok:
        resp.release()
        return True, f"权限模式已切换为: {mode}"
    else:
        body = await resp.text()
        resp.release()
        return False, f"切换失败: {resp.status} {body[:200]}"


async def set_model_mode(client: AsyncHapiClient, sid: str, model: str) -> tuple[bool, str]:
    """设置模型模式（Claude / Gemini）。model 传 None 或 "default"/"auto" 表示恢复自动选择。"""
    api_model: str | None = model
    if model in ("default", "auto"):
        api_model = None
    resp = await client.post(f"/api/sessions/{sid}/model", json={"model": api_model})
    if resp.ok:
        resp.release()
        label = model if api_model else "auto"
        return True, f"模型已切换为: {label}"
    else:
        body = await resp.text()
        resp.release()
        return False, f"切换失败: {resp.status} {body[:200]}"


async def fetch_session_models(client: AsyncHapiClient, sid: str) -> dict:
    """获取 session 可用的模型列表。
    Returns: {flavor, currentModel, presets: [{id, label}], supportsCustomModel}
    """
    data = await client.get_json(f"/api/sessions/{sid}/models")
    return data


async def compact_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """触发 session 的上下文压缩"""
    resp = await client.post(f"/api/sessions/{sid}/compact", json={})
    if resp.ok:
        resp.release()
        return True, "已触发上下文压缩"
    else:
        body = await resp.text()
        resp.release()
        return False, f"触发压缩失败: {resp.status} {body[:200]}"


async def approve_permission(client: AsyncHapiClient, sid: str, rid: str,
                             answers: dict | None = None) -> tuple[bool, str]:
    """批准权限请求；AskUserQuestion 需传 answers={"0": ["选项label"]}"""
    body = {"answers": answers} if answers else {}
    resp = await client.post(f"/api/sessions/{sid}/permissions/{rid}/approve", json=body)
    if resp.ok:
        resp.release()
        return True, "已批准"
    else:
        body_text = await resp.text()
        resp.release()
        return False, f"批准失败: {resp.status} {body_text[:200]}"


async def answer_permission_question(client: AsyncHapiClient, sid: str, rid: str,
                                     answers: dict) -> tuple[bool, str]:
    """提交 AskUserQuestion 的回答。"""
    return await approve_permission(client, sid, rid, answers=answers)


async def deny_permission(client: AsyncHapiClient, sid: str, rid: str) -> tuple[bool, str]:
    """拒绝权限请求"""
    resp = await client.post(f"/api/sessions/{sid}/permissions/{rid}/deny", json={})
    if resp.ok:
        resp.release()
        return True, "已拒绝"
    else:
        body = await resp.text()
        resp.release()
        return False, f"拒绝失败: {resp.status} {body[:200]}"


async def switch_to_remote(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """切换 session 到 remote 远程托管模式"""
    resp = await client.post(f"/api/sessions/{sid}/switch", json={})
    if resp.ok:
        resp.release()
        return True, "已切换到 remote 远程托管模式"
    else:
        body = await resp.text()
        resp.release()
        return False, f"切换失败: {resp.status} {body[:200]}"


async def abort_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """中断活跃的 session"""
    resp = await client.post(f"/api/sessions/{sid}/abort", json={})
    if resp.ok:
        resp.release()
        return True, f"已中断 [{sid[:8]}]"
    else:
        body = await resp.text()
        resp.release()
        return False, f"中断失败: {resp.status} {body[:200]}"


async def archive_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """归档 session"""
    resp = await client.post(f"/api/sessions/{sid}/archive", json={})
    if resp.ok:
        resp.release()
        return True, f"归档成功 [{sid[:8]}]"
    else:
        body = await resp.text()
        resp.release()
        return False, f"归档失败: {resp.status} {body[:200]}"


async def resume_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str, str | None]:
    """恢复 inactive session，返回 (成功, 描述, 恢复后的 session_id 或 None)"""
    resp = await client.post(f"/api/sessions/{sid}/resume", json={})
    if resp.ok:
        data = await resp.json()
        resp.release()
        resumed_sid = data.get("sessionId") or sid
        return True, f"已恢复 [{resumed_sid[:8]}]", resumed_sid
    else:
        body = await resp.text()
        resp.release()
        return False, f"恢复失败: {resp.status} {body[:200]}", None


async def rename_session(client: AsyncHapiClient, sid: str, new_name: str) -> tuple[bool, str]:
    """重命名 session"""
    resp = await client.patch(f"/api/sessions/{sid}", json={"name": new_name})
    if resp.ok:
        resp.release()
        return True, f"重命名成功 [{sid[:8]}]"
    else:
        body = await resp.text()
        resp.release()
        return False, f"重命名失败: {resp.status} {body[:200]}"


async def delete_session(client: AsyncHapiClient, sid: str) -> tuple[bool, str]:
    """删除 session"""
    resp = await client.delete(f"/api/sessions/{sid}")
    if resp.ok:
        resp.release()
        return True, f"删除成功 [{sid[:8]}]"
    else:
        body = await resp.text()
        resp.release()
        return False, f"删除失败: {resp.status} {body[:200]}"


async def fetch_machines(client: AsyncHapiClient) -> list[dict]:
    """获取在线机器列表"""
    resp = await client.get("/api/machines")
    resp.raise_for_status()
    data = await resp.json()
    resp.release()
    machines = data.get("machines", [])
    return [m for m in machines if m.get("active")]


async def fetch_recent_paths(client: AsyncHapiClient) -> list[str]:
    """从已有 sessions 提取去重的最近工作目录"""
    sessions = await fetch_sessions(client)
    paths = []
    for s in sessions:
        p = s.get("metadata", {}).get("path", "")
        if p and p not in paths:
            paths.append(p)
    return paths


async def spawn_session(client: AsyncHapiClient, machine_id: str,
                        directory: str, agent: str, session_type: str = "simple",
                        yolo: bool = False, worktree_name: str = "",
                        model_reasoning_effort: str | None = None) -> tuple[bool, str, str | None]:
    """创建新 session，返回 (成功, 消息, session_id 或 None)"""
    body = {
        "directory": directory,
        "agent": agent,
        "sessionType": session_type,
        "yolo": yolo,
    }
    if worktree_name:
        body["worktreeName"] = worktree_name
    if model_reasoning_effort:
        body["modelReasoningEffort"] = model_reasoning_effort

    resp = await client.post(f"/api/machines/{machine_id}/spawn", json=body)
    if resp.status != 200:
        body_text = await resp.text()
        resp.release()
        return False, f"创建失败: {resp.status} {body_text[:300]}", None

    result = await resp.json()
    resp.release()
    if result.get("type") == "success":
        sid = result["sessionId"]
        return True, f"创建成功! Session ID: {sid}", sid
    else:
        return False, f"创建失败: {result.get('message', '未知错误')}", None


async def list_files(client: AsyncHapiClient, sid: str,
                     query: str = "", limit: int = 200) -> list[dict]:
    """搜索 session 工作目录下的文件（ripgrep）"""
    params: dict = {"limit": limit}
    if query:
        params["query"] = query
    data = await client.get_json(f"/api/sessions/{sid}/files", params=params)
    return data.get("files", [])


async def list_directory(client: AsyncHapiClient, sid: str,
                         path: str = ".") -> list[dict]:
    """列出远端目录，每个条目含 name/type/size/modified"""
    data = await client.get_json(f"/api/sessions/{sid}/directory",
                                 params={"path": path})
    return data.get("entries", [])


async def read_file(client: AsyncHapiClient, sid: str,
                    path: str) -> tuple[bool, str]:
    """读取远端文件，返回 (成功, base64内容或错误信息)"""
    resp = await client.get(f"/api/sessions/{sid}/file", params={"path": path})
    if not resp.ok:
        body = await resp.text()
        resp.release()
        return False, f"读取失败: {resp.status} {body[:200]}"
    data = await resp.json()
    resp.release()
    if not data.get("success"):
        return False, f"读取失败: {data.get('error', data.get('message', '未知错误'))}"
    content = data.get("content", "")
    if not content:
        return False, "文件内容为空或不存在"
    return True, content


async def fetch_skills(client: AsyncHapiClient, sid: str) -> list[dict]:
    """获取会话可用的 skills 列表"""
    data = await client.get_json(f"/api/sessions/{sid}/skills")
    return data.get("skills", [])


async def fetch_slash_commands(client: AsyncHapiClient, sid: str) -> list[dict]:
    """获取会话可用的 slash commands 列表"""
    data = await client.get_json(f"/api/sessions/{sid}/slash-commands")
    return data.get("commands", data.get("slashCommands", []))


async def fetch_session_capabilities(client: AsyncHapiClient, sid: str) -> dict:
    """并行抓取会话的能力配置（skills/commands/CLAUDE.md/MCP），单个失败不影响整体"""
    results = await asyncio.gather(
        _safe_fetch(fetch_skills, client, sid),
        _safe_fetch(fetch_slash_commands, client, sid),
        _safe_fetch(read_file, client, sid, "CLAUDE.md"),
        _safe_fetch(read_file, client, sid, ".claude/settings.json"),
        return_exceptions=True,
    )

    caps: dict = {"skills": [], "slash_commands": [], "claude_md_summary": "",
                  "mcp_servers": [], "fetched_at": time.monotonic()}

    # skills
    if isinstance(results[0], list):
        caps["skills"] = results[0]

    # slash commands
    if isinstance(results[1], list):
        caps["slash_commands"] = results[1]

    # CLAUDE.md
    if isinstance(results[2], tuple) and results[2][0]:
        try:
            text = base64.b64decode(results[2][1]).decode("utf-8", errors="replace")
            caps["claude_md_summary"] = text[:500]
        except Exception:
            pass

    # settings.json → MCP servers
    if isinstance(results[3], tuple) and results[3][0]:
        try:
            settings = json.loads(base64.b64decode(results[3][1]).decode("utf-8", errors="replace"))
            caps["mcp_servers"] = list(settings.get("mcpServers", {}).keys())
        except Exception:
            pass

    return caps


async def _safe_fetch(fn, *args):
    """包装异步调用，异常时返回 None 而非抛出"""
    try:
        return await fn(*args)
    except Exception as e:
        logger.debug("capability fetch failed (%s): %s", fn.__name__, e)
        return None


def _guess_home_dir(work_dir: str) -> str:
    """从工作目录推导 home 目录（/root 或 /home/xxx）"""
    parts = (work_dir or "").split("/")
    if len(parts) >= 2 and parts[1] == "root":
        return "/root"
    if len(parts) >= 3 and parts[1] == "home":
        return f"/home/{parts[2]}"
    return "/root"


async def find_cc_history_dir(client: AsyncHapiClient, machine_id: str,
                              work_dir: str) -> str | None:
    """根据工作目录推算 Claude Code 历史目录路径（使用 machine-level API，无路径限制）。

    Claude Code 路径规则: ~/.claude/projects/{path_with_dashes}/
    例如 /root/workspace/host → /root/.claude/projects/-root-workspace-host/
    """
    home_dir = _guess_home_dir(work_dir)
    projects_base = f"{home_dir}/.claude/projects"

    def _has_jsonl(entries: list) -> bool:
        return any(e.get("name", "").endswith(".jsonl") for e in entries)

    # 方案 A：直接按路径规则精确匹配
    hash_name = work_dir.rstrip("/").replace("/", "-")
    if not hash_name.startswith("-"):
        hash_name = "-" + hash_name
    candidate = f"{projects_base}/{hash_name}"
    logger.debug("[playbook] 方案A 候选路径: %s (work_dir=%s)", candidate, work_dir)
    try:
        entries = await list_machine_directory(client, machine_id, candidate)
        if _has_jsonl(entries):
            logger.info("[playbook] 方案A 成功找到: %s (%d 个条目)", candidate, len(entries))
            return candidate
        logger.debug("[playbook] 方案A 路径存在但无 jsonl: %s", candidate)
    except Exception as e:
        logger.debug("[playbook] 方案A 异常 (%s): %s", candidate, e)

    # 方案 B：枚举 ~/.claude/projects/ 并按路径各段逐级匹配
    try:
        projects = await list_machine_directory(client, machine_id, projects_base)
        logger.debug("[playbook] 方案B 扫描 %s，共 %d 个条目", projects_base, len(projects))

        path_parts = [p for p in work_dir.rstrip("/").split("/") if p]
        for i in range(len(path_parts), 0, -1):
            segment = path_parts[i - 1]
            for entry in projects:
                if entry.get("type") != "directory":
                    continue
                name = entry.get("name", "")
                if not (segment and segment in name):
                    continue
                found = f"{projects_base}/{name}"
                sub = await list_machine_directory(client, machine_id, found)
                if _has_jsonl(sub):
                    logger.info("[playbook] 方案B 匹配: segment=%s → %s", segment, found)
                    return found
    except Exception as e:
        logger.warning("[playbook] 方案B 失败 (projects_base=%s): %s", projects_base, e)

    logger.warning("[playbook] 无法找到历史目录: work_dir=%s, home_dir=%s", work_dir, home_dir)
    return None


async def read_cc_conversations(client: AsyncHapiClient, machine_id: str,
                                history_dir: str) -> tuple[list[dict], dict]:
    """读取 Claude Code 历史目录下所有 JSONL 对话文件（使用 machine-level API），并行提取关键片段。

    Returns:
        (conversations, stats)
        conversations: [{filename, turns: [{role, text/tool, ...}]}]  按修改时间从新到旧排序
        stats: {"total": int, "valid": int, "filtered": int, "failed": int}
    """
    import asyncio as _asyncio

    entries = await list_machine_directory(client, machine_id, history_dir)
    jsonl_files = sorted(
        [e for e in entries if e.get("name", "").endswith(".jsonl")],
        key=lambda e: e.get("modified", ""),
        reverse=True,
    )
    logger.info("[playbook] 目录 %s 扫描到 %d 个条目，其中 %d 个 .jsonl 文件",
                history_dir, len(entries), len(jsonl_files))

    semaphore = _asyncio.Semaphore(3)  # 限制并发，避免 RPC 通道拥堵
    MIN_TURNS = 3  # 对话至少包含 3 个 turn 才有分析价值

    async def _read_one(f: dict) -> dict | None:
        async with semaphore:
            fname = f["name"]
            path = f"{history_dir}/{fname}"
            ok, b64_content = await read_machine_file(client, machine_id, path)
            if not ok:
                logger.warning("[playbook] 读取失败 %s: %s", fname, b64_content)
                return None
            try:
                text = base64.b64decode(b64_content).decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning("[playbook] 解码失败 %s: %s", fname, e)
                return None
            turns = _extract_key_turns(text)
            if not turns:
                logger.info("[playbook] 跳过 %s: 未提取到任何 turn", fname)
                return None
            # 过滤过于简短的对话（如只有一条 "hello" 的空 session）
            has_assistant = any(t["role"] in ("assistant", "tool_use") for t in turns)
            if len(turns) < MIN_TURNS and not has_assistant:
                logger.info("[playbook] 过滤 %s: 对话过短（%d turns，无 assistant 回复）", fname, len(turns))
                return None
            logger.info("[playbook] 读取成功 %s: %d turns", fname, len(turns))
            return {"filename": fname, "turns": turns}

    results = await _asyncio.gather(*[_read_one(f) for f in jsonl_files], return_exceptions=True)
    conversations = []
    read_fail = 0
    skipped = 0
    for f, r in zip(jsonl_files, results):
        if isinstance(r, Exception):
            read_fail += 1
            logger.warning("[playbook] 读取异常 %s: %s", f["name"], r)
        elif r is not None:
            conversations.append(r)
        else:
            skipped += 1

    stats = {"total": len(jsonl_files), "valid": len(conversations),
             "filtered": skipped, "failed": read_fail}
    logger.info("[playbook] 读取完成: %d 个文件中 %d 个有效, %d 个过滤, %d 个失败",
                stats["total"], stats["valid"], stats["filtered"], stats["failed"])

    return conversations, stats


_TOOL_INPUT_MAX = 120  # 工具调用参数预览最大字符数


def _truncate(text: str, max_len: int) -> str:
    """截断文本，超长时加省略号。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _summarize_tool_input(tool_name: str, raw_input: dict | str) -> str:
    """将工具调用的 input 精简为简短摘要，避免大段文件内容占用 LLM 注意力。

    对于读/写/编辑类工具，只保留路径和操作类型；
    对于其他工具保留截断后的参数预览。
    """
    if isinstance(raw_input, str):
        return _truncate(raw_input, _TOOL_INPUT_MAX)

    if not isinstance(raw_input, dict):
        return _truncate(str(raw_input), _TOOL_INPUT_MAX)

    # 写文件 / 编辑文件类工具：只保留路径，截掉文件内容
    path = raw_input.get("file_path") or raw_input.get("path") or raw_input.get("filename", "")
    name_lower = tool_name.lower()

    if any(kw in name_lower for kw in ("write", "edit", "create_file", "overwrite")):
        if path:
            return f"path={path}"
        return _truncate(str(raw_input), _TOOL_INPUT_MAX)

    if "read" in name_lower:
        if path:
            return f"path={path}"

    if "bash" in name_lower or "execute" in name_lower:
        cmd = raw_input.get("command", "")
        if cmd:
            return f"command={_truncate(cmd, _TOOL_INPUT_MAX)}"

    if "search" in name_lower or "grep" in name_lower or "glob" in name_lower:
        pattern = raw_input.get("pattern") or raw_input.get("query", "")
        if pattern:
            extra = f", path={path}" if path else ""
            return f"pattern={_truncate(pattern, 80)}{extra}"

    # 通用回退：截断整体字符串
    return _truncate(str(raw_input), _TOOL_INPUT_MAX)


# 非对话类型，应跳过
_SKIP_TYPES = frozenset((
    "system", "progress", "file-history-snapshot", "last-prompt",
))

# slash command 展开 prompt 的特征模式
_RE_COMMAND_NAME = re.compile(r"<command-name>/?([^<]+)</command-name>")
_RE_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_EXPANDED_PROMPT_MARKERS = (
    "IT IS CRITICAL THAT YOU FOLLOW THIS COMMAND",
    "You must fully embody this agent",
    "<agent-activation",
)


def _process_user_content(text: str) -> str | None:
    """处理用户消息文本，识别并精简 slash command 展开内容。

    返回处理后的文本，或 None 表示应跳过该消息。
    """
    stripped = text.strip()
    if not stripped:
        return None

    # 跳过 IDE 注入的系统/命令消息（/exit, stdout 等）
    if stripped.startswith("<") and (
        "local-command-caveat" in stripped[:200]
        or "local-command-stdout" in stripped[:200]
    ):
        return None

    # 识别带 <command-name> 标签的 slash command
    cmd_match = _RE_COMMAND_NAME.search(stripped)
    if cmd_match:
        cmd_name = cmd_match.group(1).strip()
        # 提取参数
        args_match = _RE_COMMAND_ARGS.search(stripped)
        args = args_match.group(1).strip() if args_match else ""
        if args:
            return f"/{cmd_name} {args}"
        return f"/{cmd_name}"

    # 识别无标签的展开 prompt（slash command 的扩展内容）
    for marker in _EXPANDED_PROMPT_MARKERS:
        if marker in stripped[:200]:
            # 检查是否有 ARGUMENTS: 部分（用户附带的真实参数）
            args_idx = stripped.find("ARGUMENTS:")
            if args_idx != -1:
                user_args = stripped[args_idx + len("ARGUMENTS:"):].strip()
                if user_args:
                    return f"[自定义命令] {user_args}"
            # 纯系统 prompt 展开，无用户参数 — 跳过
            return None

    return stripped


def _extract_key_turns(jsonl_text: str) -> list[dict]:
    """从 JSONL 提取关键对话片段。

    保留完整的用户指令和助手文字回复；工具调用只保留名称和精简参数摘要。
    跳过非对话类型（system / progress / snapshot 等）和工具结果回传行。

    兼容两种 JSONL 格式：
    - 直接格式：{role: "human"/"assistant", content: ...}
    - Claude Code 嵌套格式：{type: "user"/"assistant", message: {role, content}}
    """
    turns: list[dict] = []
    for line in jsonl_text.strip().split("\n"):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        top_type = record.get("type", "")

        # 跳过非对话类型
        if top_type in _SKIP_TYPES:
            continue

        # 跳过工具结果回传（嵌套格式中 type="user" + toolUseResult）
        if "toolUseResult" in record:
            continue

        # 兼容嵌套格式：从 message 字段提取 role/content
        role = record.get("role", "")
        content = record.get("content", "")
        if not role and "message" in record:
            msg = record["message"]
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
        # 也接受顶层 type 作为 role 的补充
        if not role:
            if top_type in ("user", "human"):
                role = "user"
            elif top_type == "assistant":
                role = "assistant"

        if role in ("human", "user"):
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(text_parts)
            if isinstance(content, str):
                processed = _process_user_content(content)
                if processed:
                    turns.append({"role": "user", "text": processed})

        elif role == "assistant":
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        tool_name = block.get("name", "?")
                        turns.append({
                            "role": "tool_use",
                            "tool": tool_name,
                            "input_preview": _summarize_tool_input(
                                tool_name, block.get("input", {})),
                        })
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        turns.append({"role": "assistant", "text": block["text"]})
            elif isinstance(content, str) and content.strip():
                turns.append({"role": "assistant", "text": content})

    return turns


async def list_machine_directory(client: AsyncHapiClient, machine_id: str,
                                 path: str) -> list[dict]:
    """通过 machine-level API 列出目录（不受 session 工作目录限制）"""
    data = await client.get_json(f"/api/machines/{machine_id}/directory",
                                 params={"path": path})
    return data.get("entries", [])


async def read_machine_file(client: AsyncHapiClient, machine_id: str,
                            path: str, timeout: int = 120) -> tuple[bool, str]:
    """通过 machine-level API 读取文件（不受 session 工作目录限制）"""
    resp = await client.request("GET", f"/api/machines/{machine_id}/file",
                                params={"path": path}, timeout=timeout)
    if not resp.ok:
        body = await resp.text()
        resp.release()
        return False, f"读取失败: {resp.status} {body[:200]}"
    data = await resp.json()
    resp.release()
    if not data.get("success"):
        return False, f"读取失败: {data.get('error', data.get('message', '未知错误'))}"
    content = data.get("content", "")
    if not content:
        return False, "文件内容为空或不存在"
    return True, content


async def check_path_exists(client: AsyncHapiClient, machine_id: str, path: str) -> bool:
    """检查机器上的路径是否存在"""
    try:
        resp = await client.post(f"/api/machines/{machine_id}/paths/exists",
                                 json={"path": path})
        data = await resp.json()
        resp.release()
        return data.get("exists", False)
    except Exception:
        return False
