"""TakeoverManager 单元测试

运行方式：
  cd /root/workspace/host/AstrBot
  uv run pytest data/plugins/astrbot_plugin_hapi_connector/tests/test_takeover.py -v
"""

import asyncio
import json
import sys
import types
import pytest

# ──── stub 相对导入依赖（同 test_auto_decision.py 模式） ────

_pkg_name = "data.plugins.astrbot_plugin_hapi_connector"
_ops_pkg = f"{_pkg_name}.ops"
_llm_pkg = f"{_pkg_name}.llm"
_ui_pkg = f"{_pkg_name}.ui"
for _partial in [
    "data", "data.plugins", _pkg_name, _ops_pkg, _llm_pkg, _ui_pkg,
]:
    if _partial not in sys.modules:
        sys.modules[_partial] = types.ModuleType(_partial)

# stub session_ops
_session_ops = types.ModuleType(f"{_ops_pkg}.session_ops")

_send_message_log = []


async def _fake_send_message(client, sid, text, attachments=None):
    _send_message_log.append({"sid": sid, "text": text})
    return True, "OK"


async def _fake_fetch_messages(client, sid, limit=10):
    return []


_session_ops.send_message = _fake_send_message
_session_ops.fetch_messages = _fake_fetch_messages
sys.modules[f"{_ops_pkg}.session_ops"] = _session_ops

# stub formatters
_formatters = types.ModuleType(f"{_ui_pkg}.formatters")


def _fake_extract(content, max_len=0):
    return content.get("text", None)


_formatters.extract_text_preview = _fake_extract
sys.modules[f"{_ui_pkg}.formatters"] = _formatters
sys.modules[f"{_pkg_name}.formatters"] = _formatters

# stub astrbot.api.logger
if "astrbot" not in sys.modules:
    sys.modules["astrbot"] = types.ModuleType("astrbot")
if "astrbot.api" not in sys.modules:
    _api = types.ModuleType("astrbot.api")
    import logging
    _api.logger = logging.getLogger("test_takeover")
    sys.modules["astrbot.api"] = _api

# stub takeover_prompts
import importlib
import os

_prompts_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             os.pardir, "llm", "takeover_prompts.py")
_prompts_spec = importlib.util.spec_from_file_location(
    f"{_llm_pkg}.takeover_prompts", _prompts_path, submodule_search_locations=[])
_prompts_mod = importlib.util.module_from_spec(_prompts_spec)
sys.modules[f"{_llm_pkg}.takeover_prompts"] = _prompts_mod
_prompts_spec.loader.exec_module(_prompts_mod)

# 加载 takeover_manager
_tm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "llm", "takeover_manager.py")
_tm_spec = importlib.util.spec_from_file_location(
    f"{_llm_pkg}.takeover_manager", _tm_path, submodule_search_locations=[])
_tm_mod = importlib.util.module_from_spec(_tm_spec)
_tm_mod.__package__ = _llm_pkg
sys.modules[f"{_llm_pkg}.takeover_manager"] = _tm_mod
_tm_spec.loader.exec_module(_tm_mod)

TakeoverManager = _tm_mod.TakeoverManager
_find_next_pending = _tm_mod._find_next_pending
_find_task_by_id = _tm_mod._find_task_by_id
_insert_after = _tm_mod._insert_after
_count_tasks = _tm_mod._count_tasks
_format_plan_text = _tm_mod._format_plan_text
_completed_summary = _tm_mod._completed_summary
_create_task = _tm_mod._create_task


# ──── 模拟对象 ────

class FakeKV:
    def __init__(self):
        self.store = {}

    async def put_kv_data(self, key, value):
        self.store[key] = value

    async def get_kv_data(self, key, default=None):
        return self.store.get(key, default)


class FakeStateMgr:
    def __init__(self):
        self._takeover_plans = {}

    def select_notification_targets(self, sid, cache):
        return ["test_umo"]

    def set_takeover_plan(self, sid, plan):
        if plan is None:
            self._takeover_plans.pop(sid, None)
        else:
            self._takeover_plans[sid] = plan

    def get_takeover_plan(self, sid):
        return self._takeover_plans.get(sid)

    def get_all_takeover_plans(self):
        return dict(self._takeover_plans)

    async def persist_takeover_plan(self, sid):
        pass  # no-op in tests

    def get_playbook(self, key):
        return None


class FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeSSEListener:
    def __init__(self):
        self._lock = FakeLock()
        self.session_states = {"sid_001": {"lastSeq": 100}}
        self._pending_takeover_completions = {}
        self.sent_messages = []

    async def _send_user_message(self, umo, text):
        self.sent_messages.append({"umo": umo, "text": text})


class FakeContext:
    def __init__(self, llm_response=None):
        self._llm_response = llm_response

    async def get_current_chat_provider_id(self, umo=None):
        return "test_provider" if self._llm_response else None

    async def llm_generate(self, **kwargs):
        class Resp:
            completion_text = self._llm_response
        return Resp()


class FakePlugin:
    def __init__(self, llm_response=None):
        self.config = {"takeover_max_tasks": 10}
        self.client = None
        self.state_mgr = FakeStateMgr()
        self.context = FakeContext(llm_response)
        self.sessions_cache = [
            {"id": "sid_001", "machineId": "m1",
             "metadata": {"path": "/home/user/project", "flavor": "claude"}}
        ]
        self.sse_listener = FakeSSEListener()


# ──── 辅助 ────

def make_plan(status="confirming", tasks=None):
    """创建测试用 plan dict"""
    if tasks is None:
        tasks = [
            _create_task({"title": "任务1", "description": "描述1"}, 0),
            _create_task({"title": "任务2", "description": "描述2"}, 1),
            _create_task({"title": "任务3", "description": "描述3"}, 2),
        ]
    return {
        "id": "plan_test",
        "sid": "sid_001",
        "umo": "test_umo",
        "goal": "测试目标",
        "status": status,
        "tasks": tasks,
        "current_task_id": None,
        "created_at": 1000.0,
        "updated_at": 1000.0,
    }


# ════════════════════════════════════════
# 数据模型测试
# ════════════════════════════════════════

