# Agent Eval实际运行报告

运行ID：20261117T073519331256Z
模式：真实DeepSeek + 正式只读工具
模型：deepseek-flash
已执行：24/24
总通过率：91.67%
平均决策轮次：1.3333333333333333

| 类别 | 通过/执行 | 通过率 |
|---|---|---|
| answerable | 6/8 | 75.00% |
| clarification | 8/8 | 100.00% |
| refusal | 8/8 | 100.00% |

## 失败用例与Trace

- answer_1：报告未成功通过本地证据和语义校验；session_id=`c9298eb6bcf94e4587dda3119418210a`；状态=completed
- answer_6：报告未成功通过本地证据和语义校验；session_id=`945f67fe8fb845dfb74cdaff109790c6`；状态=completed

完整原始结果：results.json；历史运行：results/；本地Trace：reports/agent_trace.jsonl。
此评估检验固定任务和受限报告协议，不代表通用准确率或安全保证。
失败保留在报告中；用例失败不会让评估进程以测试失败退出。
