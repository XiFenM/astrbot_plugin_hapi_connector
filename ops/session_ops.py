"""Session 操作函数：异步封装多步 API 调用"""

import asyncio
import base64
import json
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


async def find_cc_history_dir(client: AsyncHapiClient, sid: str,
                              work_dir: str) -> str | None:
    """根据 session 工作目录推算 Claude Code 历史目录路径。

    Claude Code 路径规则: ~/.claude/projects/{path_with_dashes}/
    例如 /root/workspace/host → /root/.claude/projects/-root-workspace-host/
    """
    # 方案 A：直接按路径规则推算
    hash_name = work_dir.replace("/", "-")
    if not hash_name.startswith("-"):
        hash_name = "-" + hash_name
    candidate = f"/root/.claude/projects/{hash_name}"
    try:
        entries = await list_directory(client, sid, candidate)
        if entries:
            return candidate
    except Exception:
        pass

    # 方案 B：遍历 ~/.claude/projects/ 找匹配目录
    try:
        projects = await list_directory(client, sid, "/root/.claude/projects")
        last_part = work_dir.rstrip("/").split("/")[-1]
        for entry in projects:
            if entry.get("type") != "directory":
                continue
            name = entry.get("name", "")
            if last_part and last_part in name:
                return f"/root/.claude/projects/{name}"
    except Exception:
        pass

    return None


async def read_cc_conversations(client: AsyncHapiClient, sid: str,
                                history_dir: str) -> list[dict]:
    """读取 Claude Code 历史目录下所有 JSONL 对话文件，全量提取关键片段。

    Returns:
        [{filename, turns: [{role, text/tool, ...}]}]  按修改时间从新到旧排序
    """
    entries = await list_directory(client, sid, history_dir)
    jsonl_files = sorted(
        [e for e in entries if e.get("name", "").endswith(".jsonl")],
        key=lambda e: e.get("modified", ""),
        reverse=True,
    )

    conversations = []
    for f in jsonl_files:
        path = f"{history_dir}/{f['name']}"
        ok, b64_content = await read_file(client, sid, path)
        if not ok:
            continue
        try:
            text = base64.b64decode(b64_content).decode("utf-8", errors="replace")
        except Exception:
            continue

        turns = _extract_key_turns(text)
        if turns:
            conversations.append({"filename": f["name"], "turns": turns})

    return conversations


def _extract_key_turns(jsonl_text: str) -> list[dict]:
    """从 JSONL 提取关键对话片段（user 指令 / tool_use 名称 / assistant 回复）。

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
            top_type = record.get("type", "")
            if top_type in ("user", "human"):
                role = "user"
            elif top_type == "assistant":
                role = "assistant"

        if role in ("human", "user"):
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(text_parts)
            if isinstance(content, str) and content.strip():
                turns.append({"role": "user", "text": content})

        elif role == "assistant":
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        turns.append({
                            "role": "tool_use",
                            "tool": block.get("name", "?"),
                            "input_preview": str(block.get("input", {})),
                        })
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        turns.append({"role": "assistant", "text": block["text"]})
            elif isinstance(content, str) and content.strip():
                turns.append({"role": "assistant", "text": content})

    return turns


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
