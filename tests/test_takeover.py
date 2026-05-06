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


_abort_session_log = []
_abort_session_result = (True, "已中断 [test]")


async def _fake_abort_session(client, sid):
    _abort_session_log.append({"sid": sid})
    return _abort_session_result


_session_detail_state = {"thinking": False, "active": False}
_session_detail_error: Exception | None = None


async def _fake_fetch_session_detail(client, sid):
    if _session_detail_error is not None:
        raise _session_detail_error
    return {"id": sid, **_session_detail_state}


_session_ops.send_message = _fake_send_message
_session_ops.fetch_messages = _fake_fetch_messages
_session_ops.abort_session = _fake_abort_session
_session_ops.fetch_session_detail = _fake_fetch_session_detail
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


class FakeLLMIntegration:
    def __init__(self, completion_response=""):
        self.completion_response = completion_response
        self.fetch_call_log = []

    async def _fetch_completion_response(self, sid, pre_send_seq):
        self.fetch_call_log.append({"sid": sid, "pre_send_seq": pre_send_seq})
        return self.completion_response


class FakePlugin:
    def __init__(self, llm_response=None, completion_response=""):
        self.config = {"takeover_max_tasks": 10}
        self.client = None
        self.state_mgr = FakeStateMgr()
        self.context = FakeContext(llm_response)
        self.sessions_cache = [
            {"id": "sid_001", "machineId": "m1",
             "metadata": {"path": "/home/user/project", "flavor": "claude"}}
        ]
        self.sse_listener = FakeSSEListener()
        self.llm_integration = FakeLLMIntegration(completion_response)


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
        # 全叶子任务计划应该全部用 ⬜ 复选框，没有 📂/📁 分组图标
        assert "📂" not in text
        assert "📁" not in text
        assert "⬜" in text

    def test_format_plan_text_with_subtasks(self):
        """父任务渲染为 📂/📁 分组标题，叶子用复选框，进度只数叶子。"""
        sub1 = _create_task({"title": "子1.1", "description": "d"}, 0)
        sub2 = _create_task({"title": "子1.2", "description": "d"}, 1)
        sub1["status"] = "done"  # 一个完成
        parent = _create_task({"title": "父任务1", "description": "d"}, 0)
        parent["subtasks"] = [sub1, sub2]
        leaf = _create_task({"title": "独立叶子", "description": "d"}, 1)
        plan = make_plan(tasks=[parent, leaf])

        text = _format_plan_text(plan)
        # 父任务渲染为 📂（部分完成）+ 子任务计数
        assert "📂 父任务1 (1/2)" in text
        # 叶子还是用 ⬜
        assert "⬜ 独立叶子" in text
        # 进度数只数叶子：子1.1(done) + 子1.2(pending) + 独立叶子(pending) = 1/3
        assert "1/3" in text

    def test_format_plan_text_fully_completed_parent(self):
        """父任务下所有子完成 → 父显示 📁（关闭文件夹）。"""
        sub1 = _create_task({"title": "子1.1"}, 0)
        sub2 = _create_task({"title": "子1.2"}, 1)
        sub1["status"] = "done"
        sub2["status"] = "skipped"  # done + skipped 都算完成
        parent = _create_task({"title": "父任务"}, 0)
        parent["subtasks"] = [sub1, sub2]
        plan = make_plan(tasks=[parent])

        text = _format_plan_text(plan)
        assert "📁 父任务 (2/2)" in text
        assert "📂" not in text  # 全完成不应显示打开文件夹

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
        _abort_session_log.clear()
        plugin.sse_listener._pending_takeover_completions["sid_001"] = {
            "pre_send_seq": 1, "ts": 0, "task_id": "x"}
        result = await mgr.control("sid_001", "cancel")
        assert "取消" in result
        assert plan["status"] == "cancelled"
        # cancel 现在会调 abort_session 并 pop pending ctx
        assert _abort_session_log == [{"sid": "sid_001"}]
        assert "sid_001" not in plugin.sse_listener._pending_takeover_completions

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
    async def test_execute_next_task_rejects_reentry(self):
        """同一 sid 的并发 _execute_next_task 调用，第二个应直接放弃（pause→resume race）。"""
        plugin = FakePlugin(llm_response="请执行")
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan
        mgr._executing_sids.add("sid_001")  # 模拟"前一个调用还在跑"

        _send_message_log.clear()
        await mgr._execute_next_task("sid_001")  # 应被拒

        assert len(_send_message_log) == 0  # 没发消息
        assert plan["tasks"][0]["status"] == "pending"  # 没动状态

    @pytest.mark.asyncio
    async def test_execute_next_task_clears_in_flight_on_exit(self):
        """正常完成后应从 _executing_sids 移除，允许后续调用。"""
        plugin = FakePlugin(llm_response="请执行")
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan

        await mgr._execute_next_task("sid_001")

        assert "sid_001" not in mgr._executing_sids

    @pytest.mark.asyncio
    async def test_execute_next_task_clears_in_flight_on_exception(self):
        """实现层抛异常时也要清掉 in-flight 标记，否则永久卡死。"""
        plugin = FakePlugin(llm_response="请执行")
        mgr = TakeoverManager(plugin)

        async def _boom(sid):
            raise RuntimeError("simulated failure")
        mgr._execute_next_task_impl = _boom

        try:
            await mgr._execute_next_task("sid_001")
        except RuntimeError:
            pass

        assert "sid_001" not in mgr._executing_sids

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


