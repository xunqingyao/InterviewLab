from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, field_validator

from domain.enums import (
    EvidenceStatus,
    HintLevel,
    InterviewerAction,
    MessageKind,
)
from domain.text import locate_quote, normalize_text


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TextSpan(StrictModel):
    message_id: str
    start_char: StrictInt | None = None
    end_char: StrictInt | None = None
    quote: str = Field(min_length=1, max_length=2000)


class QuestionProposal(StrictModel):
    action: InterviewerAction
    reason: str = Field(default="", max_length=80)
    claim_id: str | None = None
    probe_key: str | None = None
    topic_id: str | None = None
    skill_ids: list[str] = Field(default_factory=list, max_length=8)
    question_text: str | None = Field(default=None, max_length=4000)
    parent_turn_id: str | None = None
    source_gap_ids: list[str] = Field(default_factory=list, max_length=12)
    rubric_template_id: str | None = None
    difficulty: str | None = None
    decision_summary: str = Field(min_length=1, max_length=500)

    @field_validator("action", mode="before")
    @classmethod
    def coerce_action(cls, value):
        return value if isinstance(value, InterviewerAction) else InterviewerAction(value)

    @field_validator("question_text")
    @classmethod
    def normalize_question(cls, value: str | None) -> str | None:
        return normalize_text(value) if value is not None else None

    def validate_semantics(self) -> None:
        if self.action is InterviewerAction.END:
            if any(
                (
                    self.question_text,
                    self.claim_id,
                    self.probe_key,
                    self.topic_id,
                    self.skill_ids,
                    self.parent_turn_id,
                    self.source_gap_ids,
                    self.rubric_template_id,
                )
            ):
                raise ValueError("END cannot include a question or direction binding")
            return
        if not all(
            (self.question_text, self.topic_id, self.claim_id, self.probe_key, self.rubric_template_id)
        ):
            raise ValueError("questions require text, claim_id, probe_key, topic_id and rubric_template_id")
        if len(self.skill_ids) != 1 or self.topic_id != self.skill_ids[0]:
            raise ValueError("questions must target exactly one requirement matching topic_id")
        if self.action is InterviewerAction.FOLLOW_UP and not self.parent_turn_id:
            raise ValueError("FOLLOW_UP requires parent_turn_id")
        if self.action is InterviewerAction.ASK and self.parent_turn_id:
            raise ValueError("ASK cannot have a parent question")


class FollowUpDecision(StrictModel):
    action: Literal["FOLLOW_UP"]
    question_text: str = Field(min_length=1, max_length=4000)
    reason: str = Field(default="", max_length=80)
    decision_summary: str = Field(min_length=1, max_length=500)


class AskDecision(StrictModel):
    action: Literal["ASK"]
    claim_id: str
    probe_key: str
    requirement_id: str
    question_text: str = Field(min_length=1, max_length=4000)
    reason: str = Field(default="", max_length=80)
    decision_summary: str = Field(min_length=1, max_length=500)


class EndDecision(StrictModel):
    action: Literal["END"]
    reason: str = Field(default="", max_length=80)
    decision_summary: str = Field(min_length=1, max_length=500)


class QuestionDecision(
    RootModel[
        Annotated[
            FollowUpDecision | AskDecision | EndDecision,
            Field(discriminator="action"),
        ]
    ]
):
    """The model proposes content; the server owns FOLLOW_UP direction and policy IDs."""


class DimensionAssessment(StrictModel):
    skill_id: str
    level: StrictInt | None = None
    evidence_status: EvidenceStatus
    answer_evidence: list[TextSpan] = Field(default_factory=list, max_length=12)
    reference_chunk_ids: list[str] = Field(default_factory=list, max_length=8)
    demonstrated: str = Field(default="", max_length=2000)
    missing: str = Field(default="", max_length=2000)
    hint_level: HintLevel = HintLevel.NONE

    @field_validator("evidence_status", mode="before")
    @classmethod
    def coerce_evidence_status(cls, value):
        return value if isinstance(value, EvidenceStatus) else EvidenceStatus(value)

    @field_validator("hint_level", mode="before")
    @classmethod
    def coerce_hint_level(cls, value):
        return value if isinstance(value, HintLevel) else HintLevel(value)

    @field_validator("level")
    @classmethod
    def level_range(cls, value: int | None) -> int | None:
        if value is not None and value not in {0, 1, 2, 3}:
            raise ValueError("level must be 0-3 or null")
        return value


class VerificationGap(StrictModel):
    skill_id: str
    description: str = Field(min_length=1, max_length=1000)
    priority: str = Field(default="MEDIUM")


class AssessmentResult(StrictModel):
    turn_id: str
    rubric_snapshot_id: str
    status: str
    dimensions: list[DimensionAssessment] = Field(default_factory=list, max_length=16)
    verification_gaps: list[VerificationGap] = Field(default_factory=list, max_length=16)


class ResumeClaim(StrictModel):
    id: str = Field(min_length=1, max_length=32)
    project: str = Field(default="", max_length=200)
    kind: str = Field(default="duty", max_length=32)
    text: str = Field(min_length=8, max_length=240)
    probe_hints: list[str] = Field(default_factory=list, max_length=5)


class ClaimsExtraction(StrictModel):
    claims: list[ResumeClaim] = Field(default_factory=list, max_length=16)


class ExtractedRequirement(StrictModel):
    text: str = Field(min_length=2, max_length=600)
    priority: Literal["must", "nice"] = "must"


class RequirementsExtraction(StrictModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list, max_length=16)


class TrainingTarget(StrictModel):
    requirement_id: str
    claim_id: str | None = None
    exercise_hint: str = Field(min_length=2, max_length=200)
    retest_hint: str = Field(min_length=2, max_length=200)


class CoachProposal(StrictModel):
    targets: list[TrainingTarget] = Field(min_length=1, max_length=3)
    rationale: str = Field(min_length=1, max_length=2000)


class SubmitAnswerCommand(StrictModel):
    turn_id: str
    text: str = Field(min_length=1, max_length=6000)
    expected_state_version: StrictInt

    @field_validator("text")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        return normalize_text(value)


class ErrorBody(StrictModel):
    code: str
    message: str
    request_id: str
    retryable: bool = False
    details: dict = Field(default_factory=dict)


def bind_citation(span: TextSpan, message_kind: MessageKind, normalized: str) -> TextSpan:
    if message_kind is not MessageKind.ANSWER:
        raise ValueError("citations must point at answer messages")
    located = locate_quote(normalized, span.quote)
    if located is None:
        raise ValueError("quote is missing or ambiguous in immutable answer text")
    start, end = located
    return TextSpan(message_id=span.message_id, start_char=start, end_char=end, quote=span.quote)
