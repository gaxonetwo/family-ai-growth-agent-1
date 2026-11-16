# Family AI Growth Agent

面向 Family AI 智能硬件与软件产品的运营分析 Agent 项目。项目采用“单 Agent 自主规划 + 工具调用”的路线，优先使用免费、本地可运行的技术。

## 当前进度

第一阶段：模拟业务数据集与 SQLite 数据库。

- 生成模拟用户、设备、行为事件、用户反馈、付费记录。
- 单独生成用户场景标签和反馈分类标签（ground truth），不混入业务数据表，避免 Agent 直接读取答案。
- 执行数据质量检查。
- 将 CSV 数据导入 SQLite。
- 已实现只读 SQL 查询工具。
- 已实现用户激活漏斗分析工具。
- 已实现精确日期用户留存分析工具。
- 已实现 DAU、滚动 WAU 和滚动 MAU 用户活跃分析工具。
- 已实现反馈主题、情感与负面趋势分析工具。
- 已实现四个业务工具的统一注册、参数发现、校验和安全执行入口。

> 所有数据均为合成数据，不代表任何真实公司或真实用户。反馈文本为虚构的产品体验描述，不包含真实家庭成员、儿童语音、视频或聊天内容。

## 环境要求

- Python 3.10+
- pip

## 快速开始

