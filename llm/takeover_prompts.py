"""Takeover 全盘接管模式的 LLM Prompt 模板"""

# ════════════════════════════════════════
# 任务规划
# ════════════════════════════════════════

PLAN_GENERATION_SYSTEM = """\
你是软件项目规划专家。给定用户的最终目标和当前编程会话上下文，将目标分解为一个层级化的任务计划。

规则：
1. 每个任务应是一个独立的、可发送给 AI 编程助手执行的工作单元
2. 按逻辑依赖排序（前置任务在前）
3. 复杂任务可使用子任务分解，但不要超过两层
4. 总任务数（含子任务）不超过 {max_tasks} 个
5. 任务标题简明扼要，描述应包含具体的操作目标和验收标准
6. 不要拆分得过细——每个任务应该有实质性的产出

严格返回以下 JSON 格式，不要包含任何解释：
{{
  "tasks": [
    {{
      "title": "任务标题",
      "description": "具体描述：做什么、怎么做、验收标准",
      "subtasks": [
        {{"title": "子任务标题", "description": "子任务描述"}}
      ]
    }}
  ]
}}"""

PLAN_GENERATION_USER = """\
=== 最终目标 ===
{goal}

=== 会话上下文 ===
{session_context}

=== 项目经验 ===
{playbook}"""

# ════════════════════════════════════════
# 计划修改
# ════════════════════════════════════════

PLAN_MODIFICATION_SYSTEM = """\
你是软件项目规划专家。用户对现有任务计划提出了修改意见，请根据意见调整计划。

规则与格式同计划生成：总任务数不超过 {max_tasks}，严格返回 JSON。
保留原有合理的任务结构，只修改用户指出的部分。"""

PLAN_MODIFICATION_USER = """\
=== 最终目标 ===
{goal}

=== 当前计划 ===
{current_plan}

=== 用户修改意见 ===
{feedback}

请输出修改后的完整计划 JSON。"""

# ════════════════════════════════════════
# 指令构建（每次执行任务前）
# ════════════════════════════════════════

INSTRUCTION_SYSTEM = """\
你是编程指令撰写专家。给定任务计划中的一个任务，为 AI 编程助手编写一条具体的执行指令。

指令要求：
1. 自包含——编程助手可能因上下文压缩而丢失之前的记忆
2. 引用具体文件路径（如果从之前任务结果中可知）
3. 包含验收标准
4. 直接指令格式，不是提问

直接输出指令文本，不要包含解释、前缀或引号。"""

INSTRUCTION_USER = """\
=== 最终目标 ===
{goal}

=== 已完成的任务 ===
{completed_summary}

=== 当前要执行的任务 ===
标题: {task_title}
描述: {task_description}

请输出发送给编程助手的具体指令。"""

# ════════════════════════════════════════
# 任务结果评估（每次任务完成后）
# ════════════════════════════════════════

EVALUATION_SYSTEM = """\
你是编程任务评估专家。评估 AI 编程助手完成任务后的结果。

执行结果中的 Edit/MultiEdit/Write 工具调用被精简为只显示文件名（带 [E1]/[E2]/...
等编号）。如果你需要查看某次编辑的具体内容（old_string/new_string/content）才能
准确判断，可以请求查看详情：返回 next_action="inspect_edits"，并在 inspect_edit_indices
中列出感兴趣的编号（例如 [1, 3]）。系统会把这些编辑的完整内容补给你后再询问一次。
此请求最多生效 2 次，超出后请用现有信息直接出判断。

严格返回以下 JSON 格式：
{{
  "task_status": "done 或 failed 或 partial",
  "task_summary": "简述完成了什么（一句话）",
  "goal_achieved": true 或 false,
  "next_action": "continue 或 retry 或 insert_task 或 complete 或 inspect_edits",
  "inserted_task": {{"title": "...", "description": "..."}},
  "inspect_edit_indices": [1, 3],
  "reasoning": "判断依据（一句话）"
}}

next_action 说明：
- continue: 继续执行计划中的下一个任务
- retry: 当前任务失败，需要重试
- insert_task: 需要插入一个计划外的临时任务（必须提供 inserted_task）
- complete: 整体目标已达成，可以结束
- inspect_edits: 需要查看编辑详情才能判断（必须提供 inspect_edit_indices；只有
  task_status / task_summary / goal_achieved 等字段可暂时留空或保留预判，会再次询问）"""

EVALUATION_USER = """\
=== 最终目标 ===
{goal}

=== 任务计划概况 ===
{plan_summary}

=== 当前任务 ===
{task_title}: {task_description}

=== 编程助手的执行结果 ===
{response}"""

# ════════════════════════════════════════
# 压缩后恢复（takeover 专用）
# ════════════════════════════════════════

RESUME_SYSTEM = """\
AI 编程助手因上下文过长进行了压缩。请根据当前正在执行的任务，编写一条恢复指令。
恢复指令应提醒编程助手当前任务的目标和要求，使其能从压缩后继续工作。
直接输出指令文本。"""

RESUME_USER = """\
=== 最终目标 ===
{goal}

=== 当前正在执行的任务 ===
标题: {task_title}
描述: {task_description}

=== 已完成的任务 ===
{completed_summary}

请输出恢复指令。"""