class TestDataModel:
    def test_create_task_basic(self):
        t = _create_task({"title": "测试", "description": "描述"})
        assert t["title"] == "测试"
        assert t["description"] == "描述"
        assert t["status"] == "pending"
        assert t["result_summary"] is None
        assert t["subtasks"] == []
        assert len(t["id"]) == 8

    def test_create_task_with_subtasks(self):
        t = _create_task({
            "title": "父任务",
            "description": "父描述",
            "subtasks": [
                {"title": "子1", "description": "子1描述"},
                {"title": "子2", "description": "子2描述"},
            ]
        })
        assert len(t["subtasks"]) == 2
        assert t["subtasks"][0]["title"] == "子1"
        assert t["subtasks"][1]["order"] == 1

    def test_find_next_pending_basic(self):
        tasks = [
            _create_task({"title": "t1", "description": ""}, 0),
            _create_task({"title": "t2", "description": ""}, 1),
        ]
        result = _find_next_pending(tasks)
        assert result["title"] == "t1"

    def test_find_next_pending_skips_done(self):
        tasks = [
            _create_task({"title": "t1", "description": ""}, 0),
            _create_task({"title": "t2", "description": ""}, 1),
        ]
        tasks[0]["status"] = "done"
        result = _find_next_pending(tasks)
        assert result["title"] == "t2"

    def test_find_next_pending_subtasks_first(self):
        tasks = [_create_task({
            "title": "parent",
            "description": "",
            "subtasks": [
                {"title": "child1", "description": ""},
                {"title": "child2", "description": ""},
            ]
        })]
        result = _find_next_pending(tasks)
        assert result["title"] == "child1"

    def test_find_next_pending_parent_done_when_subtasks_done(self):
        tasks = [
            _create_task({
                "title": "parent",
                "description": "",
                "subtasks": [{"title": "child", "description": ""}]
            }),
            _create_task({"title": "next", "description": ""}, 1),
        ]
        tasks[0]["subtasks"][0]["status"] = "done"
        result = _find_next_pending(tasks)
        assert tasks[0]["status"] == "done"  # parent auto-marked
        assert result["title"] == "next"

    def test_find_next_pending_all_done(self):
        tasks = [_create_task({"title": "t1", "description": ""}, 0)]
        tasks[0]["status"] = "done"
        assert _find_next_pending(tasks) is None

    def test_find_task_by_id(self):
        tasks = [_create_task({
            "title": "parent",
            "description": "",
            "subtasks": [{"title": "child", "description": ""}]
        })]
        child_id = tasks[0]["subtasks"][0]["id"]
        found = _find_task_by_id(tasks, child_id)
        assert found is not None
        assert found["title"] == "child"

    def test_find_task_by_id_not_found(self):
        tasks = [_create_task({"title": "t", "description": ""})]
        assert _find_task_by_id(tasks, "nonexistent") is None

    def test_insert_after(self):
        tasks = [
            _create_task({"title": "t1", "description": ""}, 0),
            _create_task({"title": "t3", "description": ""}, 1),
        ]
        new = _create_task({"title": "t2_inserted", "description": ""})
        ok = _insert_after(tasks, tasks[0]["id"], new)
        assert ok
        assert len(tasks) == 3
        assert tasks[1]["title"] == "t2_inserted"

    def test_insert_after_not_found(self):
        tasks = [_create_task({"title": "t1", "description": ""}, 0)]
        new = _create_task({"title": "new", "description": ""})
        assert _insert_after(tasks, "nonexistent", new) is False
        assert len(tasks) == 1

    def test_count_tasks(self):
        tasks = [
            _create_task({"title": "t1", "description": ""}, 0),
            _create_task({
                "title": "t2", "description": "",
                "subtasks": [
                    {"title": "s1", "description": ""},
                    {"title": "s2", "description": ""},
                ]
            }, 1),
        ]
        tasks[0]["status"] = "done"
        tasks[1]["subtasks"][0]["status"] = "done"
        total, done = _count_tasks(tasks)
        assert total == 3  # t1 + s1 + s2 (t2 has subtasks, so t2 itself not counted)
        assert done == 2   # t1 + s1

    def test_format_plan_text(self):
        plan = make_plan()
        text = _format_plan_text(plan)
        assert "测试目标" in text
        assert "0/3" in text
        assert "任务1" in text
        assert "任务2" in text

    def test_completed_summary(self):
        tasks = [
            _create_task({"title": "t1", "description": ""}, 0),
            _create_task({"title": "t2", "description": ""}, 1),
        ]
        tasks[0]["status"] = "done"
        tasks[0]["result_summary"] = "完成了 t1"
        summary = _completed_summary(tasks)
        assert "t1" in summary
        assert "完成了 t1" in summary


# ════════════════════════════════════════
# 状态机测试
# ════════════════════════════════════════

