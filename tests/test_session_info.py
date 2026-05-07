"""session info / effort 子命令的单元测试.

运行方式:
  cd /root/workspace/host/AstrBot
  uv run pytest data/plugins/astrbot_plugin_hapi_connector/tests/test_session_info.py -v
"""

import importlib.util
import os
import sys
import types

import pytest


# ──── stub 相对导入依赖 ────

_pkg_name = "data.plugins.astrbot_plugin_hapi_connector"
_ops_pkg = f"{_pkg_name}.ops"
_ui_pkg = f"{_pkg_name}.ui"
_core_pkg = f"{_pkg_name}.core"
for _partial in [
    "data", "data.plugins", _pkg_name, _ops_pkg, _ui_pkg, _core_pkg,
]:
    if _partial not in sys.modules:
        sys.modules[_partial] = types.ModuleType(_partial)

# stub astrbot.api.logger
if "astrbot" not in sys.modules:
    sys.modules["astrbot"] = types.ModuleType("astrbot")
if "astrbot.api" not in sys.modules:
    _api = types.ModuleType("astrbot.api")
    import logging
    _api.logger = logging.getLogger("test_session_info")
    sys.modules["astrbot.api"] = _api

# stub core.hapi_client (session_ops imports AsyncHapiClient)
_hapi_client_mod = types.ModuleType(f"{_core_pkg}.hapi_client")


class _StubHapiClient:
    """Test fake — only used as a type placeholder in signatures."""


_hapi_client_mod.AsyncHapiClient = _StubHapiClient
sys.modules[f"{_core_pkg}.hapi_client"] = _hapi_client_mod

# load core.constants directly (no deps)
_const_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, "core", "constants.py",
)
_const_spec = importlib.util.spec_from_file_location(
    f"{_core_pkg}.constants", _const_path, submodule_search_locations=[],
)
_const_mod = importlib.util.module_from_spec(_const_spec)
_const_mod.__package__ = _core_pkg
sys.modules[f"{_core_pkg}.constants"] = _const_mod
_const_spec.loader.exec_module(_const_mod)

# load ops.session_ops
_ops_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, "ops", "session_ops.py",
)
_ops_spec = importlib.util.spec_from_file_location(
    f"{_ops_pkg}.session_ops", _ops_path, submodule_search_locations=[],
)
_ops_mod = importlib.util.module_from_spec(_ops_spec)
_ops_mod.__package__ = _ops_pkg
sys.modules[f"{_ops_pkg}.session_ops"] = _ops_mod
_ops_spec.loader.exec_module(_ops_mod)

session_ops = _ops_mod
aggregate_token_usage = _ops_mod.aggregate_token_usage
set_session_effort = _ops_mod.set_session_effort
set_session_reasoning_effort = _ops_mod.set_session_reasoning_effort

# load ui.formatters (only the function we need; module also imports zoneinfo etc.)
_fmt_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, "ui", "formatters.py",
)
_fmt_spec = importlib.util.spec_from_file_location(
    f"{_ui_pkg}.formatters", _fmt_path, submodule_search_locations=[],
)
_fmt_mod = importlib.util.module_from_spec(_fmt_spec)
_fmt_mod.__package__ = _ui_pkg
sys.modules[f"{_ui_pkg}.formatters"] = _fmt_mod
_fmt_spec.loader.exec_module(_fmt_mod)

format_session_info = _fmt_mod.format_session_info


# ──── 模拟 client + response ────

class FakeResp:
    def __init__(self, ok=True, status=200, body=""):
        self.ok = ok
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    def release(self):
        pass


class FakeClient:
    def __init__(self):
        self.calls = []
        self.next_response = FakeResp()

    async def post(self, path, json=None):
        self.calls.append({"path": path, "json": json})
        return self.next_response


# ════════════════════════════════════════
# aggregate_token_usage
# ════════════════════════════════════════

class TestAggregateTokenUsage:

    def test_empty_messages_returns_no_data(self):
        result = aggregate_token_usage([])
        assert result["has_data"] is False
        assert result["samples"] == 0
        assert result["latest_input"] == 0
        assert result["cumulative_output"] == 0

    def test_messages_without_usage_field_skipped(self):
        msgs = [
            {"seq": 1, "content": {"role": "user", "message": {"role": "user"}}},
            {"seq": 2, "content": {"role": "assistant"}},
        ]
        result = aggregate_token_usage(msgs)
        assert result["has_data"] is False
        assert result["samples"] == 0

    def test_extracts_usage_from_top_level_content(self):
        msgs = [
            {"seq": 5, "content": {
                "role": "assistant",
                "usage": {"input_tokens": 1000, "output_tokens": 200,
                          "cache_read_input_tokens": 800},
            }},
        ]
        result = aggregate_token_usage(msgs)
        assert result["has_data"] is True
        assert result["latest_input"] == 1000
        assert result["latest_cached"] == 800
        assert result["latest_output"] == 200
        assert result["cumulative_output"] == 200
        assert result["latest_seq"] == 5
        assert result["samples"] == 1

    def test_extracts_usage_from_wrapped_message(self):
        msgs = [
            {"seq": 10, "content": {
                "role": "assistant",
                "message": {
                    "role": "assistant",
                    "usage": {"input_tokens": 2000, "output_tokens": 300,
                              "cache_creation_input_tokens": 100},
                },
            }},
        ]
        result = aggregate_token_usage(msgs)
        assert result["has_data"] is True
        assert result["latest_input"] == 2000
        assert result["latest_cached"] == 100  # cache_creation counted
        assert result["latest_output"] == 300

    def test_latest_snapshot_uses_max_seq(self):
        msgs = [
            {"seq": 3, "content": {"usage": {"input_tokens": 500, "output_tokens": 50}}},
            {"seq": 7, "content": {"usage": {"input_tokens": 1500, "output_tokens": 80}}},
            {"seq": 5, "content": {"usage": {"input_tokens": 1000, "output_tokens": 60}}},
        ]
        result = aggregate_token_usage(msgs)
        # latest = seq 7
        assert result["latest_seq"] == 7
        assert result["latest_input"] == 1500
        assert result["latest_output"] == 80
        # cumulative = 50 + 80 + 60
        assert result["cumulative_output"] == 190
        assert result["samples"] == 3

    def test_combined_cache_read_and_creation(self):
        msgs = [
            {"seq": 1, "content": {"usage": {
                "input_tokens": 3000,
                "output_tokens": 100,
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 500,
            }}},
        ]
        result = aggregate_token_usage(msgs)
        assert result["latest_cached"] == 2500


