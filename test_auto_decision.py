"""AutoDecisionManager 单元测试

运行方式：
  cd /root/workspace/host/AstrBot
  uv run python -m data.plugins.astrbot_plugin_hapi_connector.test_auto_decision
"""

import json
import sys
import types

# ──── 在导入 auto_decision 之前，先 stub 掉它的相对导入依赖 ────

# 创建包层级
_pkg_name = "data.plugins.astrbot_plugin_hapi_connector"
for _partial in [
    "data",
    "data.plugins",
    _pkg_name,
]:
    if _partial not in sys.modules:
        sys.modules[_partial] = types.ModuleType(_partial)

# stub session_ops
_session_ops = types.ModuleType(f"{_pkg_name}.session_ops")
_session_ops.fetch_messages = None
_session_ops.answer_permission_question = None
async def _fake_approve(client, sid, rid, answers=None):
    return True, "OK"
async def _fake_deny(client, sid, rid):
    return True, "OK"
_session_ops.approve_permission = _fake_approve
_session_ops.deny_permission = _fake_deny
sys.modules[f"{_pkg_name}.session_ops"] = _session_ops

# stub approval_ops
_approval_ops = types.ModuleType(f"{_pkg_name}.approval_ops")
async def _fake_answer_question(client, sid, rid, answers):
    return True, "OK"
_approval_ops.answer_question = _fake_answer_question
sys.modules[f"{_pkg_name}.approval_ops"] = _approval_ops

# stub formatters
_formatters = types.ModuleType(f"{_pkg_name}.formatters")
def _fake_extract(content, max_len=0):
    return content.get("text", None)
def _fake_label(sid, cache):
    meta = {}
    for s in cache:
        if s.get("id") == sid:
            meta = s.get("metadata", {})
            break
    path = meta.get("path", "")
    dir_name = path.rstrip("/").split("/")[-1] if path else sid[:8]
    flavor = meta.get("flavor", "?")
    return f"[{flavor}] {dir_name}"
def _fake_is_question(req):
    return req.get("tool", "") in ("AskUserQuestion",)
_formatters.extract_text_preview = _fake_extract
_formatters.session_label_short = _fake_label
_formatters.is_question_request = _fake_is_question
sys.modules[f"{_pkg_name}.formatters"] = _formatters

# 现在可以安全导入 auto_decision（它的 from . import 会命中上面的 stub）
import importlib
import os
_ad_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_decision.py")
_spec = importlib.util.spec_from_file_location(f"{_pkg_name}.auto_decision", _ad_path)
auto_decision = importlib.util.module_from_spec(_spec)
sys.modules[f"{_pkg_name}.auto_decision"] = auto_decision
_spec.loader.exec_module(auto_decision)
AutoDecisionManager = auto_decision.AutoDecisionManager
DecisionResult = auto_decision.DecisionResult


# ──── 模拟对象 ────

class FakePlugin:
    """模拟插件实例"""
    def __init__(self):
        self.config = {
            "auto_decision_mode": "auto",
            "auto_decision_max_history": 30,
            "auto_decision_confidence_threshold": 7,
        }
        self.client = None
        self.state_mgr = FakeStateMgr()
        self.context = FakeContext()
        self.sessions_cache = [
            {"id": "abc12345-full-id", "metadata": {"path": "/home/user/my-project", "flavor": "claude"}}
        ]
        self.sse_listener = FakeSSEListener()


class FakeStateMgr:
    def select_notification_targets(self, sid, cache):
        return ["test_umo"]


class FakeContext:
    async def get_current_chat_provider_id(self, umo=None):
        return None  # 默认无 provider


class FakeSSEListener:
    async def _push_notification(self, text, sid):
        print(f"  [通知推送] sid={sid[:8] if sid else 'global'}")
        for line in text.split("\n"):
            print(f"    {line}")


# ──── 测试用请求构造 ────

def make_single_question_req(question: str, options: list[dict], header: str = "") -> dict:
    q = {"question": question, "options": options}
    if header:
        q["header"] = header
    return {
        "tool": "AskUserQuestion",
        "arguments": {"questions": [q]},
        "index": 1,
    }


