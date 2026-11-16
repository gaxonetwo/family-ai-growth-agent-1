# 单 Agent 工具注册与统一调用系统

## 1. 本阶段解决的问题

四个业务分析函数可以分别调用，但 Agent 需要一个安全、稳定的公共入口，才能知道“有哪些工具、每个工具解决什么问题、需要哪些参数、调用是否成功”。工具系统负责发现、校验和执行；当前 `AgentController` 在它之上完成动态工具选择和多轮执行。

系统由四层组成：

1. Tool Schema：描述并校验 Agent 可以提交的业务参数。
2. Tool Registry：保存经过批准的工具定义和真实 Python 函数引用。
3. Tool Executor：只执行注册表中的函数，并在执行前完成参数校验。
4. Result Formatter：把成功和失败整理成稳定、可 JSON 序列化的结构。

注册表不是固定 Workflow，也没有规定四个工具必须按某个顺序执行。当前单 Agent 每轮重新读取工具描述，根据用户任务和真实观察选择下一步。

## 2. Tool Registry

`ToolRegistry` 是内存中的工具白名单。它不根据用户字符串动态导入模块，也不使用 `eval` 或 `exec`。`ToolDefinition` 是不可变数据类，包含：

- 唯一工具名称；
- 面向 Agent 的中文业务描述和用途；
- 不可变的参数 Schema；
- 已批准的 Python 函数引用；
- `read_only` 属性。

重复名称会抛出 `ToolAlreadyRegisteredError`；查询不存在的名称会抛出 `ToolNotFoundError`。`list_tools()` 按名称稳定排序，`get_tool_descriptions()` 返回适合 Agent 阅读且可 JSON 序列化的描述。

`create_default_tool_registry()` 每次创建一个新的、包含四个工具的注册表。对同一个注册表重复调用 `register_business_tools()` 会明确报重复注册错误，不会静默覆盖原函数。

## 3. Tool Schema

Schema 使用 JSON Schema 风格表达，但验证器完全由 Python 标准库实现，不需要安装 `jsonschema`。每个参数记录类型、必填性、默认值、说明、枚举、日期格式或数值范围；四个 Schema 均设置 `additionalProperties=False`。

Agent 入口只暴露业务参数。源函数中的 `db_path` 和 `query_runner` 是内部测试及依赖注入接口，故意不在 Agent Schema 中，避免调用方选择任意数据库路径或注入任意对象。

外部调用日期统一要求真实的 `YYYY-MM-DD` 字符串。整数不接受布尔值，`retention_days` 必须是非空、不重复的正整数数组。可选参数缺省时，验证器复制 Schema 默认值后再调用函数，避免共享可变默认值。

四个工具的 Agent 参数如下：

| 工具 | 必填参数 | 主要可选参数 |
|---|---|---|
| `analyze_activation_funnel` | `cohort_start`, `cohort_end` | `observation_days=7`, `dataset_end_date=None` |
| `analyze_retention` | `cohort_start`, `cohort_end` | `retention_days=[1,7,30]`, `data_through_date=None`, `breakdown_by=None` |
| `analyze_user_activity` | `start_date`, `end_date` | 数据覆盖日期、`as_of_date=None`、`breakdown_by=None` |
| `analyze_feedback_trends` | `start_date`, `end_date` | `analysis_mode="rules"`、分组、覆盖日期、`page_size=100`、`top_n=10` |

Schema 验证单个参数的结构和约束。诸如起始日期晚于结束日期、请求超出数据覆盖范围等跨字段业务规则，继续由现有业务函数校验，并由执行器作为执行失败返回，避免重复或改变业务逻辑。

## 4. Tool Executor

统一入口为：

```python
from src.agent import ToolExecutor, create_default_tool_registry

executor = ToolExecutor(create_default_tool_registry())
result = executor.execute(
    "analyze_retention",
    {
        "cohort_start": "2026-08-16",
        "cohort_end": "2026-10-14",
        "breakdown_by": "acquisition_channel",
    },
)
```

执行顺序固定为安全检查流程，而不是业务 Workflow：

1. 根据精确名称在 Registry 中查找定义；
2. 确认 `arguments` 是字典；
3. 校验必填、多余字段、类型、日期、枚举和范围；
4. 应用合法默认值；
5. 只调用定义中保存的函数引用，且只调用一次；
6. 捕获工具异常并返回失败结构；
7. 检查结果是否为严格 JSON 可序列化数据；
8. 返回统一 envelope。

参数校验失败时不会执行工具。用户不能传入模块名、函数名或任意 callable，也不能通过该入口绕过 `SQLQueryTool` 的只读限制。

