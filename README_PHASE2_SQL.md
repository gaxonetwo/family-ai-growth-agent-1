# 第二阶段第一课：SQL 查询工具

在项目根目录执行：

```bash
python -m src.tools.demo_sql_queries
```

运行测试（需要安装 pytest）：

```bash
pip install pytest
pytest -q tests/test_sql_query_tool.py
```

工具代码位于 `src/tools/sql_query_tool.py`。它只允许 SELECT / WITH 查询、只允许读取五张业务表，并限制最多返回 200 行。不要将真实用户数据或敏感家庭信息放入本项目。
