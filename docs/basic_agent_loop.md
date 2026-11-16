# 阶段4.5.1：单 Agent 基础执行循环

## 业务价值

运营任务可能需要先看活跃，再查看留存。`AgentController` 每轮把原始请求、Registry当前工具说明和真实执行结果交给同一个Provider，由模型选择下一步。Python不预设工具顺序，只控制允许的动作和安全边界。

```text
用户任务 -> 获取工具描述 -> 模型选择动作 -> 本地校验 -> ToolExecutor
                                |                          |
                         结束或请求澄清             保存真实观察，回到决策
```

每次只执行一个工具，可连续调用不同工具。四个业务函数、Schema、只读SQLQueryTool保持原样。控制器不直接操作业务表、SQL或系统命令。

## 动作协议

```json
{"action":"call_tool","tool_name":"analyze_user_activity","arguments":{"start_date":"2026-11-07","end_date":"2026-11-13"}}
```

```json
{"action":"clarify","question":"你希望分析哪段注册日期的留存？"}
```

```json
{"action":"finish"}
```

模型每次只能返回一种动作，不能增加任意字段。阶段4.5.2额外允许call_tool携带`date_expression`，三种动作均可携带`remaining_tasks`。其余字段仍拒绝。finish表示选择停止，不表示业务问题已经被独立验证解决；没有工具调用时观察为空，不生成虚构业务结论。

## 可运行的离线示例

在项目根目录的Python会话中运行以下代码。所有工具函数都被替换为假函数，不连接正式数据库。预设动作只演示循环，不代表Mock理解了问题。

```python
from dataclasses import replace
from src.agent.action_provider import MockActionProvider
from src.agent.agent_controller import AgentController
from src.agent.tool_registry import create_default_tool_registry

registry = create_default_tool_registry()
for definition in registry.list_tools():
    registry.unregister_tool(definition.name)
    registry.register_tool(replace(
        definition, execute=lambda **kwargs: {"demo_only": True, "users": 12}
    ))

actions = [
    {"action": "call_tool", "tool_name": "analyze_user_activity",
     "arguments": {"start_date": "2026-11-07", "end_date": "2026-11-13"}},
    {"action": "call_tool", "tool_name": "analyze_retention",
     "arguments": {"cohort_start": "2026-08-16", "cohort_end": "2026-10-14"}},
    {"action": "finish"},
]
provider = MockActionProvider(response_factory=lambda prompt, context, n: {
    "output": actions[n - 1], "model": "offline-demo", "finish_reason": "stop"
})
result = AgentController(provider, registry, max_tool_calls=2).run(
    "分析2026-11-07至2026-11-13活跃和2026-08-16至2026-10-14注册用户留存。"
)
print(result["status"], result["tool_calls"])
# completed 2；users=12为假数据，没有正式业务含义。
```

## 复用、安全与返回结构

DeepSeekProvider新增`generate_action()`，共用原有JSON请求、环境配置、有限网络重试及安全错误码。`generate_intent()`和`generate_report()`保持兼容。MockActionProvider复用已有MockIntentProvider的预设响应、工厂、异常和调用记录，没有关键词分类逻辑。只有调用方显式注入Live Provider并运行控制器才会访问模型，本批次未新增CLI。

控制器默认使用四工具Registry；注入空Registry不会悄悄替换。注入Executor必须使用同一Registry。每次调用检查注册状态与只读标记，复用`schema.validate()`补齐默认值，并检查日期起止顺序；Executor会再次校验并执行注册函数。未注册函数、任意SQL、额外内部参数不会被执行。

返回包含`ok`、`status`、`user_input`、`tool_calls`、`observations`、`decisions`、`explanations`、`clarification_question`、`error`；阶段4.5.2增加`task_context`与`parameter_checks`。每条观察保留call_index、工具名、最终参数、Executor完整结果信封，数据和错误不会被模型改写。运行状态在每次run局部创建，返回深拷贝。

- completed：模型主动结束；ok=True表示循环正常结束，不保证任务分析完整。
- needs_clarification：模型询问或参数校验失败；返回中文问题，外部调用方负责接收回答。
- unsupported：工具不存在、空Registry或工具非只读。
- model_output_invalid：动作、响应元数据不合法或输出截断。
- failed：工具或Provider失败、上下文过大；此前观察仍保留。
- limit_reached：达到调用上限后模型再次要求执行工具。

max_tool_calls默认4，允许1至20。达到上限仍允许一次决策来结束或澄清，但不执行额外工具。动作请求最多max_tool_calls+1次，不含Provider内部的有限网络重试；工具失败不重试。上下文默认限60000字符，超限明确失败，不静默截断。