## 5. 统一结果

成功结果：

```json
{
  "ok": true,
  "tool_name": "analyze_retention",
  "data": {"原始业务结果": "完整保留"},
  "error": null,
  "metadata": {"read_only": true, "execution_status": "success"}
}
```

失败结果：

```json
{
  "ok": false,
  "tool_name": "analyze_retention",
  "data": null,
  "error": {
    "code": "argument_validation_error",
    "message": "Missing required argument(s): cohort_end"
  },
  "metadata": {"read_only": true, "execution_status": "failed"}
}
```

`None` 保持 `None`，`unknown` 保持字符串 `unknown`，业务指标不做重命名或删减。若业务函数显式返回 `ok=False`，执行器返回 `business_tool_failed`，不会伪装成功，并在可序列化时把原始失败内容放入 `error.details.business_result`。不支持的对象或 NaN/Infinity 会返回/抛出明确的序列化错误，不会静默删除字段。

常见错误代码：

| code | 含义 |
|---|---|
| `tool_not_found` | 工具名称不在 Registry 中 |
| `argument_validation_error` | 参数结构或值不符合 Schema |
| `tool_execution_error` | 业务函数执行时抛出异常 |
| `business_tool_failed` | 业务函数显式返回 `ok=False` |
| `result_serialization_error` | 返回值不能安全序列化为 JSON |

## 6. 四个工具为何是只读工具

四个现有分析函数均通过注入的查询接口或 `SQLQueryTool` 读取业务表。默认路径中的 `SQLQueryTool` 使用 SQLite `mode=ro`、SELECT/WITH 检查、表白名单和 SQLite authorizer；分析函数不写 CSV、数据库或 ground truth。因此注册定义均标记 `read_only=True`。

这个标记是工具元数据，不替代底层安全控制。执行器也不开放 `db_path` 和 `query_runner`，更不会改变或绕过 `SQLQueryTool`。

## 7. 如何添加第五个业务工具

新增工具时按以下顺序操作：

1. 在独立分析模块实现并测试只读业务函数，先明确业务口径；
2. 在 `tool_schemas.py` 增加不可变 Schema，只暴露必要业务参数；
3. 把 Schema 加入 `TOOL_SCHEMAS`；
4. 在 `tool_registry.py` 增加一个 `ToolDefinition`，填写清晰描述、用途和只读属性；
5. 增加 Schema 边界测试、注册测试和使用模拟函数的执行器测试；
6. 运行新测试及全部回归测试；
7. 更新本文档和 README。

不要让调用者通过字符串动态导入工具，也不要为了新增工具修改现有工具的业务定义。

## 8. 工具层与Agent层的边界

工具层负责：

- 提供当前可用工具、用途和真实参数Schema；
- 拒绝未注册工具、额外字段、类型错误和非法枚举；
- 应用Schema默认值并执行已批准的Python函数引用；
- 保留业务工具原始结果，统一成功和失败信封；
- 维持SQLQueryTool的SQLite只读限制和表白名单。

Agent层负责：

- 把原始任务、澄清历史和真实观察交给Provider；
- 决定下一步调用哪个工具、请求澄清或结束；
- 使用DateResolver和ActionParameters处理相对日期与参数冲突；
- 控制调用次数、停止失败、复用已完成调用并核验待办任务；
- 在任务完成后请求单工具或多工具报告并验证证据。

模型不属于执行边界。它返回的工具名和参数必须经过Registry与Schema，不能直接执行SQL、导入任意Python模块、传入任意函数名或指定数据库路径。

## 9. 与当前Agent的调用关系

```text
AgentController
  -> registry.get_tool_descriptions()     # 给模型当前工具说明
  -> registry.get_tool(name)              # 精确名称和只读属性检查
  -> ActionParameters / DateResolver      # 本地日期与冲突检查
  -> definition.schema.validate()         # 第一次参数校验
  -> ToolExecutor.execute()               # 再次校验并执行
  -> observation                          # 完整结果进入下一轮
```

Executor不会根据自然语言选择工具；Agent也不能绕过Executor直接调用业务函数。两层分离使决策可替换、执行边界可单独测试。

## 10. 当前限制

- Registry只有进程内状态，没有远程工具市场或动态插件安装。
- Schema是项目自研的JSON-Schema风格子集，不支持完整JSON Schema标准。
- `read_only=True`是元数据，底层仍依赖SQLQueryTool只读模式实现真正的数据保护。
- 工具层不判断模型是否正确理解用户语义；这由Agent日期、证据与澄清机制补充。
- 当前只登记四个分析工具，不允许任意SQL、任意Python或写操作。
