# 阶段4.2：意图识别与参数验证

本阶段将自然语言请求转换为结构化任务，不执行业务工具。
这是未来单 Agent 的可复用输入处理能力，不承担自主规划。

## 模块与调用关系

`IntentPipeline` 依次复用以下模块，并在失败或需要澄清时提前结束：

1. `IntentParser`：向 Provider 提交原始输入与动态 Registry 工具描述，检查模型 JSON 结构。
2. `ToolMatcher`：用集中维护的四种意图映射匹配已注册工具，不使用模糊匹配。
3. `DateResolver`：本地计算日期；显式参考日期优先，其次使用配置中的数据截止日期，不使用电脑当天日期兜底。
4. `IntentValidator`：复用 Tool Schema，检查参数、日期范围和配置的置信度阈值。
5. `argument_provenance`：记录用户表达、模型提取、日期计算和 Schema 默认值的来源，保留冲突及澄清问题。

原始请求完整传给 Validator。一次澄清由
`continue_with_clarification(previous_result, answer)` 处理，原始请求与回答分别保存。
最多处理一次补充回答，未解决的问题仍返回 `needs_clarification`。

## Provider 与配置

默认使用离线 `MockIntentProvider`。其响应是显式预设，不代表真实模型识别。
只有 `--live` 才选择 `DeepSeekProvider`；禁止模型自动切换及静默回退 Mock。

Live 模式从环境变量读取 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_MODEL`，配置项见根目录
`.env.example`。程序不自动加载 `.env`。不要将真实密钥写入版本库。
置信度阈值通过 `INTENT_CONFIDENCE_THRESHOLD` 或 CLI 参数显式设置。

```powershell
python -m src.tools.demo_intent --confidence-threshold 0.8
python -m src.tools.demo_intent --scenario clarification --confidence-threshold 0.8
python -m src.tools.demo_intent --live --confidence-threshold 0.8
```

示例输入：`分析最近7天的活跃情况`。
Mock 的意图由 `--intent` 指定，日期表达固定为“最近7天”；不会根据问题自行分类。
Live 命令在有效输入后会访问模型 API，默认测试不会执行真实请求。

## 测试与限制

```powershell
python -m unittest discover -s tests -p "test_intent*.py" -v
python -m pytest -q
```

完整回归中，历史数据质量和 SQL 工具测试需要本地五张 CSV 与 SQLite 数据库；
不要为了测试重新生成或覆盖已有数据。意图系统专项测试使用 Mock/Fake 隔离外部服务。

本阶段不包含工具执行、结果解释、任意自然语言日期、自主规划、多工具循环或 Web UI。
Provider 安全异常保留稳定错误码，通信失败不冒充业务上的“不支持”。
