"""Terminal intent demo: offline by default, DeepSeek only with --live."""
from __future__ import annotations

import argparse
import json
import os

from src.agent.intent.intent_pipeline import IntentPipeline
from src.agent.intent.deepseek_provider import DeepSeekProvider
from src.agent.intent.intent_validator import IntentValidatorConfigurationError
from src.agent.intent.intent_parser import IntentParserConfigurationError
from src.agent.intent.mock_provider import MockIntentProvider
from src.agent.intent.models import IntentCandidate, IntentStatus, ProviderResponse
from src.agent.intent.provider import IntentProviderError
from src.agent.single_tool_execution import SingleToolExecution
from src.agent.tool_executor import ToolExecutor


_PROVIDER_ERROR_MESSAGES = {
    "missing_api_key": "缺少DEEPSEEK_API_KEY环境变量。",
    "missing_model": "缺少DEEPSEEK_MODEL环境变量。",
    "invalid_configuration": "DeepSeek配置无效，请检查模型、地址和请求参数。",
    "authentication_error": "鉴权失败，请检查API Key。",
    "rate_limit_error": "请求受到限流，请稍后重试。",
    "connection_error": "无法连接模型服务，请检查网络。",
    "request_timeout": "模型请求超时。",
    "invalid_json": "模型返回了非法JSON。",
    "response_truncated": "模型输出被截断，请检查输出Token限制。",
    "empty_response": "模型返回了空响应。",
    "api_error": "模型服务返回API错误。",
}


def display_provider_error(error, output_fn=None):
    """Use only local text and allowlisted codes, never transport details."""
    output_fn = print if output_fn is None else output_fn
    code = error.code if error.code in _PROVIDER_ERROR_MESSAGES else "api_error"
    output_fn("模型服务错误：" + _PROVIDER_ERROR_MESSAGES[code] + " 错误码：" + code)


def create_demo_provider(intent="activity_analysis", scenario="ready"):
    """Explicit canned responses, not keyword-based intent recognition."""
    def factory(prompt, user_input, call_number):
        arguments, evidence = {}, {"date_range": "最近7天"}
        if scenario != "ready":
            arguments = {"breakdown_by": "plan_type"}
            evidence["breakdown_by"] = (
                "用户当前套餐分组"
                if scenario == "clarification" and call_number > 1 else "按照套餐"
            )
        return ProviderResponse(dict(
            intent_candidates=[intent], arguments=arguments,
            argument_evidence=evidence, date_expression="最近7天",
            confidence=.95, reason="离线预设响应，不代表真实模型判断",
        ), "offline-demo-mock", "stop")
    return MockIntentProvider(response_factory=factory)


def display_result(result, output_fn=print):
    data = result.to_dict()
    output_fn("用户原始请求：" + str(data["user_input"]))
    if data["clarification_answer"] is not None:
        output_fn("补充回答：" + data["clarification_answer"])
    output_fn("业务意图：" + json.dumps((data["draft"] or {}).get("intent_candidates", []), ensure_ascii=False))
    output_fn("匹配工具：" + str((data["tool_match"] or {}).get("tool_name")))
    output_fn("日期参数：" + json.dumps((data["date_resolution"] or {}).get("date_arguments", {}), ensure_ascii=False))
    output_fn("最终业务参数：" + json.dumps((data["validation"] or {}).get("arguments", {}), ensure_ascii=False))
    output_fn("参数验证状态：" + result.status.value)
    output_fn("是否需要确认：" + ("是" if result.status is IntentStatus.NEEDS_CLARIFICATION else "否"))
    if result.status is IntentStatus.NEEDS_CLARIFICATION:
        output_fn("澄清问题：" + (result.clarification_question or "请补充业务需求。"))
    if data["error"]:
        output_fn("未完成原因：" + str(data["error"].get("message", "验证未通过")))