# ════════════════════════════════════════
# format_session_info
# ════════════════════════════════════════

class TestFormatSessionInfo:

    def _detail(self, **overrides):
        base = {
            "id": "abcdef1234",
            "metadata": {"flavor": "claude", "path": "/tmp/test",
                         "summary": {"text": "test session"}},
            "permissionMode": "default",
            "active": True,
            "thinking": False,
            "modelMode": "default",
        }
        base.update(overrides)
        return base

    def test_renders_claude_effort_when_set(self):
        detail = self._detail(effort="high")
        text = format_session_info(detail, None, None)
        assert "Effort:    high" in text

    def test_renders_claude_effort_auto_when_unset(self):
        detail = self._detail(effort=None)
        text = format_session_info(detail, None, None)
        assert "Effort:    auto" in text

    def test_renders_codex_reasoning_effort(self):
        detail = self._detail(metadata={"flavor": "codex", "path": "/x", "summary": {"text": "t"}},
                              modelReasoningEffort="medium")
        text = format_session_info(detail, None, None)
        assert "Reasoning: medium" in text
        # codex 不显示 Claude effort
        assert "Effort:" not in text

    def test_no_effort_line_for_other_flavor(self):
        detail = self._detail(metadata={"flavor": "gemini", "path": "/x", "summary": {"text": "t"}})
        text = format_session_info(detail, None, None)
        assert "Effort:" not in text
        assert "Reasoning:" not in text

    def test_uses_models_currentModel_when_provided(self):
        detail = self._detail()
        models = {"currentModel": "sonnet[1m]", "presets": [], "flavor": "claude"}
        text = format_session_info(detail, models, None)
        assert "模型:     sonnet[1m]" in text

    def test_falls_back_to_modelMode(self):
        detail = self._detail(modelMode="opus")
        text = format_session_info(detail, None, None)
        assert "模型:     opus" in text

    def test_renders_token_usage_when_available(self):
        detail = self._detail()
        usage = {"has_data": True, "latest_input": 12345, "latest_cached": 8000,
                 "latest_output": 600, "cumulative_output": 4500, "samples": 7}
        text = format_session_info(detail, None, usage)
        assert "12,345" in text
        assert "8,000" in text
        assert "600" in text
        assert "4,500" in text
        assert "最近 7 条" in text

    def test_renders_no_token_usage_message_when_empty(self):
        detail = self._detail()
        text = format_session_info(detail, None, None)
        assert "Token 用量: 暂无" in text


# ════════════════════════════════════════
# set_session_effort / set_session_reasoning_effort
# ════════════════════════════════════════

class TestSetEffort:

    @pytest.mark.asyncio
    async def test_set_session_effort_calls_correct_endpoint(self):
        client = FakeClient()
        ok, msg = await set_session_effort(client, "sid123", "high")
        assert ok is True
        assert client.calls[0]["path"] == "/api/sessions/sid123/effort"
        assert client.calls[0]["json"] == {"effort": "high"}
        assert "high" in msg

    @pytest.mark.asyncio
    async def test_set_session_effort_passes_null_for_reset(self):
        client = FakeClient()
        ok, msg = await set_session_effort(client, "sid", None)
        assert ok is True
        assert client.calls[0]["json"] == {"effort": None}
        assert "auto" in msg

    @pytest.mark.asyncio
    async def test_set_session_effort_failure_returns_error(self):
        client = FakeClient()
        client.next_response = FakeResp(ok=False, status=400, body="bad request")
        ok, msg = await set_session_effort(client, "sid", "low")
        assert ok is False
        assert "400" in msg

    @pytest.mark.asyncio
    async def test_set_reasoning_effort_uses_codex_path(self):
        client = FakeClient()
        ok, msg = await set_session_reasoning_effort(client, "sid", "xhigh")
        assert ok is True
        assert client.calls[0]["path"] == "/api/sessions/sid/model-reasoning-effort"
        assert client.calls[0]["json"] == {"modelReasoningEffort": "xhigh"}
        assert "xhigh" in msg

    @pytest.mark.asyncio
    async def test_set_reasoning_effort_null_for_reset(self):
        client = FakeClient()
        ok, msg = await set_session_reasoning_effort(client, "sid", None)
        assert ok is True
        assert client.calls[0]["json"] == {"modelReasoningEffort": None}
        assert "default" in msg