def make_multi_question_req() -> dict:
    return {
        "tool": "AskUserQuestion",
        "arguments": {
            "questions": [
                {
                    "question": "Which language?",
                    "options": [
                        {"label": "TypeScript", "description": "Typed JavaScript"},
                        {"label": "JavaScript", "description": "Dynamic scripting"},
                    ],
                },
                {
                    "question": "Which package manager?",
                    "options": [
                        {"label": "npm"},
                        {"label": "pnpm"},
                        {"label": "yarn"},
                    ],
                },
            ]
        },
        "index": 2,
    }


def make_approval_req(tool: str = "Read", arguments: dict | None = None) -> dict:
    return {
        "tool": tool,
        "arguments": arguments or {"path": "/src/main.py"},
        "index": 1,
    }


# ──── 测试用例 ────

def test_parse_valid_answer():
    """测试 1：解析正常的 JSON 回答"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_single_question_req(
        "Which framework?",
        [{"label": "React"}, {"label": "Vue"}, {"label": "Angular"}],
    )
    response = json.dumps({
        "action": "answer", "confidence": 9,
        "reasoning": "用户提到了 React",
        "answers": {"0": ["React"]},
    })
    answers, reasoning, confidence = mgr._parse_question_response(response, req)
    assert answers == {"0": ["React"]}, f"Expected React, got {answers}"
    assert confidence == 9
    print("  PASS: 正确解析单选回答")


def test_parse_escalate():
    """测试 2：解析 ESCALATE 响应"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_single_question_req("Delete production DB?", [{"label": "Yes"}, {"label": "No"}])
    response = json.dumps({
        "action": "escalate", "confidence": 3,
        "reasoning": "涉及生产环境，需要人工确认",
    })
    answers, reasoning, confidence = mgr._parse_question_response(response, req)
    assert answers is None, "Should be None for escalate"
    assert confidence == 3
    print("  PASS: ESCALATE 正确返回 None")


def test_parse_multi_question():
    """测试 3：解析多问题回答"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_multi_question_req()
    response = json.dumps({
        "action": "answer", "confidence": 8,
        "reasoning": "TypeScript + pnpm 是现代项目首选",
        "answers": {"0": ["TypeScript"], "1": ["pnpm"]},
    })
    answers, reasoning, confidence = mgr._parse_question_response(response, req)
    assert answers == {"0": ["TypeScript"], "1": ["pnpm"]}, f"Got {answers}"
    assert confidence == 8
    print("  PASS: 多问题回答正确解析")


def test_parse_missing_answer():
    """测试 4：缺少某个问题的回答 → 返回 None"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_multi_question_req()
    response = json.dumps({
        "action": "answer", "confidence": 8,
        "reasoning": "只回答了一个",
        "answers": {"0": ["TypeScript"]},
    })
    answers, reasoning, confidence = mgr._parse_question_response(response, req)
    assert answers is None, "Should be None when answer is missing"
    print("  PASS: 缺少回答时正确返回 None")


def test_parse_markdown_fence():
    """测试 5：剥离 markdown code fence"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_single_question_req("Pick one", [{"label": "A"}, {"label": "B"}])
    response = '```json\n{"action": "answer", "confidence": 8, "reasoning": "test", "answers": {"0": ["A"]}}\n```'
    answers, reasoning, confidence = mgr._parse_question_response(response, req)
    assert answers == {"0": ["A"]}, f"Got {answers}"
    print("  PASS: markdown code fence 正确剥离")


def test_parse_invalid_json():
    """测试 6：无效 JSON → 返回 None"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_single_question_req("Pick one", [{"label": "A"}])
    answers, reasoning, confidence = mgr._parse_question_response("not json at all", req)
    assert answers is None
    assert confidence == 0
    print("  PASS: 无效 JSON 正确返回 None")


