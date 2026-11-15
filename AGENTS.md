# Family AI Growth Agent 开发指南

## 项目背景

Family AI Growth Agent 是面向 Family AI 智能硬件和软件产品的运营分析项目。运营人员通过自然语言提出业务问题，Agent 负责理解任务、动态制定计划、选择合适工具、查询业务数据、完成分析并生成可解释的运营报告。

项目中的业务数据均为合成数据，不代表真实用户。不得在项目中加入真实家庭成员、儿童个人信息、语音、视频、聊天记录或其他敏感数据。

## 核心架构原则

本项目采用**单 Agent 自主规划架构**。

单个 Agent 应能够根据问题动态完成：

1. 理解业务目标。
2. 制定或调整执行计划。
3. 自主选择 SQL 查询、指标分析等工具。
4. 根据工具结果继续执行、重试或重新规划。
5. 汇总证据并生成最终运营分析报告。

不得将该架构改造成多个 Agent 固定分工，也不得把自主规划过程改造成步骤写死的固定 Workflow。可以开发独立、可复用的工具和分析函数，但工具的调用顺序应由未来的单 Agent 根据任务动态决定。

未经用户明确确认，不引入 LangGraph 或其他 Agent 框架。

## 业务目标

项目计划逐步支持：

- 用户注册、设备绑定和激活漏斗分析。
- DAU、WAU、MAU 等活跃指标分析。
- D1、D7、D30 等留存分析。
- AI 功能和其他产品功能的使用分析。
- 设备在线状态和连接问题分析。
- 用户反馈主题、情感和问题趋势分析。
- 支付转化、成功支付和退款分析。
- 由单 Agent 综合调用工具并生成运营报告。

所有业务指标必须先明确计算口径，再实现查询或分析代码。不得把事件数量误当成独立用户数量，也不得使用观察窗口不完整的用户计算需要完整窗口的指标。

## 当前目录结构

```text
family-ai-growth-agent-1/
├── configs/
│   └── settings.yaml              # 项目参数、数据截止日期和路径配置
├── data/
│   ├── raw/                       # 五张模拟业务 CSV（本地数据，Git 忽略）
│   ├── processed/                 # family_ai.db（本地数据，Git 忽略）
│   └── ground_truth/              # 离线评估标签，不得供 Agent 查询
├── database/
│   └── schema.sql                 # SQLite 表结构和索引
├── docs/
│   └── lesson_sql_basics.md       # SQL 教学文档
├── reports/                       # 分析报告输出目录
├── scripts/
│   └── run_phase1.py              # 第一阶段完整数据流水线
├── src/
│   ├── data/                      # 数据生成、校验和 SQLite 导入
│   ├── tools/                     # Agent 可调用工具及演示入口
│   ├── analytics/                 # 指标和业务分析逻辑
│   ├── agent/                     # 后续单 Agent 自主规划实现
│   └── ui/                        # 后续交互界面
├── tests/                         # 自动化测试
├── README.md
├── README_PHASE2_SQL.md
├── PROJECT_STRUCTURE.md
└── requirements.txt
```

当前已经实现：

- 合成数据生成、基础数据校验和 SQLite 导入代码。
- 只允许读取五张业务表的 `SQLQueryTool`。
- SQL 查询示例和安全边界测试。
- 用户激活漏斗分析及其独立临时数据库测试。
- 精确日期用户留存分析、渠道/套餐分组及其独立临时数据库测试。

## 数据表和数据边界

业务分析允许使用以下五张表：

- `users`：注册时间、渠道、地区和当前套餐。
- `devices`：设备绑定、设备类型、当前状态和最后在线时间。
- `events`：用户行为、AI 交互、功能使用和设备状态事件。
- `feedback`：反馈时间、渠道和文本。
- `payments`：支付时间、金额、套餐和支付状态。

`data/ground_truth/` 中的数据只用于离线评估，不属于 Agent 的业务查询数据源。业务分析工具不得读取、关联或泄露 ground truth。

保留现有模拟数据：

- 不重新生成或覆盖已有 CSV，除非用户明确要求。
- 不删除、覆盖或随意迁移 `family_ai.db`。
- 不运行 `scripts/run_phase1.py`、`src.data.generate_data` 或 `src.data.load_sqlite`，除非用户明确授权数据重建。
- 修改数据库结构前必须先获得用户确认。
- 测试必须使用临时数据库或独立测试夹具，不得修改正式模拟数据。

