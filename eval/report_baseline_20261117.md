# 原始24条真实DeepSeek Eval基准

- 运行ID：20261117T030612147236Z
- 模型：deepseek-flash
- 参考日期：2026-11-13
- 已执行：24/24
- 总通过率：21/24，87.50%
- 平均决策轮次：1.3333333333333333

| 类别 | 通过/执行 | 通过率 |
|---|---|---|
| 正常可答 | 5/8 | 62.50% |
| 需要澄清 | 8/8 | 100.00% |
| 应当拒答 | 8/8 | 100.00% |

| 失败用例 | session_id | 原始报告错误 |
|---|---|---|
| answer_2 | bc7f3a49f5794fb3b0d54202098538f2 | unverified_semantic_statement |
| answer_4 | 431d7c70a41b4eef9895c3904e213e7e | invalid_report_or_evidence |
| answer_6 | 494fae3799a948deb54225432ad43cee | unverified_semantic_statement |

根据历史结构化结果 eval/results/20261117T030612147236Z.json 整理。
这是原始基准，不是可观测性改进后的重新评估。
