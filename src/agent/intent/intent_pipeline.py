"""Integrate intent processing without invoking business tool callables."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import re
from types import MappingProxyType
from typing import Any, Mapping

from src.agent.intent.date_resolver import DateResolver
from src.agent.intent.intent_parser import IntentParser, IntentParserError
from src.agent.intent.intent_validator import IntentValidator
from src.agent.intent.models import IntentStatus
from src.agent.intent.provider import IntentModelProvider
from src.agent.intent.tool_matcher import ToolMatcher
from src.agent.tool_registry import ToolRegistry, create_default_tool_registry


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class IntentPipelineResult:
    """Immutable stage snapshots; None explicitly means a stage was not run.

    Invalid non-string caller inputs are retained in ``user_input`` as supplied.
    Stage snapshots use the existing results' public ``to_dict`` contracts.
    """

    status: IntentStatus
    user_input: Any
    draft: Mapping[str, Any] | None = None
    tool_match: Mapping[str, Any] | None = None
    date_resolution: Mapping[str, Any] | None = None
    validation: Mapping[str, Any] | None = None
    clarification_question: str | None = None
    error: Mapping[str, Any] | None = None
    clarification_answer: str | None = None
    clarification_count: int = 0
    previous_result: Mapping[str, Any] | None = None

    def __post_init__(self):
        for name in ("user_input", "draft", "tool_match", "date_resolution", "validation", "error", "previous_result"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            **{name: _thaw(getattr(self, name)) for name in (
                "user_input", "draft", "tool_match", "date_resolution",
                "validation", "clarification_question", "error",
                "clarification_answer", "clarification_count", "previous_result",
            )},
        }


class IntentPipeline:
    """One parser call followed by local matching, date resolution and validation.

    Configuration and Provider exceptions propagate unchanged. Only expected
    Parser input/output errors are converted to structured task results.
    """

    def __init__(
        self,
        provider: IntentModelProvider,
        registry: ToolRegistry | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        date_resolver: DateResolver | None = None,
    ):
        registry = registry if registry is not None else create_default_tool_registry()
        self.parser = IntentParser(provider, registry)
        self.matcher = ToolMatcher(registry)
        self.date_resolver = date_resolver if date_resolver is not None else DateResolver()
        self.validator = IntentValidator(registry, environ=environ)

    def process(self, user_input: str, reference_date: str | date | None = None) -> IntentPipelineResult:
        return self._process(user_input, reference_date)

    def continue_with_clarification(self, previous_result, clarification_answer):
        """Reprocess one actual answer, preserving the original task and snapshots.

        Results are immutable: callers must use the returned result for the next
        operation. No session state or automatic follow-up loop is maintained.
        """
        if not isinstance(previous_result, IntentPipelineResult):
            raise TypeError("previous_result must be an IntentPipelineResult.")
        if previous_result.status is not IntentStatus.NEEDS_CLARIFICATION:
            raise ValueError("Only needs_clarification results can be continued.")
        if previous_result.clarification_count != 0:
            raise ValueError("Only one clarification round is supported.")
        if not isinstance(clarification_answer, str) or not clarification_answer.strip():
            raise ValueError("clarification_answer must be a non-empty string.")
        original = previous_result.user_input
        if not isinstance(original, str):
            raise ValueError("The original request must be a string.")
        # Only actual user text is supplied. System questions are not evidence.
        combined = original + "\n" + clarification_answer
        reference = None
        if previous_result.date_resolution:
            reference = previous_result.date_resolution.get("reference_date")
        result = self._process(combined, reference, previous_result)
        if re.search(r"不是|不对|不同意|不要|并非|否认", clarification_answer):
            question = "你否定了此前的解释，请明确希望采用的参数含义或分析方式。"
            validation = _thaw(result.validation) if result.validation else None
            if validation is not None:
                validation.update(status=IntentStatus.NEEDS_CLARIFICATION.value,
                                  clarification_question=question)
            result = replace(result, status=IntentStatus.NEEDS_CLARIFICATION,
                             validation=validation, clarification_question=question,
                             error={"stage": "clarification", "code": "explicit_denial",
                                    "message": "User denied the previous interpretation."})
        if result.validation:
            validation = _thaw(result.validation)
            for record in validation["argument_provenance"].values():
                evidence = record.get("evidence")
                record["evidence_origin"] = (
                    "clarification_answer" if evidence and evidence in clarification_answer
                    else "original_request" if evidence and evidence in original
                    else None
                )
            result = replace(result, validation=validation)
        return replace(result, user_input=original,
                       clarification_answer=clarification_answer, clarification_count=1,
                       previous_result=previous_result.to_dict())

    def _process(self, user_input, reference_date=None, previous=None):
        # Context is local to this invocation; the original string is never trimmed.
        context = {"user_input": user_input}
        try:
            draft = self.parser.parse(user_input)
        except IntentParserError as exc:
            return IntentPipelineResult(
                status=exc.status, **context,
                error={"stage": "parser", "code": exc.status.value, "message": str(exc)},
            )
        context["draft"] = draft.to_dict()

        changes = []
        if previous is not None and previous.draft is not None:
            old = _thaw(previous.draft)
            if sorted(old["intent_candidates"]) != sorted(c.value for c in draft.intent_candidates):
                changes.append({"parameter_name": "intent_candidates", "before": old["intent_candidates"],
                                "after": [c.value for c in draft.intent_candidates]})
            arguments = dict(old["arguments"])
            for name, value in draft.arguments.items():
                if name in arguments and arguments[name] != value:
                    changes.append({"parameter_name": name, "before": arguments[name], "after": value})
                arguments[name] = value
            evidence = {**old["argument_evidence"], **draft.argument_evidence}
            if old["date_expression"] and draft.date_expression and old["date_expression"] != draft.date_expression:
                changes.append({"parameter_name": "date_expression", "before": old["date_expression"], "after": draft.date_expression})
            draft = replace(draft, arguments=arguments, argument_evidence=evidence,
                            date_expression=draft.date_expression or old["date_expression"])
            context["draft"] = draft.to_dict()

        match = self.matcher.match(draft)
        context["tool_match"] = match.to_dict()
        if match.status is not IntentStatus.READY:
            question = None
            if match.status is IntentStatus.NEEDS_CLARIFICATION:
                question = (
                    "你希望优先分析哪一项？候选工具：" + "、".join(match.candidate_tools) + "。"
                    if match.candidate_tools else "你希望分析激活、留存、活跃还是用户反馈？"
                )
            return IntentPipelineResult(
                status=match.status, **context, clarification_question=question,
                error={"stage": "tool_match", "code": match.status.value, "message": match.reason},
            )

        dates = self.date_resolver.resolve(draft, match, reference_date=reference_date)
        context["date_resolution"] = dates.to_dict()
        if dates.status is not IntentStatus.READY:
            return IntentPipelineResult(
                status=dates.status, **context,
                clarification_question=(
                    "请提供可靠的参考日期，以便解析相对日期。"
                    if dates.error == "missing_reference_date"
                    else "请明确要分析的起始日期和结束日期（YYYY-MM-DD）。"
                ),
                error={"stage": "date_resolution", "code": dates.error or dates.status.value, "message": dates.reason},
            )

        validation = self.validator.validate(draft, match, dates, user_input=user_input)
        if changes:
            validation = replace(
                validation, status=IntentStatus.NEEDS_CLARIFICATION,
                conflict_details=validation.conflict_details + tuple(changes),
                clarification_question="补充回答改变了原始参数，请明确最终取值：" +
                    "；".join(f"{c['parameter_name']}：{c['before']} → {c['after']}" for c in changes) + "。",
            )
        return IntentPipelineResult(
            status=validation.status, **context, validation=validation.to_dict(),
            clarification_question=validation.clarification_question,
            error=(None if validation.status is IntentStatus.READY else {
                "stage": "validation", "code": validation.status.value,
                "message": validation.reason, "details": list(validation.validation_errors),
            }),
        )
