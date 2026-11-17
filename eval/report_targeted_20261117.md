# Agent Eval实际运行报告

运行ID：20261117T072226863480Z
模式：真实DeepSeek + 正式只读工具
模型：deepseek-flash
已执行：3/3
总通过率：66.67%
平均决策轮次：2

| 类别 | 通过/执行 | 通过率 |
|---|---|---|
| answerable | 2/3 | 66.67% |
| clarification | 0/0 | N/A |
| refusal | 0/0 | N/A |

## 失败用例与Trace

- answer_2：报告未成功通过本地证据和语义校验；session_id=`196da14dd69b4b43b4c618053498085d`；状态=completed

完整原始结果：results.json；历史运行：results/；本地Trace：reports/agent_trace.jsonl。
此评估检验固定任务和受限报告协议，不代表通用准确率或安全保证。
失败保留在报告中；用例失败不会让评估进程以测试失败退出。
