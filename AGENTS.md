# Family AI Growth Agent 开发指南

## 1. 项目定位

Family AI Growth Agent 面向 Family AI 智能硬件与软件产品的运营分析场景。运营人员通过自然语言提出业务问题，单 Agent 在已注册的只读业务工具范围内逐轮选择动作，调用工具取得真实观察，并在任务完成后生成可追踪证据的分析报告。

当前实现是**有边界的单 Agent 执行循环**，不是多 Agent 系统，也不是步骤写死的固定 Workflow。模型每轮选择 \`call_tool\`、\`clarify\` 或 \`finish\`；Python 负责工具白名单、日期解析、参数校验、只读执行、状态管理和报告验证。

项目数据均为合成数据，不代表真实公司或真实用户。不得加入真实家庭成员、儿童个人信息、语音、视频、聊天记录或其他敏感数据。

## 2. 当前能力与限制

### 已实现

- 四个只读业务分析工具：激活漏斗、用户留存、用户活跃和用户反馈趋势。
- DeepSeek Provider 用于生成动作；Mock/Fake Provider 用于离线验收。
- Agent 根据原始任务、当前工具说明和真实工具观察逐轮决定下一步。
- 支持多个业务工具连续调用，默认最多4次工具调用。
- 支持已登记的相对日期表达及明确 ISO 日期范围。
- 最多两轮澄清；恢复时保留原始请求、澄清历史和成功观察，并可复用相同的成功调用。
- \`remaining_tasks\` 必须提供用户原文证据，模型摘要不能替代原文。
- 单工具解释和多工具综合报告均经过结构、证据路径、原始数值和语义约束校验。
- SQL 查询通过只读 \`SQLQueryTool\` 访问允许的业务表。

### 当前不支持

- 多 Agent 协作、长期记忆、跨会话任务持久化、并行工具执行。
- 开放式自主规划、任意反思或自动重试业务工具。
- 任意 SQL、任意 Python、系统命令或任意模块导入。
- 已交付的 Web/Streamlit 用户界面或公网部署。
- 尚未注册为 Agent 工具的设备运维、支付转化等分析能力。

不得把未实现能力描述成当前功能。

## 3. 核心架构原则

1. 保持单 Agent。工具调用顺序由 Agent 根据任务和观察逐轮决定，不得改成多个 Agent 固定分工或固定业务 Workflow。
2. 模型只生成受限动作和报告草稿，不是工具执行边界。
3. 工具必须登记在 \`ToolRegistry\` 并通过真实 Schema 校验；执行只能调用注册表中保存的函数引用。
4. 日期解析、参数来源核验、工具权限、执行状态和报告证据验证由本地 Python 代码负责。
5. 工具失败时停止后续执行并保留已有成功观察，不得伪装成功或擅自重试。
6. 业务指标必须有明确口径；不得把事件数量当成独立用户数，也不得使用观察窗口不完整的用户计算要求完整窗口的指标。
7. 未经用户明确确认，不引入 LangGraph、AutoGen、CrewAI 或其他 Agent 框架。

## 4. 当前目录结构

\`\`\`text
family-ai-growth-agent-1/
├── configs/
│   └── settings.yaml                 # 数据截止日期、路径等项目配置
├── data/
│   ├── raw/                          # 本地原始合成业务CSV；运行数据不提交
│   ├── processed/                    # 本地处理数据及family_ai.db；运行数据不提交
│   └── ground_truth/                 # 离线评估数据；禁止Agent业务查询
├── database/
│   └── schema.sql                    # SQLite表结构和索引定义
├── docs/                             # 架构、Agent循环、工具系统及业务规则文档
├── reports/                          # 分析报告输出位置
├── scripts/
│   └── run_phase1.py                 # 合成数据生成、校验和导入流水线
├── src/
│   ├── agent/
│   │   └── intent/                   # 意图识别、Provider、日期解析和参数来源核验
│   ├── analytics/                    # 激活、留存、活跃和反馈业务指标计算
│   ├── data/
│   │   └── feedback_classifier/      # 反馈文本标准化、主题和情感规则分类
│   ├── tools/                        # SQL只读查询工具和命令行演示入口
│   └── ui/                           # 界面模块预留；当前未实现可用界面
├── tests/                            # 单元、集成和离线端到端测试
├── .env.example                      # 环境变量模板，不包含真实密钥
├── AGENTS.md                         # Codex开发规范
├── DEVELOPMENT_LOG.md                # 项目阶段记录
├── PROJECT_STRUCTURE.md              # 目录结构和模块职责
├── README.md                         # 项目介绍与运行指南
└── requirements.txt                  # Python依赖
\`\`\`

仓库中的 \`data/raw/\`、\`data/processed/\`、\`data/ground_truth/\` 和 \`reports/\` 当前保留目录占位文件；本地运行数据按 Git 忽略规则管理。GitHub 页面未显示本地数据文件，不代表本地运行环境中不存在。

## 5. 业务数据与安全边界

当前业务表：

- \`users\`：用户注册时间、渠道、地区和当前套餐。
- \`devices\`：设备绑定、设备类型、当前状态和最后在线时间。
- \`events\`：应用打开、AI交互、功能使用及设备状态事件。
- \`feedback\`：用户反馈时间、渠道和文本。
- \`payments\`：支付时间、金额、套餐和支付状态。

数据使用规则：

- \`data/ground_truth/\` 只用于离线评估，不属于 Agent 可访问的数据源。
- 未经用户明确授权，不重新生成、覆盖、删除或迁移 CSV 和 \`family_ai.db\`。
- 不运行 \`scripts/run_phase1.py\`、\`src.data.generate_data\` 或 \`src.data.load_sqlite\`，除非用户明确授权数据重建。
- 修改数据库结构前先获得用户确认。
- 测试必须使用临时 SQLite 数据库或独立夹具，不得修改正式模拟数据。
- SQL 工具维持只读模式、表白名单和查询限制；不得暴露任意数据库路径或查询执行器。
- API Key 只从环境变量读取；不得提交真实密钥、个人数据或本地数据库。

## 6. 模块职责边界

| 目录/模块 | 负责内容 | 不应承担的职责 |
|---|---|---|
| \`src/agent/\` | Agent循环、动作处理、工具注册与执行、参数处理、报告生成和语义校验 | 不直接计算业务指标或绕过只读查询工具 |
| \`src/agent/intent/\` | 单意图链路、Provider、日期解析、参数来源核验和意图校验 | 不代替多工具AgentController循环 |
| \`src/analytics/\` | 四类业务指标的计算和口径处理 | 不负责模型决策或生成LLM报告 |
| \`src/data/\` | 合成数据生成、质量检查、SQLite导入及反馈规则分类 | 不负责Agent工具选择 |
| \`src/tools/\` | SQL只读查询接口和命令行演示入口 | 不重复实现业务指标，不动态导入任意函数 |
| \`tests/\` | 验证业务口径、工具边界、Agent状态和报告证据 | 不连接或修改正式业务数据 |
| \`docs/\` | 系统设计、执行机制、工具和业务规则说明 | 不替代代码实现 |
| \`src/ui/\` | 后续界面开发预留位置 | 当前不是已交付的Web/Streamlit UI |

四个业务分析函数位于 \`src/analytics/\`；\`src/tools/\` 主要提供 SQL 查询工具和演示入口。Agent 通过 Registry/Executor 调用已注册的业务函数，不代表业务函数位于 \`src/tools/\`。

## 7. 技术栈与依赖原则

- Python 3.10+、SQLite、标准库 \`sqlite3\`
- pandas 2.x
- OpenAI Python SDK（调用 DeepSeek 的 OpenAI 兼容接口）
- pytest、unittest
- 项目自研的 JSON Schema 风格参数校验

优先复用标准库和现有依赖。未经用户确认，不安装大量依赖、不升级关键依赖、不引入重量级框架。

## 8. 常用运行命令

所有命令默认从项目根目录执行。

\`\`\`powershell
python -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
\`\`\`

运行四个业务工具示例：

\`\`\`powershell
python -m src.tools.demo_activation_funnel 2026-08-16 2026-11-06 --observation-days 7
python -m src.tools.demo_retention 2026-08-16 2026-10-14 --breakdown-by acquisition_channel
python -m src.tools.demo_user_activity 2026-10-15 2026-11-13
python -m src.tools.demo_feedback_trends 2026-08-16 2026-11-13 --analysis-mode rules
\`\`\`

运行单意图 CLI 和测试：

\`\`\`powershell
python -m src.tools.demo_intent --help
python -m src.tools.demo_intent --reference-date 2026-11-13
python -m pytest -q
\`\`\`

以下命令会生成或覆盖数据，默认禁止运行，除非用户明确授权：

\`\`\`powershell
python scripts/run_phase1.py
python -m src.data.generate_data
python -m src.data.load_sqlite
\`\`\`

## 9. 测试与交付要求

1. 修改前检查现有接口、目录、配置和相关测试，采用增量修改，不重建项目。
2. 新逻辑补充正常路径、边界条件、空数据和无效参数测试。
3. 时间窗口测试覆盖窗口内、边界、窗口外和观察窗口不足。
4. SQL工具测试覆盖只读限制、表访问边界、行数限制和错误处理。
5. 使用临时数据库或 Fake/Mock，不访问或修改正式数据库。
6. 运行与修改直接相关的测试；适当时运行完整回归和语法/静态检查。
7. 如实报告命令和结果；不得把 Fake 测试说成真实 API 联调。
8. 不因正式数据缺失而伪造业务分析结果。

用户正在学习 Python。开发交付应使用中文说明业务问题、设计理由、核心实现、运行方式、实际测试结果和当前限制，不要只给代码或提交记录。

## 10. Git 与变更约束

- 每次只完成用户指定的明确任务，完成后说明结果并等待确认。
- 不修改与当前任务无关的代码或文件。
- 未经用户确认，不进行大规模重构、目录迁移、数据库迁移或依赖升级。
- 不删除无关功能或本地联调文件。
- 不擅自 Commit、Push、合并到 \`main\` 或强制推送；只有用户明确要求时才执行相应 Git 操作。
- 交付时明确列出修改文件、测试结果、Git状态及未完成事项。