def test_parse_string_answer():
    """测试 7：answer 值为字符串（非列表）→ 自动包装"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_single_question_req("Pick one", [{"label": "A"}])
    response = json.dumps({
        "action": "answer", "confidence": 8,
        "reasoning": "test", "answers": {"0": "A"},
    })
    answers, reasoning, confidence = mgr._parse_question_response(response, req)
    assert answers == {"0": ["A"]}, f"Got {answers}"
    print("  PASS: 字符串 answer 自动包装为列表")


def test_parse_approval_response():
    """测试 8：解析审批 LLM 响应"""
    mgr = AutoDecisionManager(FakePlugin())
    response = json.dumps({
        "action": "approve", "confidence": 9,
        "reasoning": "读取操作，与任务相关",
    })
    action, reasoning, confidence = mgr._parse_approval_response(response)
    assert action == "approve"
    assert confidence == 9
    assert "读取" in reasoning

    # deny
    response = json.dumps({"action": "deny", "confidence": 8, "reasoning": "不相关"})
    action, _, _ = mgr._parse_approval_response(response)
    assert action == "deny"

    # escalate
    response = json.dumps({"action": "escalate", "confidence": 3, "reasoning": "不确定"})
    action, _, confidence = mgr._parse_approval_response(response)
    assert action == "escalate"
    assert confidence == 3

    # invalid action → escalate
    response = json.dumps({"action": "maybe", "confidence": 5, "reasoning": "hmm"})
    action, _, _ = mgr._parse_approval_response(response)
    assert action == "escalate"

    print("  PASS: 审批响应解析正确（approve/deny/escalate/invalid）")


def test_high_risk_detection():
    """测试 9：高风险操作检测"""
    _is_high_risk = auto_decision._is_high_risk

    # 高风险
    assert _is_high_risk({"tool": "Bash", "arguments": {"command": "rm -rf /"}})
    assert _is_high_risk({"tool": "Bash", "arguments": {"command": "git push --force"}})
    assert _is_high_risk({"tool": "Bash", "arguments": {"command": "sudo apt install"}})
    assert _is_high_risk({"tool": "DeleteFile", "arguments": {"path": "/src/main.py"}})

    # 安全操作
    assert not _is_high_risk({"tool": "Read", "arguments": {"path": "/src/main.py"}})
    assert not _is_high_risk({"tool": "Edit", "arguments": {"file": "/src/main.py", "content": "hello"}})
    assert not _is_high_risk({"tool": "Bash", "arguments": {"command": "ls -la"}})
    assert not _is_high_risk({"tool": "Grep", "arguments": {"pattern": "TODO"}})

    print("  PASS: 高风险操作检测正确")


def test_build_question_prompt():
    """测试 10：构建 question prompt"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_single_question_req(
        "Which CSS framework?",
        [{"label": "Tailwind CSS", "description": "Utility-first"}, {"label": "Bootstrap"}],
        header="styling",
    )
    prompt = mgr._build_question_prompt(
        req,
        "[User]: 帮我创建一个 React 项目\n[Assistant]: 好的，正在初始化...",
        [{"description": "Language?", "action": "answer", "reasoning": "项目已有 tsconfig"}],
    )
    assert "=== 对话历史 ===" in prompt
    assert "帮我创建一个 React 项目" in prompt
    assert "=== 之前的决策记录 ===" in prompt
    assert "=== 当前需要回答的问题 ===" in prompt
    assert "Tailwind CSS" in prompt
    assert "styling" in prompt
    print("  PASS: question prompt 包含所有必要部分")


