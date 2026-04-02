"""F13 Playbook 功能测试：JSONL 解析 + 历史目录定位 + LLM 分析 + 持久化

运行方式：
  cd /root/workspace/host/AstrBot
  uv run python -m pytest data/plugins/astrbot_plugin_hapi_connector/test_playbook.py -v
"""

import asyncio
import base64
import json
import os
import sys
import types

import pytest

# ──── 路径和包设置 ────
# 确保 AstrBot 根目录在 sys.path 中
_astrbot_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _astrbot_root not in sys.path:
    sys.path.insert(0, _astrbot_root)

_pkg_name = "data.plugins.astrbot_plugin_hapi_connector"

# ──── stub 外部依赖（在 import 插件模块之前） ────

# stub astrbot.api 及子模块
if "astrbot.api" not in sys.modules:
    for mod_name in ["astrbot", "astrbot.api"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)


class _FakeLogger:
    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


sys.modules["astrbot.api"].logger = _FakeLogger()

# stub astrbot.api.event — 提供 AstrMessageEvent 等类名
_event_mod = types.ModuleType("astrbot.api.event")
_event_mod.AstrMessageEvent = type("AstrMessageEvent", (), {})
_event_mod.MessageChain = type("MessageChain", (), {})
_event_mod.filter = type("filter", (), {
    "on_llm_request": staticmethod(lambda: lambda f: f),
})()
sys.modules["astrbot.api.event"] = _event_mod

# stub astrbot.api.provider
_provider_mod = types.ModuleType("astrbot.api.provider")
_provider_mod.ProviderRequest = type("ProviderRequest", (), {})
sys.modules["astrbot.api.provider"] = _provider_mod

# Now import modules under test — use relative-style path
_plugin_dir = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(_plugin_dir))
_plugin_pkg = os.path.basename(_plugin_dir)

# Import via importlib for reliable resolution
import importlib
_session_ops = importlib.import_module(f"{_plugin_pkg}.session_ops")
_state_mgr_mod = importlib.import_module(f"{_plugin_pkg}.state_manager")
_llm_mod = importlib.import_module(f"{_plugin_pkg}.llm_integration")
_binding_mod = importlib.import_module(f"{_plugin_pkg}.binding_manager")

_extract_key_turns = _session_ops._extract_key_turns
find_cc_history_dir = _session_ops.find_cc_history_dir
read_cc_conversations = _session_ops.read_cc_conversations
StateManager = _state_mgr_mod.StateManager
LLMIntegration = _llm_mod.LLMIntegration
BindingManager = _binding_mod.BindingManager


# ──── 测试用 JSONL 数据 ────

SAMPLE_JSONL_SIMPLE = "\n".join([
    json.dumps({"role": "human", "content": "帮我写一个快速排序函数"}),
    json.dumps({"role": "assistant", "content": [
        {"type": "text", "text": "好的，我来帮你写一个快速排序。"},
        {"type": "tool_use", "name": "Write", "input": {"file_path": "/src/sort.py", "content": "def quicksort..."}},
    ]}),
    json.dumps({"role": "human", "content": "测试一下"}),
    json.dumps({"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "cd /src && python -m pytest test_sort.py"}},
        {"type": "text", "text": "测试全部通过了！"},
    ]}),
])

SAMPLE_JSONL_COMPLEX = "\n".join([
    json.dumps({"role": "user", "content": [
        {"type": "text", "text": "重构 payment.py 中的 process_payment 为异步"},
    ]}),
    json.dumps({"role": "assistant", "content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/src/payment.py"}},
    ]}),
    json.dumps({"role": "tool", "content": "file content here..."}),
    json.dumps({"role": "assistant", "content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/src/payment.py", "old_string": "def process", "new_string": "async def process"}},
        {"type": "text", "text": "已将 process_payment 改为异步函数"},
    ]}),
    json.dumps({"role": "human", "content": "别忘了跑测试"}),
    json.dumps({"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/"}},
        {"type": "text", "text": "3 个测试通过，0 个失败"},
    ]}),
    json.dumps({"role": "human", "content": "commit 一下，用英文 message"}),
    json.dumps({"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "git add -A && git commit -m 'refactor: make process_payment async'"}},
    ]}),
])

