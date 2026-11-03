from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def uuid_pk() -> mapped_column:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ACTIVE")
    is_dev: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AuthSession(Base, TimestampMixin):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Resume(Base, TimestampMixin):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    file_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    versions: Mapped[list[ResumeVersion]] = relationship(back_populates="resume")


class ResumeVersion(Base, TimestampMixin):
    __tablename__ = "resume_versions"
    __table_args__ = (UniqueConstraint("resume_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume: Mapped[Resume] = relationship(back_populates="versions")


class JobProfile(Base, TimestampMixin):
    __tablename__ = "job_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    versions: Mapped[list[JobProfileVersion]] = relationship(back_populates="profile")


class JobProfileVersion(Base, TimestampMixin):
    __tablename__ = "job_profile_versions"
    __table_args__ = (UniqueConstraint("profile_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_profiles.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    requirements_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile: Mapped[JobProfile] = relationship(back_populates="versions")


class InterviewSession(Base, TimestampMixin):
    __tablename__ = "interview_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    resume_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resume_versions.id"), nullable=False)
    job_profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_profile_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="CREATED")
    phase: Mapped[str] = mapped_column(Text, nullable=False, default="IDLE")
    mode: Mapped[str] = mapped_column(Text, nullable=False, default="STRICT")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    soft_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_turn_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    model_config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    knowledge_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    skill_catalog_version: Mapped[str] = mapped_column(Text, nullable=False, default="skills_v1")


class InterviewPlan(Base, TimestampMixin):
    __tablename__ = "interview_plans"
    __table_args__ = (UniqueConstraint("session_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    topics_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    skill_catalog_version: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class SkillCatalog(Base, TimestampMixin):
    __tablename__ = "skill_catalogs"
    __table_args__ = (UniqueConstraint("catalog_key", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    catalog_key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    skills_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="APPROVED")


class QuestionTemplate(Base, TimestampMixin):
    __tablename__ = "question_templates"
    __table_args__ = (UniqueConstraint("template_key", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    skill_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_template_id: Mapped[str] = mapped_column(Text, nullable=False)
    topic_id: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="APPROVED")
    variant_group: Mapped[str | None] = mapped_column(Text, nullable=True)


class RubricTemplate(Base, TimestampMixin):
    __tablename__ = "rubric_templates"
    __table_args__ = (UniqueConstraint("template_key", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    skill_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    criteria_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="APPROVED")


class RubricSnapshot(Base, TimestampMixin):
    __tablename__ = "rubric_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    template_id: Mapped[str] = mapped_column(Text, nullable=False)
    question_hash: Mapped[str] = mapped_column(Text, nullable=False)
    criteria_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reference_versions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, default="v1")


class InterviewTurn(Base, TimestampMixin):
    __tablename__ = "interview_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_no"),
        CheckConstraint("turn_no > 0", name="ck_turn_no_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    turn_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False, default="ASK", server_default="ASK")
    claim_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    probe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("interview_turns.id"), nullable=True)
    rubric_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rubric_snapshots.id"), nullable=True)
    skill_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    difficulty: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_mode: Mapped[str] = mapped_column(Text, nullable=False, default="SEEDED")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    followup_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("id", "session_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_turns.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(Text, nullable=False)


class Assessment(Base, TimestampMixin):
    __tablename__ = "assessments"
    __table_args__ = (UniqueConstraint("turn_id", "revision"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_turns.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    rubric_snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubric_snapshots.id"), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model_config_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessments.id"), nullable=True)


class AssessmentEvidence(Base, TimestampMixin):
    __tablename__ = "assessment_evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessments.id"), nullable=False)
    dimension_id: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)


class VerificationGapRow(Base, TimestampMixin):
    __tablename__ = "verification_gaps"

    id: Mapped[uuid.UUID] = uuid_pk()
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessments.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    skill_id: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    scope_topic_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="OPEN")


class Report(Base, TimestampMixin):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("session_id", "revision"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cutoff_turn_no: Mapped[int] = mapped_column(Integer, nullable=False)
    assessment_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    coverage_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    body_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewRequest(Base, TimestampMixin):
    __tablename__ = "review_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessments.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="QUEUED")
    resolved_assessment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessments.id"), nullable=True)


class SkillObservation(Base, TimestampMixin):
    __tablename__ = "skill_observations"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    skill_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessments.id"), nullable=False)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hint_level: Mapped[str] = mapped_column(Text, nullable=False, default="NONE")
    evidence_strength: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedupe_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("interview_sessions.id"), nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="QUEUED")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentInputSnapshot(Base, TimestampMixin):
    __tablename__ = "agent_input_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    cutoff: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="RUNNING")
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False, default="v1")
    model_config_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolCall(Base, TimestampMixin):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class LlmCall(Base, TimestampMixin):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_estimate: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    usage_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BudgetAccount(Base, TimestampMixin):
    __tablename__ = "budget_accounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    limit_amount: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    reserved_amount: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    spent_amount: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)


class LlmSlot(Base, TimestampMixin):
    __tablename__ = "llm_slots"
    __table_args__ = (UniqueConstraint("scope", "slot_no"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    slot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="FREE")


class SessionEvent(Base):
    __tablename__ = "session_events"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    public_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IdempotencyRecord(Base, TimestampMixin):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("owner_id", "route", "key_hash"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_ref: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="GLOBAL")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    rights_note: Mapped[str] = mapped_column(Text, nullable=False, default="self-authored")


class KnowledgeVersion(Base, TimestampMixin):
    __tablename__ = "knowledge_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="APPROVED")


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_versions.id"), nullable=False)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    skill_tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tech_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tsv: Mapped[object | None] = mapped_column(TSVECTOR, nullable=True)


class KnowledgeEmbedding(Base, TimestampMixin):
    __tablename__ = "knowledge_embeddings"

    id: Mapped[uuid.UUID] = uuid_pk()
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_chunks.id"), nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list] = mapped_column(Vector(32), nullable=False)


class KnowledgeSnapshot(Base, TimestampMixin):
    __tablename__ = "knowledge_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    version_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")


class TrainingPlan(Base, TimestampMixin):
    __tablename__ = "training_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    skill_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="READY")


class TrainingItem(Base, TimestampMixin):
    __tablename__ = "training_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("training_plans.id"), nullable=False)
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_template_id: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rubric_snapshots.id"), nullable=True)
    hint_templates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    variant_template_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="EXERCISE")


class ExerciseSubmission(Base, TimestampMixin):
    __tablename__ = "exercise_submissions"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("training_items.id"), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    hint_level: Mapped[str] = mapped_column(Text, nullable=False, default="NONE")
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class AudioDraft(Base, TimestampMixin):
    __tablename__ = "audio_drafts"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id"), nullable=False)
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_turns.id"), nullable=False)
    file_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    transcript_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_edited: Mapped[str | None] = mapped_column(Text, nullable=True)