class TestControlFlow:
    @pytest.mark.asyncio
    async def test_start_from_confirming(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="confirming")
        mgr._plans["sid_001"] = plan
        result = await mgr.control("sid_001", "start")
        assert "开始执行" in result
        assert plan["status"] == "executing"

    @pytest.mark.asyncio
    async def test_start_wrong_status(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan
        result = await mgr.control("sid_001", "start")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_pause_from_executing(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan
        result = await mgr.control("sid_001", "pause")
        assert "暂停" in result
        assert plan["status"] == "paused"

    @pytest.mark.asyncio
    async def test_resume_from_paused(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="paused")
        mgr._plans["sid_001"] = plan
        result = await mgr.control("sid_001", "resume")
        assert "恢复" in result
        assert plan["status"] == "executing"

    @pytest.mark.asyncio
    async def test_cancel(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan
        result = await mgr.control("sid_001", "cancel")
        assert "取消" in result
        assert plan["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_control_no_plan(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        result = await mgr.control("sid_001", "start")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_control_unknown_action(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        mgr._plans["sid_001"] = make_plan()
        result = await mgr.control("sid_001", "invalid")
        assert "未知操作" in result


# ════════════════════════════════════════
# 规划阶段测试
# ════════════════════════════════════════

class TestPlanning:
    @pytest.mark.asyncio
    async def test_create_plan_success(self):
        plan_json = json.dumps({
            "tasks": [
                {"title": "步骤1", "description": "做第一件事"},
                {"title": "步骤2", "description": "做第二件事"},
            ]
        })
        plugin = FakePlugin(llm_response=plan_json)
        mgr = TakeoverManager(plugin)
        result = await mgr.create_plan("sid_001", "test_umo", "构建 REST API")
        assert "已生成任务计划" in result
        assert "步骤1" in result
        plan = mgr.get_plan("sid_001")
        assert plan is not None
        assert plan["status"] == "confirming"
        assert len(plan["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_create_plan_no_llm(self):
        plugin = FakePlugin(llm_response=None)
        mgr = TakeoverManager(plugin)
        result = await mgr.create_plan("sid_001", "test_umo", "目标")
        assert "失败" in result

    @pytest.mark.asyncio
    async def test_create_plan_bad_json(self):
        plugin = FakePlugin(llm_response="this is not json")
        mgr = TakeoverManager(plugin)
        result = await mgr.create_plan("sid_001", "test_umo", "目标")
        assert "无法解析" in result

    @pytest.mark.asyncio
    async def test_create_plan_blocked_by_active(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        mgr._plans["sid_001"] = make_plan(status="executing")
        result = await mgr.create_plan("sid_001", "test_umo", "新目标")
        assert "已有活跃计划" in result

    @pytest.mark.asyncio
    async def test_modify_plan(self):
        modified_json = json.dumps({
            "tasks": [
                {"title": "修改后步骤1", "description": "新描述"},
            ]
        })
        plugin = FakePlugin(llm_response=modified_json)
        mgr = TakeoverManager(plugin)
        mgr._plans["sid_001"] = make_plan(status="confirming")
        result = await mgr.modify_plan("sid_001", "test_umo", "去掉步骤2和3")
        assert "已更新" in result
        assert len(mgr._plans["sid_001"]["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_modify_plan_no_plan(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        result = await mgr.modify_plan("sid_001", "test_umo", "修改")
        assert "❌" in result


# ════════════════════════════════════════
# 执行循环测试
# ════════════════════════════════════════

class TestExecution:
    @pytest.mark.asyncio
    async def test_execute_next_task(self):
        plugin = FakePlugin(llm_response="请执行步骤1")
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan

        _send_message_log.clear()
        await mgr._execute_next_task("sid_001")

        # 验证消息发送
        assert len(_send_message_log) == 1
        assert _send_message_log[0]["text"] == "请执行步骤1"
        # 验证回调注册
        sse = plugin.sse_listener
        assert "sid_001" in sse._pending_takeover_completions
        assert sse._pending_takeover_completions["sid_001"]["task_id"] == plan["tasks"][0]["id"]
        # 验证状态更新
        assert plan["tasks"][0]["status"] == "running"
        assert plan["current_task_id"] == plan["tasks"][0]["id"]

    @pytest.mark.asyncio
    async def test_execute_no_pending_tasks(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        for t in plan["tasks"]:
            t["status"] = "done"
        mgr._plans["sid_001"] = plan

        await mgr._execute_next_task("sid_001")
        assert plan["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_paused_stops_loop(self):
        plugin = FakePlugin(llm_response="指令")
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="paused")
        mgr._plans["sid_001"] = plan

        _send_message_log.clear()
        await mgr._execute_next_task("sid_001")
        assert len(_send_message_log) == 0  # 不发送

    @pytest.mark.asyncio
    async def test_on_task_completed_continue(self):
        eval_json = json.dumps({
            "task_status": "done",
            "task_summary": "步骤1完成",
            "goal_achieved": False,
            "next_action": "continue",
            "reasoning": "还有更多任务"
        })
        plugin = FakePlugin(llm_response=eval_json)
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        plan["tasks"][0]["status"] = "running"
        plan["current_task_id"] = plan["tasks"][0]["id"]
        mgr._plans["sid_001"] = plan

        _send_message_log.clear()
        await mgr.on_task_completed("sid_001", "任务结果文本")

        # 任务1 标记完成
        assert plan["tasks"][0]["status"] == "done"
        assert plan["tasks"][0]["result_summary"] == "步骤1完成"
        # 自动推进到任务2（LLM 返回 eval_json 作为指令构建也会用）
        # 由于 LLM mock 返回的是 JSON 不是指令文本，这里检查至少发了消息
        assert len(_send_message_log) >= 1

    @pytest.mark.asyncio
    async def test_on_task_completed_goal_achieved(self):
        eval_json = json.dumps({
            "task_status": "done",
            "task_summary": "全部完成",
            "goal_achieved": True,
            "next_action": "complete",
            "reasoning": "目标已达成"
        })
        plugin = FakePlugin(llm_response=eval_json)
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        plan["tasks"][0]["status"] = "running"
        plan["current_task_id"] = plan["tasks"][0]["id"]
        mgr._plans["sid_001"] = plan

        await mgr.on_task_completed("sid_001", "结果")
        assert plan["status"] == "completed"

    @pytest.mark.asyncio
    async def test_on_task_completed_insert_task(self):
        eval_json = json.dumps({
            "task_status": "done",
            "task_summary": "发现需要额外步骤",
            "goal_achieved": False,
            "next_action": "insert_task",
            "inserted_task": {"title": "临时任务", "description": "修复问题"},
            "reasoning": "需要先修复才能继续"
        })
        plugin = FakePlugin(llm_response=eval_json)
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        first_id = plan["tasks"][0]["id"]
        plan["tasks"][0]["status"] = "running"
        plan["current_task_id"] = first_id
        mgr._plans["sid_001"] = plan

        await mgr.on_task_completed("sid_001", "结果")
        # 验证临时任务被插入到第一个任务之后
        assert len(plan["tasks"]) == 4
        assert plan["tasks"][1]["title"] == "临时任务"

    @pytest.mark.asyncio
    async def test_on_task_completed_retry(self):
        eval_json = json.dumps({
            "task_status": "failed",
            "task_summary": "执行失败",
            "goal_achieved": False,
            "next_action": "retry",
            "reasoning": "需要重试"
        })
        plugin = FakePlugin(llm_response=eval_json)
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        plan["tasks"][0]["status"] = "running"
        plan["current_task_id"] = plan["tasks"][0]["id"]
        mgr._plans["sid_001"] = plan

        await mgr.on_task_completed("sid_001", "失败结果")
        # retry: 任务先重置为 pending，然后 _execute_next_task 再次执行它（变为 running）
        assert plan["tasks"][0]["status"] == "running"
        # 验证又发送了一条消息（重试指令）
        assert len(_send_message_log) >= 1


# ════════════════════════════════════════
# 恢复和持久化测试
# ════════════════════════════════════════

class TestRecovery:
    def test_recover_from_restart(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        plan["tasks"][0]["status"] = "running"
        plan["current_task_id"] = plan["tasks"][0]["id"]
        plugin.state_mgr._takeover_plans["sid_001"] = plan

        mgr.recover_from_restart()
        assert mgr._plans["sid_001"]["status"] == "paused"
        assert mgr._plans["sid_001"]["tasks"][0]["status"] == "pending"

    def test_recover_completed_unchanged(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="completed")
        plugin.state_mgr._takeover_plans["sid_001"] = plan

        mgr.recover_from_restart()
        assert mgr._plans["sid_001"]["status"] == "completed"

    def test_is_active(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        assert mgr.is_active("sid_001") is False

        mgr._plans["sid_001"] = make_plan(status="executing")
        assert mgr.is_active("sid_001") is True

        mgr._plans["sid_001"]["status"] = "paused"
        assert mgr.is_active("sid_001") is False


# ════════════════════════════════════════
# JSON 解析测试
# ════════════════════════════════════════

class TestJsonParsing:
    def setup_method(self):
        self.plugin = FakePlugin()
        self.mgr = TakeoverManager(self.plugin)

    def test_parse_json_plain(self):
        result = self.mgr._parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_markdown_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = self.mgr._parse_json(text)
        assert result == {"key": "value"}

    def test_parse_json_embedded_braces(self):
        text = 'Here is the result: {"tasks": []} and more text'
        result = self.mgr._parse_json(text)
        assert result == {"tasks": []}

    def test_parse_json_invalid(self):
        assert self.mgr._parse_json("not json at all") is None

    def test_parse_plan_json_valid(self):
        text = json.dumps({"tasks": [{"title": "t1", "description": "d1"}]})
        result = self.mgr._parse_plan_json(text)
        assert result is not None
        assert len(result) == 1

    def test_parse_plan_json_no_tasks(self):
        assert self.mgr._parse_plan_json('{"other": "data"}') is None

    def test_parse_plan_json_empty_tasks(self):
        assert self.mgr._parse_plan_json('{"tasks": []}') is None