SAMPLE_JSONL_WITH_NOISE = "\n".join([
    json.dumps({"role": "system", "content": "You are a helpful assistant"}),
    json.dumps({"role": "human", "content": "查看 config.ts"}),
    "",
    "not valid json",
    json.dumps({"role": "assistant", "content": [
        {"type": "text", "text": "这是 config.ts 的内容："},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/src/config.ts"}},
    ]}),
    json.dumps({"role": "tool", "tool_use_id": "xxx", "content": [{"type": "tool_result"}]}),
])


# ════════════════════════════════════
# 1. _extract_key_turns 测试
# ════════════════════════════════════

class TestExtractKeyTurns:
    """JSONL 对话片段��取"""

    def test_simple_conversation(self):
        turns = _extract_key_turns(SAMPLE_JSONL_SIMPLE)
        user_turns = [t for t in turns if t["role"] == "user"]
        tool_turns = [t for t in turns if t["role"] == "tool_use"]
        text_turns = [t for t in turns if t["role"] == "assistant"]

        assert len(user_turns) == 2
        assert "快速排序" in user_turns[0]["text"]
        assert "测试" in user_turns[1]["text"]
        assert len(tool_turns) == 2
        assert {t["tool"] for t in tool_turns} == {"Write", "Bash"}
        assert len(text_turns) == 2

    def test_complex_conversation(self):
        turns = _extract_key_turns(SAMPLE_JSONL_COMPLEX)
        user_turns = [t for t in turns if t["role"] == "user"]
        tool_turns = [t for t in turns if t["role"] == "tool_use"]

        assert len(user_turns) == 3
        assert "重构" in user_turns[0]["text"]
        assert "测试" in user_turns[1]["text"]
        assert "commit" in user_turns[2]["text"]
        tool_names = {t["tool"] for t in tool_turns}
        assert {"Read", "Edit", "Bash"}.issubset(tool_names)

    def test_noise_handling(self):
        """系统消息、空行、非法 JSON、tool_result 应被跳过"""
        turns = _extract_key_turns(SAMPLE_JSONL_WITH_NOISE)
        roles = {t["role"] for t in turns}
        assert "user" in roles
        assert "system" not in roles
        assert "tool" not in roles

    def test_string_content(self):
        """content 为纯字符串时也应正确提取"""
        jsonl = "\n".join([
            json.dumps({"role": "human", "content": "纯字符串内容"}),
            json.dumps({"role": "assistant", "content": "好的，收到"}),
        ])
        turns = _extract_key_turns(jsonl)
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

    def test_empty_input(self):
        assert _extract_key_turns("") == []
        assert _extract_key_turns("   \n  \n  ") == []

    def test_user_text_no_truncation(self):
        """用户消息不截断，完整保留"""
        long_msg = "x" * 500
        jsonl = json.dumps({"role": "human", "content": long_msg})
        turns = _extract_key_turns(jsonl)
        assert len(turns[0]["text"]) == 500

    def test_tool_input_preview_no_truncation(self):
        """tool input preview 不截断，完整保留"""
        big_input = {"file_path": "/" + "x" * 200, "content": "y" * 200}
        jsonl = json.dumps({"role": "assistant", "content": [
            {"type": "tool_use", "name": "Write", "input": big_input},
        ]})
        turns = _extract_key_turns(jsonl)
        # 完整 input dict 的字符串表示应该远超 80 字符
        assert len(turns[0]["input_preview"]) > 200

    def test_both_human_and_user_roles(self):
        """Claude Code JSONL 中 role 可能是 human 或 user"""
        jsonl = "\n".join([
            json.dumps({"role": "human", "content": "指令A"}),
            json.dumps({"role": "user", "content": [{"type": "text", "text": "指令B"}]}),
        ])
        turns = _extract_key_turns(jsonl)
        assert len(turns) == 2
        assert turns[0]["text"] == "指令A"
        assert turns[1]["text"] == "指令B"


