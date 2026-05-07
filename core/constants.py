"""HAPI 常量定义"""

# 各 flavor 对应的权限模式
PERMISSION_MODES = {
    "claude": ["default", "acceptEdits", "bypassPermissions", "plan"],
    "codex": ["default", "read-only", "safe-yolo", "yolo"],
    "gemini": ["default", "read-only", "safe-yolo", "yolo"],
    "opencode": ["default", "yolo"],
}

# Claude 可用的模型预设（null/Auto 在 API 中用 None 表示）
CLAUDE_MODEL_PRESETS = ["sonnet", "sonnet[1m]", "opus", "opus[1m]"]

# Gemini 可用的模型预设
GEMINI_MODEL_PRESETS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# 支持模型切换的 flavor
MODEL_SWITCH_FLAVORS = {"claude", "gemini"}

# 向后兼容：旧的 MODEL_MODES（含 default 表示 auto）
MODEL_MODES = ["default"] + CLAUDE_MODEL_PRESETS

# Codex 可用的思考深度；None 表示继承 Codex 默认设置
CODEX_REASONING_EFFORT_OPTIONS = [
    (None, "继承 Codex 默认设置（推荐）"),
    ("none", "none"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
]
CODEX_REASONING_EFFORT_VALUES = [value for value, _ in CODEX_REASONING_EFFORT_OPTIONS if value]

# Claude 思考深度；与 HAPI Web UI 选项对齐。"default"/"auto" 在 API 层映射为 null
CLAUDE_EFFORT_VALUES = ["low", "medium", "high", "xhigh", "max"]

# 支持的 Agent 类型
AGENTS = ["claude", "codex", "gemini", "opencode"]

# Session 类型
SESSION_TYPES = ["simple", "worktree"]
