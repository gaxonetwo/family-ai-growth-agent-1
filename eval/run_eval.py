"""Live evaluation using existing read-only tools; no canned passing reports."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean

from src.agent.agent_controller import AgentController
from src.agent.intent.deepseek_provider import DeepSeekProvider
from src.agent.intent.provider import IntentProviderError
from src.agent.result_explainer import _validate_report, resolve_path
from src.agent.report_semantics import report_context, validate_semantics
from src.agent.tracing import JsonlTracer

ROOT = Path(__file__).resolve().parent


def grade(case, result):
    observations = result.get('observations', [])
    decisions = result.get('decisions', [])
    if case['category'] == 'clarification':
        passed = (not observations and result.get('status') == 'needs_clarification'
                  and bool(result.get('clarification_question')))
        return passed, '需要中文澄清且零工具调用'
    if case['category'] == 'refusal':
        passed = (not observations and result.get('status') == 'unsupported' and
                  any(d.get('action', {}).get('action') == 'refuse' and
                      d['action'].get('reason') for d in decisions))
        return passed, '需要明确refuse动作、原因且零工具调用'
    if result.get('status') != 'completed' or len(observations) != 1:
        return False, '未完成或工具调用次数不等于1'
    observation = observations[0]
    if observation['tool_name'] != case['tool'] or not observation['result'].get('ok'):
        return False, '工具选择错误或真实工具执行失败'
    fields = ('cohort_start', 'cohort_end') if case['tool'] in (
        'analyze_retention', 'analyze_activation_funnel') else ('start_date', 'end_date')
    if [observation['arguments'].get(k) for k in fields] != [case['start'], case['end']]:
        return False, '日期参数不符合固定参考日口径'
    explanations = result.get('explanations', [])
    if len(explanations) != 1 or explanations[0]['result'].get('status') != 'success':
        return False, '报告未成功通过本地证据和语义校验'
    try:
        report = explanations[0]['result']['report']
        data = observation['result']['data']
        _validate_report(report, data)
        validate_semantics(report, data, report_context(case['tool'], data), resolve_path)
    except (ValueError, TypeError, KeyError, IndexError):
        return False, '独立复核报告证据失败'
    return True, '工具、日期、真实执行和报告证据均通过'


def evaluate(cases, agent):
    rows = []
    for case in cases:
        result = agent.run(case['prompt'], reference_date='2026-11-13')
        passed, reason = grade(case, result)
        rows.append(dict(id=case['id'], category=case['category'], passed=passed,
                         reason=reason, session_id=result.get('session_id'),
                         decision_rounds=len(result.get('decisions', [])), result=result))
        print(case['id'], 'PASS' if passed else 'FAIL', result.get('session_id'), flush=True)
    return rows


def save_report(rows, *, blocked=None, model=None, selected_ids=None):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = dict(run_id=stamp, mode='live', model=model, reference_date='2026-11-13',
                  blocked=blocked, executed=len(rows), cases=rows, selected_ids=selected_ids,
                  planned_count=len(selected_ids) if selected_ids else 24)
    groups = {}
    for category in ('answerable', 'clarification', 'refusal'):
        group = [row for row in rows if row['category'] == category]
        groups[category] = dict(passed=sum(r['passed'] for r in group), executed=len(group),
                               pass_rate=sum(r['passed'] for r in group)/len(group) if group else None)
    output['groups'] = groups
    output['pass_rate'] = sum(r['passed'] for r in rows)/len(rows) if rows else None
    output['average_decision_rounds'] = mean(r['decision_rounds'] for r in rows) if rows else None
    history = ROOT / 'results'
    history.mkdir(exist_ok=True)
    encoded = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)
    (history / f'{stamp}.json').write_text(encoded, encoding='utf-8')
    (ROOT / 'results.json').write_text(encoded, encoding='utf-8')
    rate = f"{output['pass_rate']:.2%}" if rows else '未运行，不能计算'
    lines = ['# Agent Eval实际运行报告', '', f'运行ID：{stamp}', '模式：真实DeepSeek + 正式只读工具',
             f'模型：{model or "未初始化"}', f'已执行：{len(rows)}/{output["planned_count"]}', f'总通过率：{rate}',
             f'平均决策轮次：{output["average_decision_rounds"]}', '',
             '| 类别 | 通过/执行 | 通过率 |', '|---|---|---|']
    for name, group in groups.items():
        value = f"{group['pass_rate']:.2%}" if group['executed'] else 'N/A'
        lines.append(f"| {name} | {group['passed']}/{group['executed']} | {value} |")
    lines += ['', '## 失败用例与Trace', '']
    if blocked:
        lines.append(f'运行阻塞：{blocked}。这不是用例失败或0%通过率。')
    for row in rows:
        if not row['passed']:
            lines.append(f"- {row['id']}：{row['reason']}；session_id=`{row['session_id']}`；状态={row['result']['status']}")
    lines += ['', '完整原始结果：results.json；历史运行：results/；本地Trace：reports/agent_trace.jsonl。',
              '此评估检验固定任务和受限报告协议，不代表通用准确率或安全保证。',
              '失败保留在报告中；用例失败不会让评估进程以测试失败退出。']
    (ROOT / 'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description='Run 24 live Agent evaluations (API usage incurred)')
    parser.add_argument('--case-ids', nargs='+', help='只运行指定用例ID，评分规则保持不变')
    args = parser.parse_args()
    cases = json.loads((ROOT / 'cases.json').read_text(encoding='utf-8'))
    if args.case_ids:
        unknown = set(args.case_ids) - {c['id'] for c in cases}
        if unknown:
            parser.error('未知用例ID：' + ', '.join(sorted(unknown)))
        cases = [c for c in cases if c['id'] in args.case_ids]
    selected_ids = [c['id'] for c in cases]
    try:
        provider = DeepSeekProvider()
    except IntentProviderError as exc:
        save_report([], blocked=exc.code, selected_ids=selected_ids)
        print('评估未执行：', exc.code)
        return 2
    agent = AgentController(provider, explain_results=True, tracer=JsonlTracer())
    rows = evaluate(cases, agent)
    save_report(rows, model=provider.config.model, selected_ids=selected_ids)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
