# Family AI Growth Agent

面向 Family AI 智能硬件与软件产品的运营分析 Agent 项目。项目采用“单 Agent 自主规划 + 工具调用”的路线，优先使用免费、本地可运行的技术。

## 当前进度

第一阶段：模拟业务数据集与 SQLite 数据库。

- 生成模拟用户、设备、行为事件、用户反馈、付费记录。
- 单独生成用户场景标签和反馈分类标签（ground truth），不混入业务数据表，避免 Agent 直接读取答案。
- 执行数据质量检查。
- 将 CSV 数据导入 SQLite。

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

1. `user_scenarios.csv` 是评估标签，不应作为 Agent 的业务查询数据源。
2. `events` 是事件事实表；`devices.current_status` 表示数据集结束时的设备状态。
3. 留存、激活、功能使用和付费指标应在后续分析模块中统一定义口径。
4. 所有时间字段使用 ISO 8601 格式，默认按 UTC 存储；本项目的模拟数据不对应真实地域时区。
5. 不将真实儿童信息、语音、视频、聊天记录或家庭隐私放入项目数据。

## 后续阶段规划

- 第二阶段：LangGraph 单 Agent，自主规划、工具选择、执行反馈与动态重规划。
- 第三阶段：SQL 查询、Python 指标分析、反馈分类等工具。
- 第四阶段：Streamlit 运营助手与可视化看板。
- 第五阶段：业务案例测试、结果评估、GitHub 文档和简历整理。