# ════════════════════════════════════
# 2. find_cc_history_dir 测试
# ════════════════════════════════════

class TestFindCCHistoryDir:
    """Claude Code 历史目录定位"""

    def test_direct_match(self):
        """方案 A：路径规则直接推算命中"""
        async def fake_list_directory(client, sid, path):
            if path == "/root/.claude/projects/-root-workspace-host":
                return [{"name": "abc.jsonl", "type": "file"}]
            return []

        orig = _session_ops.list_directory
        _session_ops.list_directory = fake_list_directory
        try:
            result = asyncio.run(find_cc_history_dir(None, "sid", "/root/workspace/host"))
            assert result == "/root/.claude/projects/-root-workspace-host"
        finally:
            _session_ops.list_directory = orig

    def test_fallback_search(self):
        """方案 B：直接推算失败，遍历 projects 目录匹配"""
        async def fake_list_directory(client, sid, path):
            if path == "/root/.claude/projects/-my-project":
                return []
            if path == "/root/.claude/projects":
                return [
                    {"name": "other-stuff", "type": "directory"},
                    {"name": "-home-user-my-project", "type": "directory"},
                ]
            return []

        orig = _session_ops.list_directory
        _session_ops.list_directory = fake_list_directory
        try:
            result = asyncio.run(find_cc_history_dir(None, "sid", "/my/project"))
            assert result == "/root/.claude/projects/-home-user-my-project"
        finally:
            _session_ops.list_directory = orig

    def test_not_found(self):
        """两种方案都失败时返回 None"""
        async def fake_list_directory(client, sid, path):
            return []

        orig = _session_ops.list_directory
        _session_ops.list_directory = fake_list_directory
        try:
            result = asyncio.run(find_cc_history_dir(None, "sid", "/nonexistent"))
            assert result is None
        finally:
            _session_ops.list_directory = orig


# ════════════════════════════════════
# 3. read_cc_conversations 测试
# ════════════════════════════════════

class TestReadCCConversations:
    """全量 JSONL 文件读取与解析"""

    def test_read_and_parse_all_files(self):
        """读取所有 JSONL 文件，跳过非 JSONL"""
        so = _session_ops

        b64_simple = base64.b64encode(SAMPLE_JSONL_SIMPLE.encode()).decode()
        b64_complex = base64.b64encode(SAMPLE_JSONL_COMPLEX.encode()).decode()

        async def fake_list_directory(client, sid, path):
            return [
                {"name": "conv1.jsonl", "type": "file", "modified": "2026-04-01T10:00:00"},
                {"name": "conv2.jsonl", "type": "file", "modified": "2026-04-01T09:00:00"},
                {"name": "notes.txt", "type": "file", "modified": "2026-04-01T08:00:00"},
            ]

        async def fake_read_file(client, sid, path):
            if "conv1" in path:
                return True, b64_simple
            if "conv2" in path:
                return True, b64_complex
            return False, "not found"

        orig_ld, orig_rf = so.list_directory, so.read_file
        so.list_directory = fake_list_directory
        so.read_file = fake_read_file
        try:
            convs = asyncio.run(read_cc_conversations(None, "sid", "/history"))
            assert len(convs) == 2
            assert convs[0]["filename"] == "conv1.jsonl"
            assert convs[1]["filename"] == "conv2.jsonl"
            assert len(convs[0]["turns"]) > 0
            assert len(convs[1]["turns"]) > 0
        finally:
            so.list_directory = orig_ld
            so.read_file = orig_rf

    def test_skip_unreadable_files(self):
        """读取失败的文件被跳过"""
        so = _session_ops

        b64 = base64.b64encode(SAMPLE_JSONL_SIMPLE.encode()).decode()

        async def fake_list_directory(client, sid, path):
            return [
                {"name": "good.jsonl", "type": "file", "modified": "2026-04-01T10:00:00"},
                {"name": "bad.jsonl", "type": "file", "modified": "2026-04-01T09:00:00"},
            ]

        async def fake_read_file(client, sid, path):
            if "good" in path:
                return True, b64
            return False, "read error"

        orig_ld, orig_rf = so.list_directory, so.read_file
        so.list_directory = fake_list_directory
        so.read_file = fake_read_file
        try:
            convs = asyncio.run(read_cc_conversations(None, "sid", "/history"))
            assert len(convs) == 1
            assert convs[0]["filename"] == "good.jsonl"
        finally:
            so.list_directory = orig_ld
            so.read_file = orig_rf

    def test_no_file_count_limit(self):
        """验证不限制文件数量，全部读取"""
        so = _session_ops

        b64 = base64.b64encode(
            json.dumps({"role": "human", "content": "hello"}).encode()
        ).decode()

        async def fake_list_directory(client, sid, path):
            return [
                {"name": f"conv{i:03d}.jsonl", "type": "file", "modified": f"2026-01-{i+1:02d}T00:00:00"}
                for i in range(25)
            ]

        async def fake_read_file(client, sid, path):
            return True, b64

        orig_ld, orig_rf = so.list_directory, so.read_file
        so.list_directory = fake_list_directory
        so.read_file = fake_read_file
        try:
            convs = asyncio.run(read_cc_conversations(None, "sid", "/history"))
            assert len(convs) == 25
        finally:
            so.list_directory = orig_ld
            so.read_file = orig_rf


