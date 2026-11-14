# Planned project structure

- `configs/`: 项目参数与数据配置
- `data/raw/`: 原始模拟业务 CSV
- `data/processed/`: SQLite 数据库及后续清洗数据
- `data/ground_truth/`: 仅用于离线测试的模拟场景标签
- `database/`: 数据库建表脚本
- `src/data/`: 数据生成、校验、导入
- `src/analytics/`: 后续实现指标计算和用户分群
- `src/tools/`: 后续实现 Agent 可调用的工具
- `src/agent/`: 后续实现自主规划 Agent
- `src/ui/`: 后续实现 Streamlit 界面
- `tests/`: 自动化测试
- `reports/`: 项目运行产生的报告
- `scripts/`: 项目运行入口
