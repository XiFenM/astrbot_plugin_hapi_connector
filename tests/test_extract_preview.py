#!/usr/bin/env python3
"""测试 JSONL 对话提取 + 格式化效果的独立脚本。

用法：
    uv run python data/plugins/astrbot_plugin_hapi_connector/tests/test_extract_preview.py <jsonl文件路径>
    uv run python data/plugins/astrbot_plugin_hapi_connector/tests/test_extract_preview.py <目录>        # 处理目录下所有 jsonl
    uv run python data/plugins/astrbot_plugin_hapi_connector/tests/test_extract_preview.py <文件> --raw  # 同时显示原始 turns 详情
"""

import json
import os
import re
import sys

# ── 从 session_ops.py 复制核心函数（避免相对导入问题）──

_TOOL_INPUT_MAX = 120

def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _summarize_tool_input(tool_name: str, raw_input) -> str:
    if isinstance(raw_input, str):
        return _truncate(raw_input, _TOOL_INPUT_MAX)
    if not isinstance(raw_input, dict):
        return _truncate(str(raw_input), _TOOL_INPUT_MAX)
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
    return _truncate(str(raw_input), _TOOL_INPUT_MAX)


_SKIP_TYPES = frozenset(("system", "progress", "file-history-snapshot", "last-prompt"))

_RE_COMMAND_NAME = re.compile(r"<command-name>/?([^<]+)</command-name>")
_RE_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_EXPANDED_PROMPT_MARKERS = (
    "IT IS CRITICAL THAT YOU FOLLOW THIS COMMAND",
    "You must fully embody this agent",
    "<agent-activation",
)


def _process_user_content(text):
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("<") and (
        "local-command-caveat" in stripped[:200]
        or "local-command-stdout" in stripped[:200]
    ):
        return None
    cmd_match = _RE_COMMAND_NAME.search(stripped)
    if cmd_match:
        cmd_name = cmd_match.group(1).strip()
        args_match = _RE_COMMAND_ARGS.search(stripped)
        args = args_match.group(1).strip() if args_match else ""
        if args:
            return f"/{cmd_name} {args}"
        return f"/{cmd_name}"
    for marker in _EXPANDED_PROMPT_MARKERS:
        if marker in stripped[:200]:
            args_idx = stripped.find("ARGUMENTS:")
            if args_idx != -1:
                user_args = stripped[args_idx + len("ARGUMENTS:"):].strip()
                if user_args:
                    return f"[自定义命令] {user_args}"
            return None
    return stripped


def _extract_key_turns(jsonl_text: str) -> list[dict]:
    turns: list[dict] = []
    for line in jsonl_text.strip().split("\n"):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        top_type = record.get("type", "")
        if top_type in _SKIP_TYPES:
            continue
        if "toolUseResult" in record:
            continue
        role = record.get("role", "")
        content = record.get("content", "")
        if not role and "message" in record:
            msg = record["message"]
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
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


def _format_conversations(conversations: list[dict]) -> list[str]:
    formatted = []
    for conv in conversations:
        lines = [f"=== 对话: {conv['filename']} ==="]
        pending_tools: list[str] = []

        def _flush_tools():
            if not pending_tools:
                return
            if len(pending_tools) <= 3:
                lines.append(f"  [工具] {' → '.join(pending_tools)}")
            else:
                lines.append(
                    f"  [工具] {pending_tools[0]} → ... → {pending_tools[-1]}"
                    f"（共 {len(pending_tools)} 次调用）"
                )
            pending_tools.clear()

        for turn in conv["turns"]:
            if turn["role"] == "tool_use":
                preview = turn.get("input_preview", "")
                tool_desc = f"{turn['tool']}({preview})" if preview else turn["tool"]
                pending_tools.append(tool_desc)
            else:
                _flush_tools()
                if turn["role"] == "user":
                    lines.append(f"[用户] {turn['text']}")
                elif turn["role"] == "assistant":
                    lines.append(f"[助手] {turn['text']}")
        _flush_tools()
        formatted.append("\n".join(lines))
    return formatted


# ── 主逻辑 ──

def process_file(filepath: str, show_raw: bool = False):
    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)

    with open(filepath, encoding="utf-8", errors="replace") as f:
        text = f.read()

    total_lines = sum(1 for line in text.strip().split("\n") if line.strip())
    turns = _extract_key_turns(text)

    n_user = sum(1 for t in turns if t["role"] == "user")
    n_assistant = sum(1 for t in turns if t["role"] == "assistant")
    n_tool = sum(1 for t in turns if t["role"] == "tool_use")

    print(f"\n{'=' * 60}")
    print(f"文件: {filepath}")
    print(f"大小: {filesize:,} bytes | JSONL 行数: {total_lines}")
    print(f"提取 turns: {len(turns)} (用户: {n_user}, 助手: {n_assistant}, 工具: {n_tool})")
    print(f"{'=' * 60}")

    if show_raw and turns:
        print("\n--- 原始 turns ---")
        for i, t in enumerate(turns):
            if t["role"] == "user":
                txt = t["text"][:200] + ("..." if len(t["text"]) > 200 else "")
                print(f"  {i:3d} [user]      {txt}")
            elif t["role"] == "assistant":
                txt = t["text"][:200] + ("..." if len(t["text"]) > 200 else "")
                print(f"  {i:3d} [assistant] {txt}")
            elif t["role"] == "tool_use":
                print(f"  {i:3d} [tool_use]  {t['tool']}: {t['input_preview']}")

    conv = {"filename": filename, "turns": turns}
    result = _format_conversations([conv])
    formatted_text = result[0] if result else "(空)"

    print(f"\n--- 格式化结果 ---")
    print(formatted_text)
    print(f"\n--- 格式化长度: {len(formatted_text):,} 字符 ---")

    return {
        "file": filename,
        "size": filesize,
        "turns": len(turns),
        "user": n_user,
        "assistant": n_assistant,
        "tool": n_tool,
        "formatted_len": len(formatted_text),
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    target = sys.argv[1]
    show_raw = "--raw" in sys.argv

    files = []
    if os.path.isdir(target):
        for f in sorted(os.listdir(target)):
            if f.endswith(".jsonl"):
                files.append(os.path.join(target, f))
        if not files:
            print(f"目录 {target} 中没有 .jsonl 文件")
            sys.exit(1)
    elif os.path.isfile(target):
        files.append(target)
    else:
        print(f"路径不存在: {target}")
        sys.exit(1)

    stats = []
    for fp in files:
        stats.append(process_file(fp, show_raw=show_raw))

    if len(stats) > 1:
        print(f"\n{'=' * 60}")
        print(f"汇总: {len(stats)} 个文件")
        print(f"{'=' * 60}")
        total_turns = sum(s["turns"] for s in stats)
        total_user = sum(s["user"] for s in stats)
        total_assistant = sum(s["assistant"] for s in stats)
        total_tool = sum(s["tool"] for s in stats)
        total_formatted = sum(s["formatted_len"] for s in stats)
        print(f"  总 turns: {total_turns} (用户: {total_user}, 助手: {total_assistant}, 工具: {total_tool})")
        print(f"  格式化总长度: {total_formatted:,} 字符")
        empty = [s["file"] for s in stats if s["turns"] == 0]
        if empty:
            print(f"  空文件 ({len(empty)}): {', '.join(empty[:5])}{'...' if len(empty)>5 else ''}")


if __name__ == "__main__":
    main()