# ════════════════════════════════════
# 4. LLM 分析测试
# ════════════════════════════════════

class TestAnalyzeHistoryWithLLM:
    """LLM 调用分析历史对话生成 playbook"""

    def _make_instance(self, provider, config=None):
        """构造最小 LLMIntegration 实例"""
        class FakeContext:
            def get_using_provider(self, umo=None):
                return provider

        class FakePlugin:
            context = FakeContext()

        FakePlugin.config = config or {}

        class FakeEvent:
            unified_msg_origin = "test_umo"

        inst = object.__new__(LLMIntegration)
        inst.plugin = FakePlugin()
        return inst, FakeEvent()

    def _format(self, conversations):
        """辅助：将对话列表格式化为文本段落"""
        return LLMIntegration._format_conversations(conversations)

    def test_generates_playbook(self):
        """正常流程：LLM 返回 playbook"""
        expected = "## 有效做法\n- 修改前先读取文件\n## 应避免\n- 不要盲改"

        class FakeResp:
            completion_text = expected

        class FakeProvider:
            async def text_chat(self, system_prompt, prompt):
                assert "重构" in prompt
                return FakeResp()

        inst, event = self._make_instance(FakeProvider())
        convs = [{"filename": "c.jsonl", "turns": _extract_key_turns(SAMPLE_JSONL_COMPLEX)}]
        formatted = self._format(convs)

        result = asyncio.run(inst._analyze_history_with_llm(formatted, "/src/project", event))
        assert result is not None
        assert "有效做法" in result

    def test_llm_failure_returns_none(self):
        """LLM 调用异常返回 None"""
        class FakeProvider:
            async def text_chat(self, system_prompt, prompt):
                raise RuntimeError("API error")

        inst, event = self._make_instance(FakeProvider())
        formatted = self._format([{"filename": "x.jsonl", "turns": [{"role": "user", "text": "hi"}]}])
        result = asyncio.run(inst._analyze_history_with_llm(formatted, "/src", event))
        assert result is None

    def test_no_provider_returns_none(self):
        """无可用 provider 返回 None"""
        inst, event = self._make_instance(None)
        formatted = self._format([{"filename": "x.jsonl", "turns": [{"role": "user", "text": "hi"}]}])
        result = asyncio.run(inst._analyze_history_with_llm(formatted, "/src", event))
        assert result is None

    def test_short_content_single_call(self):
        """短内容（<10000 字符）一次性发给 LLM"""
        received_prompts = []

        class FakeResp:
            completion_text = "playbook"

        class FakeProvider:
            async def text_chat(self, system_prompt, prompt):
                received_prompts.append(prompt)
                return FakeResp()

        inst, event = self._make_instance(FakeProvider())
        convs = [
            {"filename": f"conv{i}.jsonl", "turns": _extract_key_turns(SAMPLE_JSONL_COMPLEX)}
            for i in range(3)
        ]
        formatted = self._format(convs)

        asyncio.run(inst._analyze_history_with_llm(formatted, "/project", event))
        # 短内容只调用一次
        assert len(received_prompts) == 1
        # 验证所有对话内容都在 prompt 中
        for i in range(3):
            assert f"conv{i}.jsonl" in received_prompts[0]

    def test_long_content_segmented(self):
        """长内容自动分段总结，每段携带前段摘要"""
        call_log = []

        class FakeResp:
            def __init__(self, text):
                self.completion_text = text

        class FakeProvider:
            async def text_chat(self, system_prompt, prompt):
                call_log.append(prompt)
                return FakeResp(f"摘要{len(call_log)}")

        inst, event = self._make_instance(FakeProvider())
        # 构造超过 10000 字符的内容
        big_turn = {"role": "user", "text": "x" * 4000}
        convs = [
            {"filename": f"conv{i}.jsonl", "turns": [big_turn]}
            for i in range(5)
        ]
        formatted = self._format(convs)
        total_len = sum(len(s) for s in formatted)
        assert total_len > 10000, f"测试数据不够长: {total_len}"

        result = asyncio.run(inst._analyze_history_with_llm(
            formatted, "/project", event, segment_threshold=10000,
        ))

        # 应该分了多段
        assert len(call_log) >= 2, f"应该分段但只调用了 {len(call_log)} 次"
        # 第一段不含前段摘要
        assert "前面内容的分析结果" not in call_log[0]
        # 第二段及之后应包含前段摘要
        assert "前面内容的分析结果" in call_log[1]
        assert "摘要1" in call_log[1]
        # 最终结果应是最后一段的摘要
        assert result == f"摘要{len(call_log)}"

    def test_segment_failure_continues(self):
        """某段总结失败时保留前段结果继续"""
        call_count = 0

        class FakeResp:
            def __init__(self, text):
                self.completion_text = text

        class FakeProvider:
            async def text_chat(self, system_prompt, prompt):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise RuntimeError("second segment fails")
                return FakeResp(f"摘要{call_count}")

        inst, event = self._make_instance(FakeProvider())
        big_turn = {"role": "user", "text": "y" * 4000}
        convs = [
            {"filename": f"c{i}.jsonl", "turns": [big_turn]}
            for i in range(5)
        ]
        formatted = self._format(convs)

        result = asyncio.run(inst._analyze_history_with_llm(
            formatted, "/proj", event, segment_threshold=10000,
        ))
        # 第2段失败，但第1段的摘要被保留，第3段继续用它
        assert result is not None