# ════════════════════════════════════════════════════════════════
# 新增测试：超时清理与 sweep 处理
# ════════════════════════════════════════════════════════════════


def _make_running_plan(task_id="task_running"):
    """构造一个正在执行某任务的 plan（task running、plan executing）。"""
    tasks = [
        _create_task({"title": "前置完成的任务", "description": "d0"}, 0),
        _create_task({"title": "卡住中的任务", "description": "d1"}, 1),
        _create_task({"title": "后续待办", "description": "d2"}, 2),
    ]
    tasks[0]["status"] = "done"
    tasks[1]["status"] = "running"
    tasks[1]["pre_send_seq"] = 100
    tasks[1]["sent_at"] = 1000.0
    tasks[1]["id"] = task_id
    plan = make_plan(status="executing", tasks=tasks)
    plan["current_task_id"] = task_id
    return plan


@pytest.mark.asyncio
class TestStaleHandling:
    """on_task_completed 校验 + on_sweep_timeout + on_user_response_timeout"""

    async def test_on_task_completed_rejects_stale_ctx_task_id(self):
        plugin = FakePlugin(llm_response='{"task_status":"done"}')
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan("real_task")
        mgr._plans["sid_001"] = plan

        # 旧 ctx 的 task_id 与当前 current_task_id 不一致
        await mgr.on_task_completed("sid_001", "some response", ctx_task_id="stale_task")

        # 应被丢弃：task 状态不变、没有调 LLM 评估
        task = _find_task_by_id(plan["tasks"], "real_task")
        assert task["status"] == "running"
        assert task["result_summary"] is None

    async def test_on_sweep_timeout_rolls_back_running_task(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        mgr._plans["sid_001"] = plan

        await mgr.on_sweep_timeout("sid_001", "task_running")

        task = _find_task_by_id(plan["tasks"], "task_running")
        assert task["status"] == "pending"  # 回滚
        assert plan["status"] == "paused"
        assert plan["awaiting_response_since"] is not None  # 启动 5min 计时
        # 用户收到通知
        assert any("超过 30 分钟" in m["text"]
                   for m in plugin.sse_listener.sent_messages)

    async def test_on_sweep_timeout_no_action_if_not_executing(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "cancelled"  # 已取消
        mgr._plans["sid_001"] = plan

        await mgr.on_sweep_timeout("sid_001", "task_running")

        assert plan["status"] == "cancelled"
        assert plan.get("awaiting_response_since") is None
        assert len(plugin.sse_listener.sent_messages) == 0

    async def test_on_user_response_timeout_notifies_ai(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        plan["awaiting_response_since"] = 100.0  # 在过去
        mgr._plans["sid_001"] = plan

        await mgr.on_user_response_timeout("sid_001")

        assert plan["awaiting_response_since"] is None  # 清掉防重复
        # 用户和 LLM 都收到消息（FakeSSEListener 的 _send_user_message 用同一个 list）
        msgs = plugin.sse_listener.sent_messages
        assert any("AstrBot AI 已接管" in m["text"] for m in msgs)
        assert any("hapi_coding_takeover_check" in m["text"] for m in msgs)

    async def test_on_user_response_timeout_skips_if_not_paused(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "executing"  # 用户已自行 resume
        plan["awaiting_response_since"] = 100.0
        mgr._plans["sid_001"] = plan

        await mgr.on_user_response_timeout("sid_001")

        # 字段不被清，不发通知
        assert plan["awaiting_response_since"] == 100.0
        assert len(plugin.sse_listener.sent_messages) == 0

    async def test_on_user_response_timeout_skips_if_already_cleared(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        plan["awaiting_response_since"] = None  # 已被清掉
        mgr._plans["sid_001"] = plan

        await mgr.on_user_response_timeout("sid_001")

        assert len(plugin.sse_listener.sent_messages) == 0


@pytest.mark.asyncio
class TestCheckCore:
    """check 核心函数 + 用户/AI formatter"""

    async def test_check_returns_recommendation_wait_when_thinking(self):
        global _session_detail_state, _session_detail_error
        _session_detail_state = {"thinking": True, "active": False}
        _session_detail_error = None
        plugin = FakePlugin(completion_response="some output that's long enough " * 5)
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        mgr._plans["sid_001"] = plan

        result = await mgr.check("sid_001")

        assert result["ok"] is True
        assert result["thinking"] is True
        assert result["recommendation"] == "wait"

    async def test_check_returns_recommendation_accept_when_idle_with_output(self):
        global _session_detail_state, _session_detail_error
        _session_detail_state = {"thinking": False, "active": False}
        _session_detail_error = None
        plugin = FakePlugin(completion_response="x" * 200)  # 长响应
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        mgr._plans["sid_001"] = plan

        result = await mgr.check("sid_001")

        assert result["ok"] is True
        assert result["has_output"] is True
        assert result["recommendation"] == "accept"

    async def test_check_returns_recommendation_manual_when_idle_no_output(self):
        global _session_detail_state, _session_detail_error
        _session_detail_state = {"thinking": False, "active": False}
        _session_detail_error = None
        plugin = FakePlugin(completion_response="")  # 空响应
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        mgr._plans["sid_001"] = plan

        result = await mgr.check("sid_001")

        assert result["ok"] is True
        assert result["has_output"] is False
        assert result["recommendation"] == "manual"

    async def test_check_returns_unreachable_on_api_error(self):
        global _session_detail_state, _session_detail_error
        _session_detail_state = {"thinking": False, "active": False}
        _session_detail_error = ConnectionError("HAPI down")
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        mgr._plans["sid_001"] = plan

        result = await mgr.check("sid_001")
        _session_detail_error = None  # 复位

        assert result["ok"] is False
        assert result["reason"] == "hapi_unreachable"
        assert "HAPI down" in result["error"]

    async def test_check_returns_no_plan_when_no_plan(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)

        result = await mgr.check("sid_unknown")

        assert result["ok"] is False
        assert result["reason"] == "no_plan"

    async def test_check_for_user_clears_awaiting_response_since(self):
        global _session_detail_state, _session_detail_error
        _session_detail_state = {"thinking": False, "active": False}
        _session_detail_error = None
        plugin = FakePlugin(completion_response="x" * 200)
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        plan["awaiting_response_since"] = 100.0
        mgr._plans["sid_001"] = plan

        text = await mgr.check_for_user("sid_001")

        assert plan["awaiting_response_since"] is None
        assert "Takeover 诊断" in text or "诊断" in text

    async def test_check_for_llm_returns_structured_text(self):
        global _session_detail_state, _session_detail_error
        _session_detail_state = {"thinking": False, "active": False}
        _session_detail_error = None
        plugin = FakePlugin(completion_response="x" * 200)
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        plan["awaiting_response_since"] = 100.0
        mgr._plans["sid_001"] = plan

        text = await mgr.check_for_llm("sid_001")

        assert plan["awaiting_response_since"] is None
        assert "recommendation=accept" in text
        assert "hapi_thinking=False" in text


@pytest.mark.asyncio
class TestSkipAccept:
    """_skip / _accept 控制动作"""

    async def test_skip_marks_task_skipped_and_advances(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        plan["awaiting_response_since"] = 100.0
        mgr._plans["sid_001"] = plan

        result = await mgr.control("sid_001", "skip")

        task = _find_task_by_id(plan["tasks"], "task_running")
        assert task["status"] == "skipped"
        assert plan["status"] == "executing"
        assert plan["awaiting_response_since"] is None
        assert "已跳过" in result

    async def test_skip_already_done_task_advances(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        # 任务已经完成（pause 时刚好完成但未推进）
        task = _find_task_by_id(plan["tasks"], "task_running")
        task["status"] = "done"
        plan["status"] = "paused"
        mgr._plans["sid_001"] = plan

        result = await mgr.control("sid_001", "skip")

        # 不覆盖 done 状态
        assert task["status"] == "done"
        assert plan["status"] == "executing"
        assert "已完成" in result

    async def test_skip_invalid_status_rejected(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="confirming")  # 还没开始
        mgr._plans["sid_001"] = plan

        result = await mgr.control("sid_001", "skip")

        assert "❌" in result
        assert plan["status"] == "confirming"

    async def test_accept_with_meaningful_response_calls_on_task_completed(self):
        plugin = FakePlugin(
            llm_response='{"task_status":"done","task_summary":"OK","goal_achieved":true}',
            completion_response="x" * 200)
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        mgr._plans["sid_001"] = plan

        result = await mgr.control("sid_001", "accept")

        # accept 异步触发 on_task_completed，等一轮
        await asyncio.sleep(0.05)

        assert "已采纳" in result
        # llm_integration._fetch_completion_response 被调
        assert len(plugin.llm_integration.fetch_call_log) == 1

    async def test_accept_with_empty_response_returns_error(self):
        plugin = FakePlugin(completion_response="")  # 短响应
        mgr = TakeoverManager(plugin)
        plan = _make_running_plan()
        plan["status"] = "paused"
        mgr._plans["sid_001"] = plan

        result = await mgr.control("sid_001", "accept")

        assert "❌" in result
        # plan 状态没变
        assert plan["status"] == "paused"


@pytest.mark.asyncio
class TestCancelAbort:
    """cancel 改造：调 abort_session + pop ctx + 清 awaiting"""

    async def test_cancel_calls_abort_session(self):
        global _abort_session_result
        _abort_session_result = (True, "已中断 [test]")
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan
        _abort_session_log.clear()

        await mgr.control("sid_001", "cancel")

        assert _abort_session_log == [{"sid": "sid_001"}]

    async def test_cancel_pops_pending_completion(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan
        plugin.sse_listener._pending_takeover_completions["sid_001"] = {
            "pre_send_seq": 1, "ts": 0, "task_id": "x"}

        await mgr.control("sid_001", "cancel")

        assert "sid_001" not in plugin.sse_listener._pending_takeover_completions

    async def test_cancel_succeeds_even_if_abort_fails(self):
        global _abort_session_result
        _abort_session_result = (False, "HAPI 不可达")
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="executing")
        mgr._plans["sid_001"] = plan

        result = await mgr.control("sid_001", "cancel")

        # 复位
        _abort_session_result = (True, "已中断 [test]")

        # 本地仍标 cancelled
        assert plan["status"] == "cancelled"
        assert "HAPI 中止失败" in result
        assert "/hapi stop" in result  # 提示用户手动 stop

    async def test_cancel_clears_awaiting_response_since(self):
        plugin = FakePlugin()
        mgr = TakeoverManager(plugin)
        plan = make_plan(status="paused")
        plan["awaiting_response_since"] = 100.0
        mgr._plans["sid_001"] = plan

        await mgr.control("sid_001", "cancel")

        assert plan["awaiting_response_since"] is None