## 技术栈

- Python 3.10+
- SQLite
- pandas 2.x
- Python 标准库 `sqlite3`、`unittest`、`pathlib` 等
- pytest：现有部分测试使用；运行前需确认环境已安装
- YAML 配置文件：当前未引入 PyYAML，简单配置读取应避免无必要新增依赖

优先使用标准库和现有依赖。未经用户确认，不安装大量依赖，不引入重量级框架。

## 常用运行命令

所有命令默认从项目根目录执行。

创建并激活虚拟环境：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

安装项目已有依赖：

```powershell
pip install -r requirements.txt
```

运行 SQL 查询示例：

```powershell
python -m src.tools.demo_sql_queries
```

运行激活漏斗分析：

```powershell
python -m src.tools.demo_activation_funnel 2026-08-16 2026-11-06 --observation-days 7
```

运行激活漏斗测试（不需要 pytest）：

```powershell
python -m unittest discover -s tests -p "test_activation.py" -v
```

运行留存分析和测试：

```powershell
python -m src.tools.demo_retention 2026-08-16 2026-10-14 --breakdown-by acquisition_channel
python -m unittest discover -s tests -p "test_retention.py" -v
```

运行现有 pytest 测试（仅在 pytest 已安装且所需数据存在时）：

```powershell
pytest -q
```

以下命令会生成或覆盖数据，默认禁止运行：

```powershell
python -m src.data.generate_data --users 1000 --days 90 --seed 42
python -m src.data.load_sqlite
python scripts/run_phase1.py
```

## 测试要求

每次修改代码后必须运行与本次修改直接相关的测试，并如实报告命令和结果。

- 新功能必须包含正常路径、边界条件、空数据和无效参数测试。
- 数据分析测试应覆盖重复事件，确保统计独立用户而非事件条数。
- 涉及时间窗口时，应测试窗口内、窗口边界、窗口外和观察窗口不足。
- 涉及 SQL 工具时，应测试只读限制、表访问边界、返回行数限制和错误处理。
- 测试不得依赖或修改正式数据库；优先创建临时 SQLite 数据库。
- 不得因为正式数据缺失而伪造实际运行结果。
- 如果环境缺少测试依赖，应明确说明；未经确认不要擅自安装大量依赖。
- 测试失败时应先定位并修复原因，再交付；不得声称失败的测试已经通过。

除相关单元测试外，应进行适当的语法或静态检查。不要为了追求全量测试而运行会生成数据或覆盖数据库的脚本。

## 开发约束

1. 每次只完成用户指定的一个明确开发任务，完成后停止，等待确认。
2. 开始编码前先检查现有目录、接口、配置和测试，基于现有项目增量开发，不重建项目。
3. 保留现有代码和目录结构，不删除无关功能。
4. 将数据库访问、业务计算和展示层尽量分离，保持函数职责清晰。
5. 优先复用 `SQLQueryTool` 等现有接口，不绕过已有安全边界直接开放任意 SQL。
6. 所有查询应使用独立用户数和明确的 UTC 时间口径。
7. 不允许业务分析读取 ground truth。
8. 未经用户确认，不进行大规模重构、目录迁移、数据库迁移或依赖升级。
9. 未经用户确认，不引入 LangGraph、多 Agent 框架或固定 Workflow 编排。
10. 不修改与当前任务无关的用户代码；发现潜在问题时先在交付报告中说明。
11. 不编造数据、测试结果、性能结果或业务结论。

## 教学式交付要求

用户正在学习 Python。每次开发完成后，必须用中文解释，而不是只提交代码。至少说明：

1. 本次功能解决什么业务问题。
2. 为什么需要该功能。
3. 使用了哪些数据表和字段。
4. 核心代码如何工作。
5. 为什么选择当前技术方案。
6. 如何运行和测试。
7. 实际执行了哪些测试，结果是什么。
8. 运行结果代表什么业务含义。
9. 当前实现有哪些限制和后续可能的改进方向。

如果正式 CSV 或数据库在当前工作树中不存在，应明确区分“临时测试数据结果”和“正式业务数据结果”，不得用测试夹具结果冒充正式分析结果。