# ════════════════════════════════════
# 5. Playbook 持久化测试
# ════════════════════════════════════

class FakeKV:
    """内存 KV 存储"""
    def __init__(self):
        self._store = {}

    async def put_kv_data(self, key, value):
        if value is None:
            self._store.pop(key, None)
        else:
            self._store[key] = value

    async def get_kv_data(self, key, default=None):
        return self._store.get(key, default)


class TestPlaybookPersistence:
    """Playbook 按工作目录持久化"""

    def test_set_get_by_work_dir(self):
        """按工作目录存取"""
        sm = StateManager(FakeKV(), BindingManager())
        sm.set_playbook("/root/workspace/host", "## 有效做法\n- 测试")
        assert sm.get_playbook("/root/workspace/host") == "## 有效做法\n- 测试"
        assert sm.get_playbook("/other/path") is None

    def test_persist_and_reload(self):
        """持久化后重新加载能恢复"""
        kv = FakeKV()
        sm = StateManager(kv, BindingManager())
        sm.set_playbook("/root/workspace/host", "playbook content")

        async def run():
            await sm.persist_playbook("/root/workspace/host")
            await sm.persist_playbook_index()

            # 模拟重启
            sm2 = StateManager(kv, BindingManager())
            await sm2.load_all()
            return sm2

        sm2 = asyncio.run(run())
        assert sm2.get_playbook("/root/workspace/host") == "playbook content"

    def test_survives_session_deletion(self):
        """session 删除后 playbook 仍在（按 work_dir 存储）"""
        kv = FakeKV()
        sm = StateManager(kv, BindingManager())

        async def run():
            sm._session_owners["sid-123"] = "umo-1"
            sm.set_playbook("/my/project", "important playbook")
            await sm.persist_playbook("/my/project")
            await sm.persist_playbook_index()

            # 删除 session
            del sm._session_owners["sid-123"]

            # 重新加载
            sm2 = StateManager(kv, BindingManager())
            await sm2.load_all()
            return sm2.get_playbook("/my/project")

        result = asyncio.run(run())
        assert result == "important playbook"

    def test_multiple_playbooks(self):
        """多个工作目录各自存储"""
        kv = FakeKV()
        sm = StateManager(kv, BindingManager())

        async def run():
            sm.set_playbook("/project-a", "playbook A")
            sm.set_playbook("/project-b", "playbook B")
            await sm.persist_playbook("/project-a")
            await sm.persist_playbook("/project-b")
            await sm.persist_playbook_index()

            sm2 = StateManager(kv, BindingManager())
            await sm2.load_all()
            return sm2

        sm2 = asyncio.run(run())
        assert sm2.get_playbook("/project-a") == "playbook A"
        assert sm2.get_playbook("/project-b") == "playbook B"

    def test_clear_playbook(self):
        """清除 playbook"""
        sm = StateManager(FakeKV(), BindingManager())
        sm.set_playbook("/proj", "content")
        assert sm.get_playbook("/proj") is not None
        sm.clear_playbook("/proj")
        assert sm.get_playbook("/proj") is None