def run_session(pipeline, *, input_fn=None, output_fn=None, reference_date=None):
    """One task per invocation. Always replace current with the latest result."""
    current = None
    input_fn = input if input_fn is None else input_fn
    output_fn = print if output_fn is None else output_fn
    try:
        text = input_fn("请输入业务问题（exit/quit退出）：")
        if text.strip().lower() in ("exit", "quit"):
            output_fn("已退出。")
            return None
        if not text.strip():
            output_fn("输入不能为空，本次任务结束。")
            return None
        current = pipeline.process(text, reference_date=reference_date)
        display_result(current, output_fn)
        if current.status is IntentStatus.NEEDS_CLARIFICATION and current.clarification_count == 0:
            answer = input_fn("请补充回答（exit/quit退出）：")
            if answer.strip().lower() in ("exit", "quit"):
                output_fn("已退出，保留待确认状态。")
                return current
            if not answer.strip():
                output_fn("补充回答不能为空，本次任务结束。")
                return current
            current = pipeline.continue_with_clarification(current, answer)
            display_result(current, output_fn)
            if current.status is IntentStatus.NEEDS_CLARIFICATION:
                output_fn("一次澄清已用完，任务仍待确认，本次会话结束。")
        return current
    except IntentProviderError as exc:
        # Show only the stable code, never arbitrary transport/request details.
        display_provider_error(exc, output_fn)
        return current
    except (EOFError, KeyboardInterrupt):
        output_fn("输入已结束。")
        return current


def main(argv=None):
    parser = argparse.ArgumentParser(description="意图识别演示：--live使用DeepSeek，--execute显式执行单个只读工具")
    parser.add_argument("--execute", action="store_true", help="仅对最终验证通过的任务执行一次业务工具")
    parser.add_argument("--live", action="store_true", help="显式启用DeepSeek；输入有效业务问题后才发送请求")
    parser.add_argument("--intent", choices=[i.value for i in IntentCandidate], default="activity_analysis")
    parser.add_argument("--scenario", choices=["ready", "clarification", "unresolved"], default="ready")
    parser.add_argument("--reference-date", help="可选参考日期 YYYY-MM-DD；默认读取项目配置")
    parser.add_argument("--confidence-threshold", help="覆盖 INTENT_CONFIDENCE_THRESHOLD")
    args = parser.parse_args(argv)
    if args.live:
        print("Live模式：有效输入将发送给DeepSeek识别意图；--intent和--scenario不生效。")
    else:
        print("离线Mock意图：意图由--intent预设，日期表达固定为最近7天；模型不访问网络。")
        print("输入示例：分析最近7天的活跃情况。澄清场景：按照套餐分析最近7天活跃情况。")
    print("执行模式：验证通过后将调用真实只读业务工具。" if args.execute else "仅识别与验证，尚未执行业务工具（需--execute）。")
    environ = dict(os.environ)
    if args.confidence_threshold is not None:
        environ["INTENT_CONFIDENCE_THRESHOLD"] = args.confidence_threshold
    try:
        provider = DeepSeekProvider(environ=environ) if args.live else create_demo_provider(args.intent, args.scenario)
        pipeline = IntentPipeline(provider, environ=environ)
        current = run_session(pipeline, reference_date=args.reference_date)
        if args.execute and current is not None:
            bridge = SingleToolExecution(ToolExecutor(pipeline.matcher.registry))
            execution = bridge.execute(current)
            print("工具执行状态：" + execution["execution_status"])
            if execution["tool_result"] is not None:
                print("业务工具返回：" + json.dumps(execution["tool_result"], ensure_ascii=False, indent=2))
            elif execution["error"]:
                print("未执行原因：" + execution["error"]["message"])
            if not execution["ok"]:
                return 1
    except IntentProviderError as exc:
        display_provider_error(exc)
        return 2
    except (IntentValidatorConfigurationError, IntentParserConfigurationError):
        print("配置无效：请检查Registry及INTENT_CONFIDENCE_THRESHOLD（0至1），也可使用--confidence-threshold。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