def test_build_approval_prompt():
    """测试 11：构建 approval prompt"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_approval_req("Bash", {"command": "npm install"})
    prompt = mgr._build_approval_prompt(
        req,
        "[User]: 帮我安装依赖\n[Assistant]: 好的",
        [],
    )
    assert "=== 对话历史 ===" in prompt
    assert "=== 当前需要审批的工具请求 ===" in prompt
    assert "Bash" in prompt
    assert "npm install" in prompt
    print("  PASS: approval prompt 包含所有必要部分")


def test_decision_history():
    """测试 12：决策历史记录与上限"""
    mgr = AutoDecisionManager(FakePlugin())
    sid = "test-session-id"
    assert mgr._get_decision_history(sid) == []

    for i in range(25):
        mgr._record_decision(sid, f"desc{i}", f"action{i}", f"reason{i}")

    history = mgr._get_decision_history(sid)
    assert len(history) == 20, f"Should cap at 20, got {len(history)}"
    assert history[0]["description"] == "desc5"
    assert history[-1]["description"] == "desc24"
    print("  PASS: 决策历史上限 20 条，FIFO 淘汰")


def test_system_prompts():
    """测试 13：system prompt 包含关键指令"""
    mgr = AutoDecisionManager(FakePlugin())

    sp_q = mgr._build_system_prompt_question()
    assert "ESCALATE" in sp_q
    assert "JSON" in sp_q
    assert "confidence" in sp_q

    sp_a = mgr._build_system_prompt_approval()
    assert "ESCALATE" in sp_a
    assert "approve" in sp_a
    assert "deny" in sp_a
    assert "高风险" in sp_a or "删除" in sp_a

    print("  PASS: system prompt 包含关键指令")


def test_suggest_format():
    """测试 14：suggest 模式通知格式"""
    plugin = FakePlugin()
    plugin.config["auto_decision_mode"] = "suggest"
    mgr = AutoDecisionManager(plugin, mode="suggest")
    sid = "abc12345-full-id"
    req = make_approval_req("Bash", {"command": "npm test"})

    text = mgr._format_suggestion(sid, req, "approve", "安全的测试命令", 9, is_question=False)
    assert "LLM 分析" in text
    assert "建议批准" in text
    assert "9/10" in text
    assert "安全的测试命令" in text
    print("  PASS: suggest 格式正确")
    print(f"  --- 预览 ---")
    for line in text.split("\n"):
        print(f"    {line}")
    print(f"  --- 结束 ---")


def test_suggest_question_format():
    """测试 15：suggest 模式问题建议格式"""
    mgr = AutoDecisionManager(FakePlugin(), mode="suggest")
    sid = "abc12345-full-id"
    req = make_single_question_req("Which framework?", [{"label": "React"}, {"label": "Vue"}])
    answers = {"0": ["React"]}

    text = mgr._format_suggestion(sid, req, "answer", "上下文提到 React", 8,
                                  is_question=True, answers=answers)
    assert "LLM 分析" in text
    assert "建议回答" in text
    assert "React" in text
    print("  PASS: suggest 问题建议格式正确")


def test_high_risk_warning_format():
    """测试 16：高风险警告格式"""
    mgr = AutoDecisionManager(FakePlugin(), mode="suggest")
    sid = "abc12345-full-id"
    req = make_approval_req("Bash", {"command": "rm -rf /"})

    text = mgr._format_high_risk_warning(sid, req)
    assert "高风险" in text
    assert "人工审核" in text
    print("  PASS: 高风险警告格式正确")


def test_decision_result():
    """测试 17：DecisionResult 数据类"""
    r1 = DecisionResult(handled=True)
    assert r1.handled is True
    assert r1.suggestion_text is None

    r2 = DecisionResult(handled=False, suggestion_text="建议批准")
    assert r2.handled is False
    assert r2.suggestion_text == "建议批准"
    print("  PASS: DecisionResult 数据类正确")


def test_confidence_threshold():
    """测试 18：置信度阈值控制"""
    mgr = AutoDecisionManager(FakePlugin())
    req = make_single_question_req("Pick", [{"label": "A"}])

    response = json.dumps({"action": "answer", "confidence": 6, "reasoning": "not sure", "answers": {"0": ["A"]}})
    answers, _, confidence = mgr._parse_question_response(response, req)
    assert answers is not None
    assert confidence == 6

    response = json.dumps({"action": "answer", "confidence": 8, "reasoning": "sure", "answers": {"0": ["A"]}})
    answers, _, confidence = mgr._parse_question_response(response, req)
    assert answers is not None
    assert confidence == 8
    print("  PASS: 置信度阈值逻辑正确")


# ──── 运行 ────

def main():
    tests = [
        test_parse_valid_answer,
        test_parse_escalate,
        test_parse_multi_question,
        test_parse_missing_answer,
        test_parse_markdown_fence,
        test_parse_invalid_json,
        test_parse_string_answer,
        test_parse_approval_response,
        test_high_risk_detection,
        test_build_question_prompt,
        test_build_approval_prompt,
        test_decision_history,
        test_system_prompts,
        test_suggest_format,
        test_suggest_question_format,
        test_high_risk_warning_format,
        test_decision_result,
        test_confidence_threshold,
    ]

    passed = 0
    failed = 0
    for test in tests:
        name = test.__doc__ or test.__name__
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"结果: {passed} passed, {failed} failed / {len(tests)} total")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