# ════════════════════════════════════
# 6. 真实 JSONL 文件集成测试
# ════════════════════════════════════

REAL_HISTORY_DIR = "/root/.claude/projects/-root-workspace-aurimo-dev"


@pytest.mark.skipif(
    not os.path.isdir(REAL_HISTORY_DIR),
    reason=f"真实历史目录不存在: {REAL_HISTORY_DIR}",
)
class TestRealJSONL:
    """使用真实 Claude Code JSONL 文件的集成测试"""

    def _all_jsonl_files(self):
        return sorted(
            f for f in os.listdir(REAL_HISTORY_DIR) if f.endswith(".jsonl")
        )

    def test_all_files_parse_without_error(self):
        """所有 JSONL 文件都能无异常解析"""
        for fname in self._all_jsonl_files():
            with open(os.path.join(REAL_HISTORY_DIR, fname)) as f:
                turns = _extract_key_turns(f.read())
            # 不抛异常即为通过；非空文件应至少有 1 个 turn
            assert isinstance(turns, list), f"{fname} 返回类型错误"

    def test_nested_format_extracted(self):
        """真实文件的嵌套格式（type+message）能正确提取"""
        total_turns = 0
        for fname in self._all_jsonl_files():
            with open(os.path.join(REAL_HISTORY_DIR, fname)) as f:
                turns = _extract_key_turns(f.read())
            total_turns += len(turns)
        # 21 个文件应提取出大量 turns
        assert total_turns > 100, f"提取的 turns 数量过少: {total_turns}"

    def test_role_distribution(self):
        """提取结果应包含 user/tool_use/assistant 三种角色"""
        all_roles: set[str] = set()
        for fname in self._all_jsonl_files():
            with open(os.path.join(REAL_HISTORY_DIR, fname)) as f:
                turns = _extract_key_turns(f.read())
            all_roles.update(t["role"] for t in turns)
        assert "user" in all_roles
        assert "tool_use" in all_roles
        assert "assistant" in all_roles

    def test_large_file_performance(self):
        """最大文件（35MB）解析耗时应小于 5 秒"""
        import time
        largest = max(
            self._all_jsonl_files(),
            key=lambda f: os.path.getsize(os.path.join(REAL_HISTORY_DIR, f)),
        )
        path = os.path.join(REAL_HISTORY_DIR, largest)
        size_mb = os.path.getsize(path) / (1024 * 1024)

        t0 = time.time()
        with open(path) as f:
            turns = _extract_key_turns(f.read())
        elapsed = time.time() - t0

        assert elapsed < 5.0, f"{largest} ({size_mb:.1f}MB) 解析耗时 {elapsed:.2f}s > 5s"
        assert len(turns) > 0, f"最大文件竟然没有提取到任何 turns"

    def test_tool_names_are_valid(self):
        """提取的工具名应为已知的 Claude Code 工具"""
        known_tools = {
            "Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent",
            "ToolSearch", "TodoWrite", "WebFetch", "WebSearch",
            "NotebookEdit", "AskUserQuestion",
        }
        all_tool_names: set[str] = set()
        for fname in self._all_jsonl_files():
            with open(os.path.join(REAL_HISTORY_DIR, fname)) as f:
                turns = _extract_key_turns(f.read())
            for t in turns:
                if t["role"] == "tool_use":
                    all_tool_names.add(t["tool"])

        # 至少有一些已知工具被提取
        overlap = known_tools & all_tool_names
        assert len(overlap) >= 3, f"只识别到 {overlap}，可能解析有问题"

    def test_format_for_llm_analysis(self):
        """验证提取结果格式化后适合发给 LLM 分析"""
        # 取一个中等大小的文件
        files = self._all_jsonl_files()
        mid_file = None
        for f in files:
            size = os.path.getsize(os.path.join(REAL_HISTORY_DIR, f))
            if 10000 < size < 500000:
                mid_file = f
                break
        if not mid_file:
            pytest.skip("没有找到合适大小的测试文件")

        with open(os.path.join(REAL_HISTORY_DIR, mid_file)) as f:
            turns = _extract_key_turns(f.read())

        # 模拟 LLM 分析时的格式化
        lines = [f"=== 对话: {mid_file} ==="]
        for turn in turns:
            if turn["role"] == "user":
                lines.append(f"[用户指令] {turn['text']}")
            elif turn["role"] == "tool_use":
                lines.append(f"[工具调用] {turn['tool']}: {turn.get('input_preview', '')}")
            elif turn["role"] == "assistant":
                lines.append(f"[助手回复] {turn['text']}")

        formatted = "\n".join(lines)
        assert len(formatted) > 100, "格式化后内容过短"
        assert "[用户指令]" in formatted
        assert "===" in formatted