默认不生成报告。显式设置`explain_results=True`复用ResultExplainer逐一解释成功结果；解释失败不改变工具成功状态，解释文本不参与下一轮决策。可选解释会产生额外模型请求，本阶段没有跨工具综合报告。

## 当前限制与验证

参数校验只保证Schema约束合法，不证明所有模型参数完全符合用户原意。阶段4.5.2已接入现有DateResolver，日期依据和参数冲突可以追踪；尚未移植完整参数证据机制或澄清后的会话恢复。没有复杂反思、长期记忆、任务调度或多Agent，也不保证并发修改Registry的原子性。

## 阶段4.5.2：上下文和参数可靠性

`run(user_input, reference_date=None)`保留原有调用方式，并支持显式参考日；构造函数允许注入`date_resolver=DateResolver(settings_path)`供独立测试使用。参考日优先调用方，其次配置中的dataset_end_date。两者均无效时请求澄清，不使用电脑当天日期。

每轮请求都包含完整原文、完整真实观察、成功执行的工具调用摘要、待办提示和调用次数余额。原文不会被上一轮输出替换。可选`remaining_tasks`仅接受原文中的非空片段；它是模型的进度提示，不是语义完成证明。未提供时保留之前的提示，初始值为完整请求，completion_verified始终为false。

`src/agent/action_parameters.py`衔接DateResolver和真实Schema：

1. 验证显式date_expression确实出现在原文中；没有该字段时，唯一的“最近7天”“最近30天”“本月”“上个月”“今年”表达可以自动定位。
2. 原文存在多个时间范围时，请求模型明确选择原文片段；无法选择则停止并澄清。
3. 复用DateResolver处理相对日期或ISO范围，复用其工具字段映射，并与当前Schema的字段核对。
4. 合并模型参数与本地日期结果，值不同时保留双方并澄清，不静默覆盖。
5. 调用真实schema.validate()补齐默认值，检查日期顺序，再交Executor执行。

绝对ISO范围可以通过`date_expression`选择，也可兼容旧动作的显式日期参数。未提供日期表达时，不会仅因为任务某处出现ISO日期，就自动替空动作填写范围；旧动作缺少必要参数仍请求澄清。旧格式的直接日期参数尚未进行完整原文语义核验。

IntentValidator绑定意图候选、模型置信度与证据契约，动作协议不具备全部信息，因此不伪造模型置信度来调用它。衔接模块只构造供DateResolver使用的现有IntentDraft与ToolMatchResult；其中confidence=0.0明确为不参与计算的适配占位，不代表模型评分，也不通过它绕过置信度验证。参数约束仍来自同一Registry Schema，没有复制四套业务参数。

相对日期动作示例：

```json
{"action":"call_tool","tool_name":"analyze_user_activity","arguments":{},"date_expression":"最近7天","remaining_tasks":["再分析上个月留存"]}
```

上述待办片段必须存在于真实用户原文。参考日2026-11-13时，本地得到start_date=2026-11-07、end_date=2026-11-13；若工具是留存或激活，则得到cohort_start、cohort_end。

复用上方离线示例的假工具Registry，可运行：

```python
actions = [
    {"action": "call_tool", "tool_name": "analyze_user_activity",
     "arguments": {}, "date_expression": "最近7天", "remaining_tasks": ["再分析上个月留存"]},
    {"action": "call_tool", "tool_name": "analyze_retention",
     "arguments": {}, "date_expression": "上个月", "remaining_tasks": []},
    {"action": "finish"},
]
provider = MockActionProvider(response_factory=lambda p, c, n: {
    "output": actions[n - 1], "model": "offline-demo", "finish_reason": "stop"
})
result = AgentController(provider, registry).run(
    "先分析最近7天活跃，再分析上个月留存", reference_date="2026-11-13"
)
print([(o["tool_name"], o["arguments"]) for o in result["observations"]])
```

parameter_checks保留原始动作参数、原文日期片段、DateResolver的参考日及来源、最终参数和冲突明细。日期能解析不代表数据库覆盖完整；例如“今年”可能超出模拟数据期，业务工具仍按原有覆盖规则拒绝执行。当前不处理任意自然语言、否定语境或复杂时间范围归属；不确定时需要澄清。没有增加澄清恢复或跨工具报告。

```powershell
python -m unittest discover -s tests -p "test_agent_controller.py" -v
python -m unittest discover -s tests -p "test_agent_context.py" -v
python -m pytest -q
git diff --check
```

专项测试使用Mock、Fake工具和Fake SDK，阻止SQL、数据库连接及真实网络。完整历史回归包含已有的只读正式模拟数据检查，不得为测试重新生成数据。