建议在项目根目录执行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
source .venv/bin/activate
```

安装依赖：

```bash
pip install -r requirements.txt
```

生成数据：

```bash
python -m src.data.generate_data --users 1000 --days 90 --seed 42
```

检查数据：

```bash
python -m src.data.validate_data
```

导入 SQLite：

```bash
python -m src.data.load_sqlite
```

完成后会生成：

- `data/raw/*.csv`
- `data/ground_truth/user_scenarios.csv`
- `data/ground_truth/feedback_labels.csv`
- `data/processed/family_ai.db`

## 数据表

| 表 | 用途 |
|---|---|
| users | 用户注册、渠道、地区、套餐 |
| devices | 设备绑定与当前状态 |
| events | App、AI 功能和设备行为事件 |
| feedback | 用户反馈及模拟分类标签 |
| payments | 付费订单 |
| user_scenarios | 仅用于测试评估的模拟场景标签 |

## 重要设计约定

### 三个业务工具统一口径（质量完善）

所有指标按 UTC 计算；cohort/趋势起止日期包含当天。默认完整数据覆盖声明为 2026-08-16 至 2026-11-13，来自 settings.yaml 的 dataset_end_date 和 default_days；这一声明不能自动证明区间内没有采集中断。

| 工具 | 有效依据 | 时间窗口和限制 |
|---|---|---|
| 激活 | devices.bound_at；onboarding_complete；ai_interaction | 默认注册后7天，窗口左闭右开；严格要求 注册 <= 绑定 <= 引导 <= AI；各阶段独立用户去重，只统计完整观察窗口用户 |
| 留存 | app_open、ai_interaction | Exact-day：UTC注册日期+n日；默认D1/D7/D30共用成熟到D30的用户分母；不是区间留存 |
| 活跃 | app_open、ai_interaction、feature_use | DAU当天，WAU含当天及前6天，MAU含当天及前29天；历史不足返回None，覆盖外请求报错 |

激活允许相等时间；每步寻找满足前置条件的最早事件，早期乱序事件不阻止后续合法事件完成激活。查询按注册日分批，保持 SQLQueryTool 执行限制。中间结果物化语法需要 SQLite 3.35+。

留存率、激活率以0～1返回；活跃输出人数而非比例。套餐分组使用当前套餐快照，不能还原历史套餐。活跃趋势仅首尾对比，不能证明持续变化或原因。严格顺序可能排除模拟数据中引导早于绑定的用户，不能据此直接断言真实产品引导问题。

1. `user_scenarios.csv` 是评估标签，不应作为 Agent 的业务查询数据源。
2. `events` 是事件事实表；`devices.current_status` 表示数据集结束时的设备状态。
3. 留存、激活、功能使用和付费指标应在后续分析模块中统一定义口径。
4. 所有时间字段使用 ISO 8601 格式，默认按 UTC 存储；本项目的模拟数据不对应真实地域时区。
5. 不将真实儿童信息、语音、视频、聊天记录或家庭隐私放入项目数据。

## 用户激活漏斗分析

激活定义：用户注册后的观察窗口内，同时完成设备绑定、新手引导和至少一次 AI 交互。默认观察窗口为 7 天，只统计拥有完整观察窗口的注册用户。

漏斗采用累计口径：新手引导阶段只统计已经绑定设备的用户，首次 AI 交互阶段只统计已经完成前两步的用户。日期和时间统一按 UTC 计算，窗口为 `[注册时间, 注册时间 + observation_days)`。

运行示例：

```bash
python -m src.tools.demo_activation_funnel 2026-08-16 2026-11-06 --observation-days 7
```

在 Python 中调用：

```python
from src.analytics.activation import analyze_activation_funnel

result = analyze_activation_funnel("2026-08-16", "2026-11-06", 7)
```

运行独立单元测试（仅使用 Python 标准库，测试会创建临时数据库，不修改正式数据）：

```bash
python -m unittest discover -s tests -p "test_activation.py" -v
```

## 用户留存分析

留存采用 Exact-day Retention：D1、D7、D30 分别表示用户在注册日期后的第 1、7、30 个 UTC 日历日，是否至少发生一次 `app_open` 或 `ai_interaction`。多个指标统一使用已经完整观察到最大请求天数的成熟用户作为分母。

运行总体留存分析：

```bash
python -m src.tools.demo_retention 2026-08-16 2026-10-14
```

按获客渠道分组：

```bash
python -m src.tools.demo_retention 2026-08-16 2026-10-14 --breakdown-by acquisition_channel
```

也可使用 `--breakdown-by plan_type`，或用 `--data-through-date YYYY-MM-DD` 显式指定可靠的数据截止日期。未指定时读取 `configs/settings.yaml` 的 `dataset_end_date`。

运行独立测试：

```bash
python -m unittest discover -s tests -p "test_retention.py" -v
```

## 用户活跃分析

支持关键字参数 `as_of_date`（默认 end_date）、`breakdown_by`（acquisition_channel 或 plan_type）。返回基准日 `dau/wau/mau`、`daily_trend`、分组 `breakdown` 和首尾 DAU 对比 `trend_analysis`。套餐取当前 users.plan_type，不代表历史套餐。首尾对比不能证明持续趋势或业务原因。

无参数演示：`python -m src.tools.demo_user_activity`，展示配置截止日指标及渠道、套餐分组。日期参数仍可指定趋势区间。

有效活跃事件包括 `app_open`、`ai_interaction` 和 `feature_use`。DAU 是某个 UTC 自然日内的独立活跃用户数；WAU 是截至该日向前 7 个自然日内的独立活跃用户数；MAU 是截至该日向前 30 个自然日内的独立活跃用户数。

事件记录数与活跃用户数会分别返回。同一个用户一天产生多条事件，只增加事件记录数，不会被重复计入 DAU、WAU 或 MAU。

```bash
python -m src.tools.demo_user_activity 2026-10-15 2026-11-13
```

数据完整覆盖范围默认根据 `configs/settings.yaml` 中的 `dataset_end_date` 和 `default_days` 计算。请求超出覆盖范围会报错；WAU 或 MAU 的历史窗口不完整时返回 `None`，不会把缺失数据表示为零活跃。

运行独立测试：

```bash
python -m unittest discover -s tests -p "test_user_activity.py" -v
```

## 用户反馈趋势分析

`analyze_feedback_trends()` 使用 feedback 和 users，通过现有只读 SQLQueryTool 查询，复用规则版本1.0.0的 `classify_feedback_text()`，不调用大模型API，不读取ground_truth，不将分类标签写入数据库。

```python
from src.analytics.feedback import analyze_feedback_trends

result = analyze_feedback_trends(
    "2026-08-16", "2026-11-13",
    breakdown_by="channel",
    analysis_mode="rules",
)
```

结果包含总量、独立反馈用户数、原文种类、每日数量、主题/情感及状态分布、每日负面数量及占比、前10种高频原文和可选分组。相同文本的不同反馈记录分别计数，用户数按user_id去重，原始文本不做清洗改写。

分组白名单为 `channel`（反馈提交渠道）、`acquisition_channel`（用户获客渠道）、`plan_type`（当前套餐快照，不能还原提交时的历史套餐）。缺少关联用户时保留反馈，用户属性分组值为null并返回警告。

`analysis_mode="none"` 不调用分类器，仅返回原始统计；主题、情感、状态分布、负面趋势和分类计数为None，不能解读为没有负面反馈。`rules`模式单独保留unknown和invalid_input，不将unknown并入neutral。`summary.unknown_category/unknown_sentiment`包括invalid_input带来的unknown标签，`valid_unknown_*`则排除无效输入；两种统计不混淆。

日期按UTC，起止日期包含当天，SQL采用 `[开始日零点, 结束日次日零点)`；默认覆盖来自配置的2026-08-16至2026-11-13。可用 `data_start_date` 和 `data_through_date` 显式声明其他数据覆盖范围，覆盖外请求报错。配置或调用者声明不能证明内部采集无中断。

比例使用0～1小数并保留4位；主题/情感分母为范围内所有反馈（包括unknown与invalid_input），每日负面比例分母为当天所有反馈，分组比例分母为组内反馈。完整覆盖且无反馈的日期数量和负面比例均为0。分类器异常将抛出带反馈ID的FeedbackAnalysisError，不生成成功的部分报告。

默认每页100条，通过唯一feedback_id的游标分页，核对COUNT、ID唯一性、独立用户数、不同文本数及每页truncated。截断、重复ID、缺页或计数变化会报错。SQLQueryTool的各次查询是独立连接，不保证并发修改时的跨页事务快照；正式只读模拟库可正常使用。

演示命令（无参数默认读取配置完整时间范围）：

```powershell
python -m src.tools.demo_feedback_trends
python -m src.tools.demo_feedback_trends 2026-11-01 2026-11-13 --breakdown-by acquisition_channel
python -m src.tools.demo_feedback_trends 2026-08-16 2026-11-13 --breakdown-by plan_type
python -m src.tools.demo_feedback_trends 2026-08-16 2026-11-13 --analysis-mode none
python -m unittest discover -s tests -p "test_feedback_trends.py" -v
```

正式库验证（2026-08-16至2026-11-13）：186条反馈、186位用户、15种文本，负面85、正面52、中性49，unknown与invalid_input均0，歧义标记81。反馈趋势测试29项通过。规则基于少量模板，结果表示文本规则分类，不是独立样本准确率或满意度评分。

## 单 Agent 工具注册与统一调用

`src/agent/` 提供 Tool Registry、JSON-Schema 风格参数定义、Tool Executor 和稳定结果格式。Registry 仅保存明确注册的四个业务函数，不根据用户输入动态导入或执行 Python 代码；Executor 先验证结构化参数，再调用已注册函数。四个工具均为只读分析工具，Agent Schema 不开放内部 `db_path` 和 `query_runner` 参数。

```python
from src.agent import ToolExecutor, create_default_tool_registry

registry = create_default_tool_registry()
print(registry.get_tool_descriptions())

executor = ToolExecutor(registry)
result = executor.execute(
    "analyze_user_activity",
    {"start_date": "2026-11-01", "end_date": "2026-11-13"},
)
```

成功和失败均返回包含 `ok`、`tool_name`、`data`、`error`、`metadata.execution_status` 的统一结构。参数缺失、类型或日期错误、多余字段、非法枚举都会在执行前失败，业务函数不会被调用。完整设计、错误代码、四个 Schema 及新增第五个工具的方法见 `docs/agent_tool_system.md`。

专项测试：

```powershell
python -m unittest discover -s tests -p "test_tool_registry.py" -v
python -m unittest discover -s tests -p "test_tool_executor.py" -v
python -m unittest discover -s tests -p "test_result_formatter.py" -v
```

## 单 Agent 基础执行循环（阶段4.5.1）

`src/agent/agent_controller.py` 新增 `AgentController`：每轮由模型根据原始任务、当前工具说明和真实观察选择调用一个工具、请求澄清或结束。允许连续调用不同工具，不写死业务工具顺序。

执行前复用Registry白名单与现有Tool Schema，经ToolExecutor调用只读工具；默认最多4次调用，失败即停止并保留错误。返回真实观察，可显式启用ResultExplainer逐工具解释；尚无跨工具综合报告。

`src/agent/action_provider.py` 提供离线Mock接口，DeepSeekProvider新增独立的`generate_action()`并复用安全传输。未新增自动Live或正式数据库调用入口。使用方法和离线双工具示例见[基础循环说明](docs/basic_agent_loop.md)。

阶段4.5.2增加每轮原文及任务上下文保留、日期和参数检查轨迹。`run(user_input, reference_date=None)`复用DateResolver处理相对日期及ISO范围，参考日优先显式值，其次配置数据截止日期。日期字段按工具映射并核对真实Schema，模型日期与本地结果冲突时停止澄清，不静默覆盖。尚未实现澄清恢复或多工具综合报告。

```powershell
python -m unittest discover -s tests -p "test_agent_controller.py" -v
python -m unittest discover -s tests -p "test_agent_context.py" -v
```

## 后续阶段规划

- 第二阶段：LangGraph 单 Agent，自主规划、工具选择、执行反馈与动态重规划。
- 第三阶段：SQL 查询、Python 指标分析、反馈分类等工具。
- 第四阶段：Streamlit 运营助手与可视化看板。
- 第五阶段：业务案例测试、结果评估、GitHub 文档和简历整理。