# ════════════════════════════════════
# 7. 端到端集成测试
# ════════════════════════════════════

@pytest.mark.skipif(
    not os.path.isdir(REAL_HISTORY_DIR),
    reason=f"真实历史目录不存在: {REAL_HISTORY_DIR}",
)
class TestEndToEnd:
    """端到端测试：模拟用户对 /root/workspace/aurimo-dev 目录下的 session 执行 learn，
    走完从 JSONL 读取 → 解析 → 格式化 → 分段 LLM 总结 → 持久化的完整流程。
    LLM 调用用 mock 替代：打印输入 prompt，返回假摘要。
    """

    def test_full_learn_flow(self, capsys):
        """完整 learn 流程：读真实 JSONL → 分段 mock LLM → 存储 playbook"""

        # ── 准备 mock 组件 ──

        work_dir = "/root/workspace/aurimo-dev"
        fake_sid = "test-sid-e2e-001"
        llm_call_count = 0

        class FakeResp:
            def __init__(self, text):
                self.completion_text = text

        class FakeProvider:
            async def text_chat(self, system_prompt, prompt):
                nonlocal llm_call_count
                llm_call_count += 1
                # 打印 prompt 前 500 字符供人工检查
                print(f"\n{'='*60}")
                print(f"[LLM 调用 #{llm_call_count}] prompt 长度: {len(prompt)} 字符")
                print(f"prompt 前 500 字符:\n{prompt[:500]}")
                print(f"{'='*60}")
                return FakeResp(f"第 {llm_call_count} 段落的总结")

        class FakeContext:
            def get_using_provider(self, umo=None):
                return FakeProvider()

        class FakeKV:
            def __init__(self):
                self._store = {}
            async def put_kv_data(self, key, value):
                self._store[key] = value
            async def get_kv_data(self, key, default=None):
                return self._store.get(key, default)

        class FakeEvent:
            unified_msg_origin = "test_umo_e2e"

        # ── HAPI API mock：从本地磁盘读取真实 JSONL 文件 ──

        async def fake_list_directory(client, sid, path):
            """代理到本地文件系统"""
            if os.path.isdir(path):
                entries = []
                for name in os.listdir(path):
                    full = os.path.join(path, name)
                    entry = {"name": name}
                    if os.path.isdir(full):
                        entry["type"] = "directory"
                    else:
                        entry["type"] = "file"
                        entry["modified"] = f"{os.path.getmtime(full)}"
                    entries.append(entry)
                return entries
            return []

        async def fake_read_file(client, sid, path):
            """代理到本地文件系统，返回 base64"""
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    content = base64.b64encode(f.read()).decode()
                return True, content
            return False, f"文件不存在: {path}"

        # ── 组装 LLMIntegration 实例 ──

        kv = FakeKV()
        binding_mgr = BindingManager()
        state_mgr = StateManager(kv, binding_mgr)

        # 模拟 session 数据
        fake_machine_id = "e41f502b-41cf-4260-ab8e-7fd3c2d14b61"
        fake_session = {
            "id": fake_sid,
            "machineId": fake_machine_id,
            "metadata": {"path": work_dir, "flavor": "claude"},
        }

        class FakePlugin:
            context = FakeContext()
            config = {}

        inst = object.__new__(LLMIntegration)
        inst.plugin = FakePlugin()
        inst.client = None  # HAPI client，会被 mock 函数接收
        inst.state_mgr = state_mgr
        inst.pending_mgr = None
        inst.sessions_cache = [fake_session]

        # mock effective_sid 返回我们的假 sid
        state_mgr.effective_sid = lambda event: fake_sid

        # mock session_ops 的 HAPI API 调用
        orig_ld = _session_ops.list_directory
        orig_rf = _session_ops.read_file
        _session_ops.list_directory = fake_list_directory
        _session_ops.read_file = fake_read_file

        try:
            # ── 执行 learn ──

            async def run():
                results = []
                async for msg in inst.tool_learn_from_history(FakeEvent(), ""):
                    results.append(msg)
                    print(f"[yield] {msg[:200] if isinstance(msg, str) else msg}")
                return results

            results = asyncio.run(run())
        finally:
            _session_ops.list_directory = orig_ld
            _session_ops.read_file = orig_rf

        # ── 验证输出 ──

        output = "\n".join(str(r) for r in results)
        print(f"\n{'#'*60}")
        print(f"总共 yield 了 {len(results)} 条消息")
        print(f"LLM 被调用了 {llm_call_count} 次")
        print(f"{'#'*60}")

        # 应该包含进度信息
        assert any("正在查找" in str(r) for r in results), "缺少'正在查找'进度消息"
        assert any("历史对话" in str(r) for r in results), "缺少对话数量报告"

        # 应该成功生成 playbook
        assert any("✅" in str(r) for r in results), "缺少成功标记"

        # LLM 至少被调用过
        assert llm_call_count >= 1, "LLM 一次都没调用"

        # playbook 应该被存储到 state_mgr（按 machine_id:work_dir 键）
        expected_key = f"{fake_machine_id}:{work_dir}"
        stored = state_mgr.get_playbook(expected_key)
        assert stored is not None, f"playbook 未存储到 state_mgr (key={expected_key})"
        assert "总结" in stored, f"playbook 内容异常: {stored}"
        print(f"\n最终 playbook: {stored}")

        # playbook 应该被持久化到 KV
        async def check_kv():
            pb = await kv.get_kv_data(f"playbook_{expected_key}")
            keys = await kv.get_kv_data("playbook_keys")
            return pb, keys

        pb_in_kv, keys_in_kv = asyncio.run(check_kv())
        assert pb_in_kv == stored, "KV 中的 playbook 与内存不一致"
        assert expected_key in keys_in_kv, "playbook_keys 索引中缺少 key"
        print(f"KV 持久化验证通过 ✓ (key={expected_key})")
