from __future__ import annotations

from domain.enums import AssessmentStatus, EvidenceStatus, HintLevel, MessageKind
from domain.errors import InvalidAgentOutput
from domain.schemas import AssessmentResult, DimensionAssessment, TextSpan, bind_citation


def bind_answer_quote(span: TextSpan, *, answer_message_id: str, answer_text: str) -> TextSpan:
    """Backend locates quote in the frozen answer. Model message_id is ignored."""
    forced = TextSpan(message_id=answer_message_id, quote=span.quote)
    return bind_citation(forced, MessageKind.ANSWER, answer_text)


def sanitize_assessment_result(
    result: AssessmentResult,
    *,
    answer_message_id: str,
    answer_text: str,
    answer_sources: dict[str, str] | None = None,
    expected_turn_id: str | None = None,
    expected_rubric_id: str | None = None,
    allowed_skill_ids: list[str] | None = None,
    allowed_reference_ids: list[str] | None = None,
    expected_hint_level: str | None = None,
) -> AssessmentResult:
    """Bind evidence to server-authorized answers. Invalid numeric evidence becomes null."""
    if expected_turn_id is not None and result.turn_id != expected_turn_id:
        raise InvalidAgentOutput("assessment turn_id does not match the evaluated turn")
    if expected_rubric_id is not None and result.rubric_snapshot_id != expected_rubric_id:
        raise InvalidAgentOutput("assessment rubric_snapshot_id does not match the frozen rubric")
    dimension_ids = [dim.skill_id for dim in result.dimensions]
    if len(dimension_ids) != len(set(dimension_ids)):
        raise InvalidAgentOutput("assessment contains duplicate requirement dimensions")
    if allowed_skill_ids is not None and any(
        rid not in allowed_skill_ids
        for rid in dimension_ids + [gap.skill_id for gap in result.verification_gaps]
    ):
        raise InvalidAgentOutput("assessment contains a requirement outside this question")
    sources = answer_sources if answer_sources is not None else {answer_message_id: answer_text}
    cleaned: list[DimensionAssessment] = []
    for dim in result.dimensions:
        evidence: list[TextSpan] = []
        for span in dim.answer_evidence:
            raw = span if isinstance(span, TextSpan) else TextSpan.model_validate(span)
            if raw.message_id not in sources:
                continue
            try:
                chosen = bind_answer_quote(
                    raw, answer_message_id=raw.message_id, answer_text=sources[raw.message_id]
                )
                if chosen not in evidence:
                    evidence.append(chosen)
            except ValueError:
                continue
        level = dim.level
        status = dim.evidence_status
        if level is not None and not evidence:
            level = None
            status = EvidenceStatus.INSUFFICIENT_EVIDENCE
        if status in {
            EvidenceStatus.INSUFFICIENT_EVIDENCE,
            EvidenceStatus.NOT_APPLICABLE,
            EvidenceStatus.REFERENCE_CONFLICT,
        }:
            level = None
        references = list(dict.fromkeys(dim.reference_chunk_ids))
        if allowed_reference_ids is not None:
            references = [ref for ref in references if ref in allowed_reference_ids]
        if status is EvidenceStatus.REFERENCE_CONFLICT and not references:
            status = EvidenceStatus.INSUFFICIENT_EVIDENCE
        if level is None and status is EvidenceStatus.DEMONSTRATED:
            status = EvidenceStatus.INSUFFICIENT_EVIDENCE
        cleaned.append(
            dim.model_copy(
                update={
                    "level": level,
                    "evidence_status": status,
                    "answer_evidence": evidence,
                    "reference_chunk_ids": references,
                    "hint_level": HintLevel(expected_hint_level)
                    if expected_hint_level is not None
                    else dim.hint_level,
                }
            )
        )
    if not cleaned:
        overall = AssessmentStatus.INSUFFICIENT_EVIDENCE.value
    elif any(d.level is not None for d in cleaned):
        overall = AssessmentStatus.ASSESSED.value
    else:
        overall = AssessmentStatus.INSUFFICIENT_EVIDENCE.value
    return result.model_copy(
        update={
            "dimensions": cleaned,
            "status": overall,
        }
    )
