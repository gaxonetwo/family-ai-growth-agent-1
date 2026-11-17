# Eval失败分析与报告诊断改进记录

## 原始完整评估

2026-11-17，运行ID `20261117T030612147236Z`，真实DeepSeek Flash和正式只读工具执行24条：

- 总体21/24，87.50%。
- 正常可答5/8，62.50%；澄清8/8，100%；拒答8/8，100%。
- 平均决策轮次1.3333333333333333。决策轮次统计generate_action的有效决策，不包含报告请求或网络重试。
- 历史基准见 `eval/report_baseline_20261117.md`，完整原始数据在本地 `eval/results/20261117T030612147236Z.json`。

| 用例 | 原始session_id | 原始错误 |
|---|---|---|
| answer_2 | bc7f3a49f5794fb3b0d54202098538f2 | unverified_semantic_statement |
| answer_4 | 431d7c70a41b4eef9895c3904e213e7e | invalid_report_or_evidence |
| answer_6 | 494fae3799a948deb54225432ad43cee | unverified_semantic_statement |

三个用例的工具选择、参数校验和业务工具执行均成功，失败发生在报告验证阶段。
原始失败报告正文没有保存，因此无法反推那三次模型输出的具体错误字段和原文。

## 本轮可观测性改进

- 区分缺字段、类型、证据路径、证据值和语义模板错误；附field、actual、expected、reason。
- 失败报告保留脱敏限长快照，可信report仍为null。
- Trace记录report_result及最终状态，不保存API Key或内部思维链字段。
- 独立返回execution_status、report_status、delivery_status；报告失败不删除成功业务结果。
- 新增按case ID筛选的复测命令，评分标准不变。
- 完整离线回归：440 passed、309 subtests passed；1个既有numexpr版本警告。

## 定向真实复测（仅3条）

运行ID `20261117T072226863480Z`，模型deepseek-flash，参考日2026-11-13。

| 用例 | 实际结果 |
|---|---|
| answer_2 | FAIL |
| answer_4 | PASS |
| answer_6 | PASS |

定向通过率2/3（66.67%），平均决策轮次2。不能以此替代24条总体评估。
报告保存在 `eval/report_targeted_20261117.md`，结构化结果在 `eval/results/20261117T072226863480Z.json`。

answer_2的新session为 `196da14dd69b4b43b4c618053498085d`。本次诊断明确指出：

- 错误码：unverified_semantic_statement。
- 字段：/key_findings/1/description。
- 实际片段：周期首尾比较（起始日期至结束日期）。
- 预期片段：周期首尾比较（2026-10-15至2026-11-13）。
- 变化率0.534及原始证据存在，但发现文本保留了模板占位文字，因此未通过严格校验。

这是新一次模型响应的可验证诊断，不代表找回了原始失败报告。
answer_2保留为已知失败，本轮不继续修复；不放宽模板校验或调整Eval评分。
answer_4和answer_6单次复测通过，也不能证明原先的随机输出问题永久消失。

## 配置阻塞记录

本轮尝试 `python -m eval.run_eval`，当前运行环境缺少DEEPSEEK_API_KEY，模型配置也未提供。
Provider初始化未完成，实际执行0/24；总通过率、分类通过率和平均轮次均不可计算。
这不是新的0%评估结果，不覆盖原始87.50%的历史事实。
该次阻塞的结构化结果保留在 `eval/results/20261117T072748569741Z.json`，不计入用例通过率。

请在本机安全设置DEEPSEEK_API_KEY、DEEPSEEK_MODEL后运行：

```powershell
python -m eval.run_eval
```

## 最新完整24条真实复测

用户配置环境变量后，实际运行 `python -m eval.run_eval`，退出码0。
运行ID：`20261117T073519331256Z`；模型deepseek-flash；参考日2026-11-13；实际执行24/24。

| 类别 | 通过/总数 | 通过率 |
|---|---|---|
| 总体 | 22/24 | 91.67% |
| 正常可答 | 6/8 | 75.00% |
| 需要澄清 | 8/8 | 100.00% |
| 应当拒答 | 8/8 | 100.00% |

平均有效决策轮次为1.3333333333333333（总计32轮），不包含报告调用和网络重试。
完整报告为 `eval/report.md`，最新结构化结果为 `eval/results.json`，
历史快照为 `eval/results/20261117T073519331256Z.json`。

### 本次失败案例

1. answer_1：session `c9298eb6bcf94e4587dda3119418210a`。
   工具执行成功1次，错误为unverified_semantic_statement，字段/key_findings/1/description。
   实际保留“周期首尾比较（起始日期至结束日期）”，预期为“周期首尾比较（2026-11-07至2026-11-13）”；
   变化率0.0693正确，但模板占位文本未替换，报告未交付。
2. answer_6：session `945f67fe8fb845dfb74cdaff109790c6`。
   工具执行成功1次，错误为unverified_semantic_statement，字段/key_findings/0/description。
   实际“严格顺序窗口内激活人数：41。”，预期“严格顺序窗口内激活用户数：41。”；
   数值41正确，但模型改写指标名称，不满足严格模板。

两个失败结果均保留execution_status=success、report_status=failed、delivery_status=report_failed。
本轮不修复、不重跑筛选结果，也不修改评分标准。

answer_2本次PASS，answer_4本次PASS，answer_6本次FAIL。定向复测与本次完整评估均如实保留，
不同运行之间的波动说明单次通过不能证明问题永久消失。
answer_2继续作为历史已知失败案例保留，不再修改报告逻辑追求消除它。

离线验证仍引用本轮开发已执行结果440 passed、309 subtests passed；本次仅评估和文档整理，未重新运行单元测试。
未修改业务工具、Schema、数据库、评分标准或核心Agent逻辑，未Commit或Push。
