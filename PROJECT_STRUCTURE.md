# 项目目录结构与模块职责

本文档描述 GitHub \`main\` 分支当前实际存在的目录和文件职责，不是未来规划清单。目录说明以当前代码为准；本地运行生成的数据文件可能被 Git 忽略，因此不会全部显示在 GitHub 文件列表中。

## 1. 根目录

\`\`\`text
family-ai-growth-agent-1/
├── .env.example
├── .gitignore
├── AGENTS.md
├── DEVELOPMENT_LOG.md
├── PROJECT_STRUCTURE.md
├── README.md
├── README_PHASE2_SQL.md
├── requirements.txt
├── configs/
├── data/
├── database/
├── docs/
├── reports/
├── scripts/
├── src/
└── tests/
\`\`\`

| 文件/目录 | 职责 |
|---|---|
| \`.env.example\` | 环境变量模板；不放真实 API Key |
| \`.gitignore\` | 排除密钥、本地数据、缓存和运行产物 |
| \`AGENTS.md\` | Codex/开发代理的架构、数据安全和开发规范 |
| \`DEVELOPMENT_LOG.md\` | 项目阶段性开发记录和里程碑 |
| \`PROJECT_STRUCTURE.md\` | 本文档，说明目录结构和模块职责 |
| \`README.md\` | 面向使用者和招聘者的项目介绍、架构及运行指南 |
| \`README_PHASE2_SQL.md\` | SQL阶段补充说明 |
| \`requirements.txt\` | Python依赖清单 |

## 2. 配置、数据与数据库

\`\`\`text
configs/
└── settings.yaml

data/
├── raw/
├── processed/
└── ground_truth/

database/
└── schema.sql
\`\`\`

| 路径 | 职责 | 当前状态与边界 |
|---|---|---|
| \`configs/settings.yaml\` | 项目数据截止日期、路径等运行配置 | 当前配置由日期解析和数据工具使用 |
| \`data/raw/\` | 原始合成业务CSV | 本地运行数据目录；数据文件受Git忽略规则管理 |
| \`data/processed/\` | 处理后的数据及\`family_ai.db\` | 本地SQLite数据库不提交到仓库 |
| \`data/ground_truth/\` | 离线评估参考标签 | 不允许Agent或业务分析工具读取 |
| \`database/schema.sql\` | SQLite表、字段和索引定义 | 只保存结构脚本，不存放运行数据库 |

GitHub仓库中的上述数据目录目前以 \`.gitkeep\` 占位；本地可能存在被忽略的CSV、SQLite和评估文件。查看GitHub时看不到这些文件，不代表本地运行环境中不存在。

当前业务数据表为 \`users\`、\`devices\`、\`events\`、\`feedback\` 和 \`payments\`。具体字段及约束以 \`database/schema.sql\` 为准。

## 3. 文档目录

\`\`\`text
docs/
├── agent_tool_system.md
├── architecture.md
├── basic_agent_loop.md
├── feedback_classification_rules.md
├── intent_recognition.md
└── lesson_sql_basics.md
\`\`\`

| 文件 | 说明 |
|---|---|
| \`architecture.md\` | 系统总体架构、模块职责、状态流和安全边界 |
| \`basic_agent_loop.md\` | 单Agent三种动作、多轮执行、日期参数、澄清恢复及报告验证 |
| \`agent_tool_system.md\` | Tool Registry、Schema、Executor和统一结果结构 |
| \`intent_recognition.md\` | 意图识别链路和相关实现说明 |
| \`feedback_classification_rules.md\` | 用户反馈主题与情感规则分类说明 |
| \`lesson_sql_basics.md\` | SQL基础学习材料 |

## 4. 源代码目录

\`\`\`text
src/
├── agent/
│   ├── intent/
│   ├── action_parameters.py
│   ├── action_provider.py
│   ├── agent_controller.py
│   ├── report_semantics.py
│   ├── result_explainer.py
│   ├── result_formatter.py
│   ├── single_tool_execution.py
│   ├── tool_executor.py
│   ├── tool_registry.py
│   └── tool_schemas.py
├── analytics/
│   ├── activation.py
│   ├── activity.py
│   ├── feedback.py
│   └── retention.py
├── data/
│   ├── feedback_classifier/
│   ├── generate_data.py
│   ├── load_sqlite.py
│   └── validate_data.py
├── tools/
│   ├── demo_activation_funnel.py
│   ├── demo_feedback_trends.py
│   ├── demo_intent.py
│   ├── demo_retention.py
│   ├── demo_sql_queries.py
│   ├── demo_user_activity.py
│   └── sql_query_tool.py
└── ui/
\`\`\`

### 4.1 \`src/agent/\`：Agent控制与报告层

负责Agent执行循环和安全工具调用。

- \`agent_controller.py\`：维护任务状态，处理 \`call_tool\`、\`clarify\`、\`finish\`，管理多轮调用、澄清恢复和完成状态。
- \`action_provider.py\`：定义动作生成接口及Mock Provider能力。
- \`action_parameters.py\`：核验日期表达来源、合并本地日期解析结果并处理参数冲突。
- \`tool_registry.py\`：保存已批准工具的名称、描述、Schema、函数引用和只读属性。
- \`tool_schemas.py\`：定义Agent可提交的业务参数及校验规则。
- \`tool_executor.py\`：重新校验参数并执行注册表中的工具函数。
- \`result_explainer.py\`：组织单工具和多工具结果解释，并检查报告结构与证据。
- \`report_semantics.py\`：按指标目录和受限声明协议校验报告语义。
- \`result_formatter.py\`：格式化单工具执行结果。
- \`single_tool_execution.py\`：单工具执行辅助流程。

### 4.2 \`src/agent/intent/\`：意图识别与日期参数层

负责单意图处理链路及Provider适配：

- \`intent_parser.py\`、\`intent_validator.py\`：意图结构解析和校验。
- \`tool_matcher.py\`：将单意图匹配到已登记工具。
- \`date_resolver.py\`：按参考日期解析支持的相对日期和明确日期范围。
- \`argument_provenance.py\`：记录参数来源并核验日期证据。
- \`provider.py\`、\`deepseek_provider.py\`、\`mock_provider.py\`：Provider接口、DeepSeek实现和离线Mock实现。
- \`intent_pipeline.py\`：串联单意图解析、工具匹配、日期处理和参数校验。

注意：\`IntentPipeline\` 是单意图入口；支持多工具连续执行的是 \`AgentController\`。二者复用部分工具基础设施，但不是同一个入口。

### 4.3 \`src/analytics/\`：业务指标计算层

四个业务分析函数实际位于此目录：

| 文件 | 业务功能 |
|---|---|
| \`activation.py\` | 用户注册、设备绑定、新手引导和首次AI交互的激活漏斗分析 |
| \`retention.py\` | D1、D7、D30等精确日期留存分析及支持的分组 |
| \`activity.py\` | DAU、滚动WAU、滚动MAU及事件量等用户活跃分析 |
| \`feedback.py\` | 用户反馈量、主题、规则情感及趋势分组分析 |

该层负责业务口径和指标计算，不负责模型决策、工具注册或自然语言报告生成。

### 4.4 \`src/data/\`：数据准备与反馈分类层

- \`generate_data.py\`：生成合成业务数据。
- \`validate_data.py\`：检查数据质量。
- \`load_sqlite.py\`：将数据导入SQLite。
- \`feedback_classifier/\`：反馈文本标准化、类别识别、情感识别和分类冲突处理等规则实现。

数据生成和导入脚本可能覆盖本地数据；运行前必须确认用户已授权，不得在普通测试或文档任务中执行。

### 4.5 \`src/tools/\`：查询工具与演示入口

- \`sql_query_tool.py\`：提供受只读模式、表白名单和查询限制约束的SQLite查询能力。
- \`demo_*.py\`：分别演示业务分析、SQL查询和单意图处理入口。

该目录不存放四个核心业务指标函数；业务指标实现位于 \`src/analytics/\`。

### 4.6 \`src/ui/\`：界面预留目录

当前GitHub中该目录只有 \`__init__.py\`，尚无已交付的Web或Streamlit界面。不要把目录存在描述成网站已经实现。

## 5. 测试目录

\`tests/\` 包含业务分析、数据质量、日期解析、意图识别、工具注册与执行、Agent控制器、澄清恢复、多工具验收以及报告证据/语义校验等测试。

| 测试文件 | 覆盖范围 |
|---|---|
| \`test_activation.py\`、\`test_retention.py\`、\`test_user_activity.py\`、\`test_feedback_trends.py\` | 四类业务分析工具 |
| \`test_feedback_classifier.py\` | 反馈规则分类 |
| \`test_date_resolver.py\`、\`test_argument_provenance.py\` | 日期解析和参数来源 |
| \`test_intent_*.py\`、\`test_demo_intent.py\` | 单意图链路和Provider |
| \`test_tool_registry.py\`、\`test_tool_executor.py\`、\`test_sql_query_tool.py\` | 工具白名单、参数校验、执行和SQL只读边界 |
| \`test_agent_controller.py\`、\`test_agent_context.py\`、\`test_agent_clarification_recovery.py\` | Agent循环、上下文和澄清恢复 |
| \`test_agent_acceptance.py\`、\`test_multi_tool_result_explainer.py\` | 多工具端到端验收和综合报告 |
| \`test_result_explainer.py\`、\`test_report_semantics.py\` | 报告结构、证据和语义约束 |

测试应使用临时数据库和Fake/Mock组件，不得读写正式模拟数据库或将离线结果冒充真实API结果。

## 6. 报告、脚本与依赖

| 路径 | 职责 |
|---|---|
| \`reports/\` | 预留分析报告输出位置；当前仓库仅有占位文件 |
| \`scripts/run_phase1.py\` | 执行第一阶段合成数据生成、校验和导入流程 |
| \`requirements.txt\` | 项目Python依赖 |

## 7. 关键架构边界

\`\`\`text
自然语言请求
    ↓
AgentController（多工具任务决策与状态管理）
    ↓
ToolRegistry / ActionParameters / DateResolver
    ↓
Tool Schema / ToolExecutor
    ↓
src/analytics 中的业务分析函数
    ↓
SQLQueryTool（只读查询）
    ↓
SQLite业务数据
    ↓
真实工具观察返回AgentController
    ↓
ResultExplainer / ReportSemantics
    ↓
证据可追踪的结构化报告
\`\`\`

- \`agent\` 决定下一步动作并控制执行边界。
- \`analytics\` 计算业务指标。
- \`tools\` 提供受限数据查询和命令行演示。
- \`data\` 生成、检查和导入合成数据。
- \`tests\` 验证各层行为和安全边界。

## 8. 维护原则

- 本文档应随实际代码结构更新，不保留与当前状态冲突的“后续实现”描述。
- 新增、删除或迁移目录时，同步检查README、AGENTS.md和本文档。
- 不为美化目录树而移动现有代码；目录迁移必须有明确收益、完整引用检查和测试。
- 本地数据文件可能被Git忽略，文档必须区分目录职责和当前仓库已跟踪的文件。
