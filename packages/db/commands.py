from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import with_parent

from db.models import (
    Assessment,
    AssessmentEvidence,
    AudioDraft,
    AuthSession,
    BudgetAccount,
    ExerciseSubmission,
    IdempotencyRecord,
    InterviewPlan,
    InterviewSession,
    InterviewTurn,
    Job,
    JobProfile,
    JobProfileVersion,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
    KnowledgeSnapshot,
    KnowledgeVersion,
    Message,
    QuestionTemplate,
    Report,
    Resume,
    ResumeVersion,
    ReviewRequest,
    RubricSnapshot,
    RubricTemplate,
    SessionEvent,
    SkillCatalog,
    SkillObservation,
    TrainingItem,
    TrainingPlan,
    User,
    VerificationGapRow,
)
from db.settings import settings
from documents import FileStore, validate_upload
from domain.enums import (
    AssessmentStatus,
    EventType,
    GenerationMode,
    HintLevel,
    InterviewerAction,
    InterviewMode,
    JobKind,
    JobStatus,
    MessageKind,
    ReportIntegrity,
    ResourceStatus,
    SessionPhase,
    SessionStatus,
    TurnStatus,
)
from domain.errors import (
    ActionNotAllowed,
    AuthRequired,
    EventCursorExpired,
    IdempotencyMismatch,
    InvalidAnswer,
    ResourceNotFound,
    StateConflict,
)
from domain.events import PUBLIC_SSE_EVENTS, public_event_payload
from domain.policies import MAX_ANSWER_CHARS, MAX_OFFICIAL_QUESTIONS, assert_owner, assert_strict_assessment_hidden
from domain.reports import aggregate_report
from domain.requirements import extract_requirements
from domain.schemas import QuestionProposal, SubmitAnswerCommand, TextSpan, bind_citation
from domain.sessions import assert_expected_version, next_status
from domain.training import is_independent_hint, requirement_id_from_key
from domain.text import normalize_text
from domain.turns import OPEN_TURN_STATUSES, assert_can_publish, assert_ready_for_answer
from llm_gateway.fake import _extract_structure
from observability.logging import hash_obj, hash_text
from retrieval import embed, rrf_fuse, search_chunks, tsquery_string, vector_literal


def _now() -> datetime:
    return datetime.now(UTC)


def _store() -> FileStore:
    return FileStore(settings.file_root)


async def get_or_create_dev_user(db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.is_dev.is_(True)).limit(1))
    user = result.scalar_one_or_none()
    if user:
        return user
    user = User(display_name=settings.dev_user_display_name, is_dev=True)
    db.add(user)
    await db.flush()
    db.add(
        BudgetAccount(
            owner_id=user.id,
            scope="global",
            limit_amount=float(settings.max_session_cost) * 20,
        )
    )
    return user


async def create_auth_session(db: AsyncSession, user: User) -> AuthSession:
    raw = uuid.uuid4().hex
    csrf = uuid.uuid4().hex
    row = AuthSession(
        user_id=user.id,
        token_hash=hash_text(raw),
        csrf_token=csrf,
        expires_at=_now() + timedelta(days=7),
    )
    db.add(row)
    await db.flush()
    row._raw_token = raw  # type: ignore[attr-defined]
    return row


async def user_from_token(db: AsyncSession, raw: str | None) -> User | None:
    if not raw:
        return None
    result = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.token_hash == hash_text(raw),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > _now(),
        )
    )
    row = result.first()
    return row[1] if row else None


async def owned_session(
    db: AsyncSession, session_id: uuid.UUID, owner_id: uuid.UUID, *, lock: bool = False
) -> InterviewSession:
    session = await db.get(
        InterviewSession, session_id, with_for_update=lock or None, populate_existing=lock
    )
    if session is None:
        raise ResourceNotFound("interview not found")
    assert_owner(session.owner_id, owner_id)
    return session


async def append_event(db: AsyncSession, session: InterviewSession, event_type: EventType, payload: dict[str, Any]) -> int:
    session.event_seq += 1
    db.add(
        SessionEvent(
            session_id=session.id,
            seq=session.event_seq,
            type=event_type.value,
            public_payload=public_event_payload(event_type.value, payload, mode=session.mode),
        )
    )
    return session.event_seq


def bump_state(session: InterviewSession) -> int:
    session.state_version += 1
    return session.state_version


async def enqueue_job(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    session_id: uuid.UUID | None,
    kind: JobKind,
    dedupe_key: str,
    payload: dict[str, Any],
    priority: int = 100,
    available_at: datetime | None = None,
) -> uuid.UUID | None:
    stmt = (
        insert(Job)
        .values(
            id=uuid.uuid4(),
            owner_id=owner_id,
            session_id=session_id,
            kind=kind.value,
            dedupe_key=dedupe_key,
            status=JobStatus.QUEUED.value,
            priority=priority,
            payload_json=payload,
            available_at=available_at or _now(),
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Job.id)
    )
    result = await db.execute(stmt)
    row = result.first()
    return row[0] if row else None


async def idempotent(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    route: str,
    key: str | None,
    body: Any,
) -> dict | None:
    if not key:
        return None
    key_hash = hash_text(key)
    request_hash = hash_obj(body)
    existing = await db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == owner_id,
            IdempotencyRecord.route == route,
            IdempotencyRecord.key_hash == key_hash,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise IdempotencyMismatch("idempotency key reused with a different body")
    return {"status": row.response_status, "body": row.response_json}


async def store_idempotency(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    route: str,
    key: str | None,
    body: Any,
    status: int,
    response: dict,
) -> None:
    if not key:
        return
    db.add(
        IdempotencyRecord(
            owner_id=owner_id,
            route=route,
            key_hash=hash_text(key),
            request_hash=hash_obj(body),
            response_status=status,
            response_json=response,
            expires_at=_now() + timedelta(hours=24),
        )
    )


async def create_resume(db: AsyncSession, owner_id: uuid.UUID, text: str | None, filename: str | None, data: bytes | None, content_type: str | None) -> Resume:
    resume = Resume(owner_id=owner_id, status=ResourceStatus.PENDING.value, original_filename=filename)
    db.add(resume)
    await db.flush()
    if data:
        kind = validate_upload(
            filename=filename or "upload.bin",
            content_type=content_type or "",
            data=data,
            max_bytes=settings.max_upload_bytes,
        )
        key = f"{owner_id}/{resume.id}/{uuid.uuid4().hex}"
        _store().write(key, data)
        resume.file_key = key
        resume.status = ResourceStatus.PENDING.value
        await enqueue_job(
            db,
            owner_id=owner_id,
            session_id=None,
            kind=JobKind.PARSE_RESUME,
            dedupe_key=f"parse_resume:{resume.id}:v1",
            payload={"resume_id": str(resume.id), "kind": kind, "file_key": key},
            priority=50,
        )
        await db.flush()
        return resume
    if not text:
        raise InvalidAnswer("resume text or file is required")
    extracted = normalize_text(text)
    db.add(
        ResumeVersion(
            resume_id=resume.id,
            version=1,
            normalized_text=extracted,
            structured_json=_extract_structure(extracted),
            sha256=hash_text(extracted),
        )
    )
    resume.status = ResourceStatus.READY.value
    resume.parse_error = None
    await db.flush()
    return resume


async def apply_parsed_resume(db: AsyncSession, resume: Resume, text: str) -> ResumeVersion:
    extracted = normalize_text(text)
    result = await db.execute(select(func.max(ResumeVersion.version)).where(ResumeVersion.resume_id == resume.id))
    next_version = (result.scalar() or 0) + 1
    row = ResumeVersion(
        resume_id=resume.id,
        version=next_version,
        normalized_text=extracted,
        structured_json=_extract_structure(extracted),
        sha256=hash_text(extracted),
    )
    db.add(row)
    resume.status = ResourceStatus.READY.value
    resume.parse_error = None
    await db.flush()
    return row


async def mark_resume_needs_text(db: AsyncSession, resume: Resume, error: str) -> None:
    resume.status = ResourceStatus.NEEDS_TEXT_INPUT.value
    resume.parse_error = error[:500]
    await db.flush()


async def confirm_resume(db: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID, text: str, structured: dict | None) -> ResumeVersion:
    resume = await db.get(Resume, resume_id)
    if resume is None:
        raise ResourceNotFound("resume not found")
    assert_owner(resume.owner_id, owner_id)
    if resume.status in {ResourceStatus.DELETED.value, ResourceStatus.DELETING.value} or resume.deleted_at is not None:
        raise ResourceNotFound("resume not found")
    normalized = normalize_text(text)
    result = await db.execute(select(func.max(ResumeVersion.version)).where(ResumeVersion.resume_id == resume.id))
    next_version = (result.scalar() or 0) + 1
    from agents.extractor import extract_resume_claims

    frozen = dict(structured or _extract_structure(normalized))
    frozen["claims"] = await extract_resume_claims(normalized, owner_id=str(owner_id))
    row = ResumeVersion(
        resume_id=resume.id,
        version=next_version,
        normalized_text=normalized,
        structured_json=frozen,
        sha256=hash_text(normalized),
        confirmed_at=_now(),
    )
    db.add(row)
    resume.status = ResourceStatus.CONFIRMED.value
    resume.parse_error = None
    await db.flush()
    return row


async def create_job_profile(db: AsyncSession, owner_id: uuid.UUID, title: str, text: str) -> JobProfile:
    profile = JobProfile(owner_id=owner_id, title=title, status=ResourceStatus.READY.value)
    db.add(profile)
    await db.flush()
    version = JobProfileVersion(
        profile_id=profile.id,
        version=1,
        text=normalize_text(text),
        requirements_json=extract_requirements(text),
    )
    db.add(version)
    await db.flush()
    return profile


async def confirm_job_profile(db: AsyncSession, owner_id: uuid.UUID, profile_id: uuid.UUID, text: str) -> JobProfileVersion:
    profile = await db.get(JobProfile, profile_id)
    if profile is None:
        raise ResourceNotFound("job profile not found")
    assert_owner(profile.owner_id, owner_id)
    result = await db.execute(select(func.max(JobProfileVersion.version)).where(JobProfileVersion.profile_id == profile.id))
    next_version = (result.scalar() or 0) + 1
    from agents.extractor import extract_job_requirements

    normalized = normalize_text(text)
    requirements = await extract_job_requirements(normalized, owner_id=str(owner_id))
    row = JobProfileVersion(
        profile_id=profile.id,
        version=next_version,
        text=normalized,
        requirements_json=requirements,
        confirmed_at=_now(),
    )
    db.add(row)
    profile.status = ResourceStatus.CONFIRMED.value
    await db.flush()
    return row


async def create_interview(
    db: AsyncSession,
    owner_id: uuid.UUID,
    resume_version_id: uuid.UUID,
    job_profile_version_id: uuid.UUID,
    mode: str,
    duration_minutes: int,
) -> InterviewSession:
    resume_version = await db.get(ResumeVersion, resume_version_id)
    job_version = await db.get(JobProfileVersion, job_profile_version_id)
    if resume_version is None or job_version is None:
        raise ResourceNotFound("resume or job profile version not found")
    resume = await db.get(Resume, resume_version.resume_id)
    profile = await db.get(JobProfile, job_version.profile_id)
    assert_owner(resume.owner_id, owner_id)
    assert_owner(profile.owner_id, owner_id)
    if resume_version.confirmed_at is None or job_version.confirmed_at is None:
        raise ActionNotAllowed("resume and job profile versions must be confirmed")
    snapshot = await db.execute(select(KnowledgeSnapshot).order_by(KnowledgeSnapshot.created_at.desc()).limit(1))
    snap = snapshot.scalar_one_or_none()
    session = InterviewSession(
        owner_id=owner_id,
        resume_version_id=resume_version.id,
        job_profile_version_id=job_version.id,
        mode=mode,
        duration_minutes=duration_minutes,
        knowledge_snapshot_id=snap.id if snap else None,
        model_config_json={"provider": settings.model_provider, "model": settings.model_identifier},
    )
    db.add(session)
    await db.flush()
    limit = settings.max_session_cost.strip() if settings.max_session_cost else ""
    if limit:
        db.add(
            BudgetAccount(
                owner_id=owner_id,
                scope=f"session:{session.id}",
                limit_amount=float(limit),
            )
        )
    return session


async def start_interview(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, expected_state_version: int) -> dict:
    session = await owned_session(db, session_id, owner_id)
    assert_expected_version(session.state_version, expected_state_version)
    session.status = next_status(SessionStatus(session.status), "start").value
    session.phase = SessionPhase.PLANNING.value
    session.started_at = _now()
    session.soft_deadline_at = session.started_at + timedelta(minutes=session.duration_minutes)
    bump_state(session)
    await append_event(db, session, EventType.SESSION_STARTED, {"session_id": str(session.id), "state_version": session.state_version})
    await append_event(db, session, EventType.PHASE_CHANGED, {"phase": session.phase})
    plan_job = await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=session.id,
        kind=JobKind.BUILD_PLAN,
        dedupe_key=f"build_plan:{session.id}:v1",
        payload={"session_id": str(session.id)},
        priority=200,
    )
    q_job = await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=session.id,
        kind=JobKind.GENERATE_QUESTION,
        dedupe_key=f"generate_question:{session.id}:turn1:rev1",
        payload={"session_id": str(session.id), "revision": 1},
        priority=180,
    )
    return {"session_id": str(session.id), "state_version": session.state_version, "job_ids": [str(j) for j in [plan_job, q_job] if j]}


async def submit_answer(
    db: AsyncSession,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    command: SubmitAnswerCommand,
    idempotency_key: str | None,
) -> dict:
    cached = await idempotent(db, owner_id=owner_id, route=f"POST /interviews/{session_id}/answers", key=idempotency_key, body=command.model_dump())
    if cached:
        return cached["body"]
    session = await owned_session(db, session_id, owner_id, lock=True)
    # Another submission can commit while this transaction waits for the row lock.
    cached = await idempotent(
        db, owner_id=owner_id, route=f"POST /interviews/{session_id}/answers",
        key=idempotency_key, body=command.model_dump(),
    )
    if cached:
        return cached["body"]
    if session.status != SessionStatus.ACTIVE.value:
        raise ActionNotAllowed("session is not accepting answers")
    assert_expected_version(session.state_version, command.expected_state_version)
    if len(command.text) > MAX_ANSWER_CHARS:
        raise InvalidAnswer("answer exceeds 6000 characters")
    turn = await db.get(InterviewTurn, uuid.UUID(command.turn_id))
    if turn is None or turn.session_id != session.id:
        raise ResourceNotFound("turn not found")
    open_turns = await db.execute(
        select(InterviewTurn).where(InterviewTurn.session_id == session.id, InterviewTurn.status.in_(list(OPEN_TURN_STATUSES)))
    )
    current = open_turns.scalars().one_or_none()
    assert_ready_for_answer(turn_status=TurnStatus(turn.status), is_current=current is not None and current.id == turn.id)
    message = Message(
        session_id=session.id,
        turn_id=turn.id,
        kind=MessageKind.ANSWER.value,
        normalized_text=command.text,
        text_sha256=hash_text(command.text),
    )
    db.add(message)
    turn.status = TurnStatus.ANSWERED.value
    turn.answered_at = _now()
    session.phase = SessionPhase.GENERATING.value
    bump_state(session)
    await append_event(
        db,
        session,
        EventType.ANSWER_ACCEPTED,
        {"turn_id": str(turn.id), "state_version": session.state_version},
    )
    await db.flush()
    eval_job = await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=session.id,
        kind=JobKind.EVALUATE_TURN,
        dedupe_key=f"evaluate:{turn.id}:{message.text_sha256}:{turn.rubric_snapshot_id}:revision_1",
        payload={"session_id": str(session.id), "turn_id": str(turn.id), "answer_message_id": str(message.id)},
        priority=120,
    )
    next_job = None
    answered = await db.execute(
        select(func.count()).select_from(InterviewTurn).where(
            InterviewTurn.session_id == session.id, InterviewTurn.status == TurnStatus.ANSWERED.value
        )
    )
    answered_count = answered.scalar() or 0
    over_time = bool(session.soft_deadline_at and _now() >= session.soft_deadline_at)
    if answered_count < MAX_OFFICIAL_QUESTIONS and not over_time:
        next_no = answered_count + 1
        next_job = await enqueue_job(
            db,
            owner_id=owner_id,
            session_id=session.id,
            kind=JobKind.GENERATE_QUESTION,
            dedupe_key=f"generate_question:{session.id}:after:{turn.id}:rev1",
            payload={"session_id": str(session.id), "after_turn_id": str(turn.id), "revision": 1},
            priority=160,
        )
    else:
        await _enter_closing(db, session, "soft_limit" if over_time else "agenda_done")
    body = {
        "accepted": True,
        "answer_message_id": str(message.id),
        "session_status": session.status,
        "phase": session.phase,
        "state_version": session.state_version,
        "job_ids": [str(j) for j in [eval_job, next_job] if j],
    }
    await store_idempotency(
        db,
        owner_id=owner_id,
        route=f"POST /interviews/{session_id}/answers",
        key=idempotency_key,
        body=command.model_dump(),
        status=202,
        response=body,
    )
    return body


async def skip_turn(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, turn_id: uuid.UUID, expected_state_version: int, reason: str) -> dict:
    session = await owned_session(db, session_id, owner_id)
    assert_expected_version(session.state_version, expected_state_version)
    turn = await db.get(InterviewTurn, turn_id)
    if turn is None or turn.session_id != session.id:
        raise ResourceNotFound("turn not found")
    if turn.status != TurnStatus.READY.value:
        raise StateConflict("only READY turns can be skipped")
    turn.status = TurnStatus.SKIPPED.value
    session.phase = SessionPhase.GENERATING.value
    bump_state(session)
    job = await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=session.id,
        kind=JobKind.GENERATE_QUESTION,
        dedupe_key=f"generate_question:{session.id}:skip:{turn.id}:rev1",
        payload={"session_id": str(session.id), "after_turn_id": str(turn.id), "revision": 1, "skipped": True},
        priority=160,
    )
    return {"skipped": True, "reason": reason, "state_version": session.state_version, "job_ids": [str(job)] if job else []}


async def finish_interview(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, expected_state_version: int) -> dict:
    session = await owned_session(db, session_id, owner_id)
    assert_expected_version(session.state_version, expected_state_version)
    if session.status in {SessionStatus.CLOSING.value, SessionStatus.COMPLETED.value}:
        return {"session_status": session.status, "state_version": session.state_version}
    await _enter_closing(db, session, "finish")
    return {"session_status": session.status, "state_version": session.state_version}


async def cancel_interview(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, expected_state_version: int) -> dict:
    session = await owned_session(db, session_id, owner_id)
    assert_expected_version(session.state_version, expected_state_version)
    session.status = next_status(SessionStatus(session.status), "cancel").value
    await _abandon_open(db, session)
    bump_state(session)
    await append_event(db, session, EventType.SESSION_CANCELLED, {"state_version": session.state_version})
    return {"session_status": session.status, "state_version": session.state_version}


async def _enter_closing(db: AsyncSession, session: InterviewSession, action: str) -> None:
    session.status = next_status(SessionStatus(session.status), action).value
    session.phase = SessionPhase.IDLE.value
    max_turn = await db.execute(select(func.max(InterviewTurn.turn_no)).where(InterviewTurn.session_id == session.id))
    session.closed_turn_no = max_turn.scalar() or 0
    await _abandon_open(db, session)
    bump_state(session)
    await append_event(
        db,
        session,
        EventType.PHASE_CHANGED,
        {"phase": session.phase, "status": session.status, "state_version": session.state_version},
    )
    await enqueue_job(
        db,
        owner_id=session.owner_id,
        session_id=session.id,
        kind=JobKind.BUILD_REPORT,
        dedupe_key=f"build_report:{session.id}:cutoff:{session.closed_turn_no}",
        payload={"session_id": str(session.id), "cutoff_turn_no": session.closed_turn_no},
        priority=90,
        available_at=_now(),
    )


async def _abandon_open(db: AsyncSession, session: InterviewSession) -> None:
    result = await db.execute(
        select(InterviewTurn).where(InterviewTurn.session_id == session.id, InterviewTurn.status.in_(list(OPEN_TURN_STATUSES)))
    )
    for turn in result.scalars():
        turn.status = TurnStatus.ABANDONED.value if turn.status == TurnStatus.RESERVED.value else TurnStatus.SKIPPED.value
    jobs = await db.execute(
        select(Job).where(
            Job.session_id == session.id,
            Job.kind == JobKind.GENERATE_QUESTION.value,
            Job.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value, JobStatus.RUNNING.value]),
        )
    )
    for job in jobs.scalars():
        job.cancel_requested_at = _now()
        if job.status != JobStatus.RUNNING.value:
            job.status = JobStatus.CANCELLED.value


async def session_snapshot(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID) -> dict:
    session = await owned_session(db, session_id, owner_id)
    turns = (
        await db.execute(
            select(InterviewTurn).where(InterviewTurn.session_id == session.id).order_by(InterviewTurn.turn_no)
        )
    ).scalars().all()
    messages = (
        await db.execute(select(Message).where(Message.session_id == session.id).order_by(Message.created_at))
    ).scalars().all()
    current = next((t for t in turns if t.status == TurnStatus.READY.value), None)
    question = None
    if current:
        q = await db.execute(
            select(Message).where(Message.turn_id == current.id, Message.kind == MessageKind.QUESTION.value)
        )
        row = q.scalar_one_or_none()
        if row:
            question = {"turn_id": str(current.id), "turn_no": current.turn_no, "text": row.normalized_text}
    return {
        "id": str(session.id),
        "status": session.status,
        "phase": session.phase,
        "mode": session.mode,
        "state_version": session.state_version,
        "last_event_seq": session.event_seq,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "soft_deadline_at": session.soft_deadline_at.isoformat() if session.soft_deadline_at else None,
        "current_question": question,
        "turns": [
            {
                "id": str(t.id),
                "turn_no": t.turn_no,
                "status": t.status,
                "topic_id": t.topic_id,
                "skill_ids": t.skill_ids,
            }
            for t in turns
        ],
        "messages": [
            {
                "id": str(m.id),
                "turn_id": str(m.turn_id),
                "kind": m.kind,
                "text": m.normalized_text,
            }
            for m in messages
        ],
    }


async def list_events(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, cursor: int) -> list[SessionEvent]:
    session = await owned_session(db, session_id, owner_id)
    if session.event_seq - cursor > settings.event_retention:
        raise EventCursorExpired("event cursor expired")
    result = await db.execute(
        select(SessionEvent)
        .where(SessionEvent.session_id == session.id, SessionEvent.seq > cursor)
        .order_by(SessionEvent.seq)
    )
    visible = []
    for event in result.scalars():
        if event.type not in PUBLIC_SSE_EVENTS:
            continue
        event.public_payload = public_event_payload(event.type, event.public_payload or {}, mode=session.mode)
        visible.append(event)
    return visible


async def get_assessment(db: AsyncSession, owner_id: uuid.UUID, assessment_id: uuid.UUID) -> Assessment:
    row = await db.get(Assessment, assessment_id)
    if row is None:
        raise ResourceNotFound("assessment not found")
    assert_owner(row.owner_id, owner_id)
    session = await db.get(InterviewSession, row.session_id)
    assert_strict_assessment_hidden(InterviewMode(session.mode), SessionStatus(session.status))
    return row


async def request_review(db: AsyncSession, owner_id: uuid.UUID, assessment_id: uuid.UUID, reason: str) -> ReviewRequest:
    assessment = await db.get(Assessment, assessment_id)
    if assessment is None:
        raise ResourceNotFound("assessment not found")
    assert_owner(assessment.owner_id, owner_id)
    session = await db.get(InterviewSession, assessment.session_id)
    if session.status != SessionStatus.COMPLETED.value:
        raise ActionNotAllowed("reviews start after the interview completes")
    req = ReviewRequest(owner_id=owner_id, assessment_id=assessment.id, reason=reason)
    db.add(req)
    await db.flush()
    await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=assessment.session_id,
        kind=JobKind.REVIEW_ASSESSMENT,
        dedupe_key=f"review:{assessment.id}:{req.id}",
        payload={"assessment_id": str(assessment.id), "review_id": str(req.id)},
        priority=80,
    )
    return req


async def latest_report(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID) -> Report:
    session = await owned_session(db, session_id, owner_id)
    if session.status not in {SessionStatus.COMPLETED.value, SessionStatus.CLOSING.value, SessionStatus.CANCELLED.value}:
        raise ActionNotAllowed("report is hidden until the interview is closing or complete")
    result = await db.execute(
        select(Report).where(Report.session_id == session.id).order_by(Report.revision.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ResourceNotFound("report not found")
    return row


async def report_view(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID) -> dict[str, Any]:
    report = await latest_report(db, owner_id, session_id)
    body = report.body_json or {}
    message_ids: list[uuid.UUID] = []
    rubric_ids: list[uuid.UUID] = []
    turn_ids: list[str] = []
    for skill in body.get("coverage") or []:
        for item in (skill.get("evidence") or []) + (skill.get("history") or []):
            if item.get("turn_id"):
                turn_ids.append(str(item["turn_id"]))
            if item.get("assessment_id"):
                try:
                    assessment = await db.get(Assessment, uuid.UUID(item["assessment_id"]))
                except (TypeError, ValueError):
                    assessment = None
                if assessment:
                    rubric_ids.append(assessment.rubric_snapshot_id)
            for span in item.get("answer_evidence") or []:
                mid = span.get("message_id")
                if not mid:
                    continue
                try:
                    message_ids.append(uuid.UUID(mid))
                except (TypeError, ValueError):
                    continue
    turns = (
        await db.execute(
            select(InterviewTurn).where(
                InterviewTurn.session_id == session_id,
                InterviewTurn.turn_no <= report.cutoff_turn_no,
            ).order_by(InterviewTurn.turn_no)
        )
    ).scalars().all()
    turn_ids.extend(str(turn.id) for turn in turns)
    transcript = (
        await db.execute(select(Message).where(
            Message.session_id == session_id,
            Message.turn_id.in_([turn.id for turn in turns]),
        ))
    ).scalars().all()
    message_ids.extend(message.id for message in transcript)
    messages_by_turn = {(message.turn_id, message.kind): message for message in transcript}
    assessments = (
        await db.execute(select(Assessment).where(
            Assessment.session_id == session_id,
            Assessment.owner_id == owner_id,
            Assessment.turn_id.in_([turn.id for turn in turns]),
        ).order_by(Assessment.revision.desc()))
    ).scalars().all()
    frozen_ids = set(report.assessment_ids or [])
    frozen_by_turn = {item.turn_id: item for item in assessments if str(item.id) in frozen_ids}
    latest_by_turn = {}
    for item in assessments:
        latest_by_turn.setdefault(item.turn_id, item)
    turn_reviews = []
    for turn in turns:
        question = messages_by_turn.get((turn.id, MessageKind.QUESTION.value))
        answer = messages_by_turn.get((turn.id, MessageKind.ANSWER.value))
        assessment = frozen_by_turn.get(turn.id)
        latest = latest_by_turn.get(turn.id)
        assessment_status = assessment.status if assessment else (
            "FAILED" if latest and latest.status == AssessmentStatus.FAILED.value else "PENDING"
        )
        turn_reviews.append({
            "turn_id": str(turn.id),
            "turn_no": turn.turn_no,
            "action": turn.action,
            "parent_turn_id": str(turn.parent_turn_id) if turn.parent_turn_id else None,
            "status": turn.status,
            "question_text": question.normalized_text if question else "",
            "answer_text": answer.normalized_text if answer else "",
            "rubric_snapshot_id": str(turn.rubric_snapshot_id) if turn.rubric_snapshot_id else None,
            "assessment": {
                "id": str(assessment.id) if assessment else None,
                "revision": assessment.revision if assessment else None,
                "status": assessment_status,
                "dimensions": ((assessment.result_json or {}).get("dimensions") or []) if assessment else [],
            } if turn.status == TurnStatus.ANSWERED.value else None,
        })
    messages = {}
    for mid in dict.fromkeys(message_ids):
        row = await db.get(Message, mid)
        if row and row.session_id == session_id:
            messages[str(row.id)] = {
                "id": str(row.id),
                "turn_id": str(row.turn_id),
                "kind": row.kind,
                "text": row.normalized_text,
            }
    questions = {}
    for tid in dict.fromkeys(turn_ids):
        try:
            turn_uuid = uuid.UUID(tid)
        except (TypeError, ValueError):
            continue
        q = (
            await db.execute(select(Message).where(Message.turn_id == turn_uuid, Message.kind == "QUESTION"))
        ).scalar_one_or_none()
        if q:
            questions[tid] = q.normalized_text
        turn = await db.get(InterviewTurn, turn_uuid)
        if turn and turn.rubric_snapshot_id:
            rubric_ids.append(turn.rubric_snapshot_id)
    rubrics = {}
    for rid in dict.fromkeys(rubric_ids):
        snap = await db.get(RubricSnapshot, rid)
        if snap:
            rubrics[str(snap.id)] = {"id": str(snap.id), "template_id": snap.template_id, "criteria": snap.criteria_json}
    session = await db.get(InterviewSession, session_id)
    training_reason = await _training_block_reason(db, session, report)
    return {
        "id": str(report.id),
        "revision": report.revision,
        "status": report.status,
        "body": body,
        "coverage": report.coverage_json,
        "messages": messages,
        "questions": questions,
        "rubrics": rubrics,
        "turns": turn_reviews,
        "training": {"eligible": training_reason is None, "reason": training_reason},
        "integrity": body.get("integrity"),
        "missing_turn_ids": body.get("missing_turn_ids") or [],
    }


async def regenerate_report(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID) -> uuid.UUID | None:
    session = await owned_session(db, session_id, owner_id)
    return await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=session.id,
        kind=JobKind.BUILD_REPORT,
        dedupe_key=f"build_report:{session.id}:manual:{uuid.uuid4()}",
        payload={"session_id": str(session.id), "cutoff_turn_no": session.closed_turn_no or 0},
        priority=90,
    )


async def _snapshot_version_ids(db: AsyncSession, session: InterviewSession) -> list[uuid.UUID]:
    if not session.knowledge_snapshot_id:
        return []
    snap = await db.get(KnowledgeSnapshot, session.knowledge_snapshot_id)
    if snap is None:
        return []
    out: list[uuid.UUID] = []
    for raw in snap.version_ids or []:
        out.append(raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw)))
    return out


async def chunk_in_snapshot(db: AsyncSession, session: InterviewSession, chunk_id: str) -> dict | None:
    version_ids = await _snapshot_version_ids(db, session)
    if not version_ids:
        return None
    try:
        cid = uuid.UUID(chunk_id)
    except ValueError:
        return None
    chunk = await db.get(KnowledgeChunk, cid)
    if chunk is None or chunk.version_id not in version_ids:
        return None
    return {"id": str(chunk.id), "text": chunk.text, "skill_tags": chunk.skill_tags or []}


async def _search_reference_sql(
    db: AsyncSession,
    version_ids: list[uuid.UUID],
    query: str,
    skill: str | None,
) -> list[dict]:
    from sqlalchemy import bindparam, text as sql_text

    if not version_ids:
        return []
    q_and = tsquery_string(query, op="&")
    q_or = tsquery_string(query, op="|")
    qvec = vector_literal(embed(query or " "))
    skill_json = json.dumps([skill]) if skill else "[]"
    has_skill = bool(skill)
    params = {
        "vids": version_ids,
        "q": q_and,
        "qvec": qvec,
        "has_skill": has_skill,
        "skill_json": skill_json,
    }
    lex_stmt = sql_text(
        """
        SELECT kc.id::text AS id
        FROM knowledge_chunks kc
        WHERE kc.version_id IN :vids
          AND kc.tsv IS NOT NULL
          AND :q <> ''
          AND kc.tsv @@ to_tsquery('simple', :q)
          AND (:has_skill = false OR kc.skill_tags @> CAST(:skill_json AS jsonb))
        ORDER BY ts_rank(kc.tsv, to_tsquery('simple', :q)) DESC
        LIMIT 20
        """
    ).bindparams(bindparam("vids", expanding=True))
    vec_stmt = sql_text(
        """
        SELECT kc.id::text AS id, kc.text AS text, kc.skill_tags AS skill_tags
        FROM knowledge_chunks kc
        JOIN knowledge_embeddings ke ON ke.chunk_id = kc.id
        WHERE kc.version_id IN :vids
          AND (:has_skill = false OR kc.skill_tags @> CAST(:skill_json AS jsonb))
        ORDER BY ke.vector <=> CAST(:qvec AS vector)
        LIMIT 20
        """
    ).bindparams(bindparam("vids", expanding=True))
    lex_ids = [row[0] for row in (await db.execute(lex_stmt, params)).all()]
    if not lex_ids and q_or and q_or != q_and:
        params["q"] = q_or
        lex_ids = [row[0] for row in (await db.execute(lex_stmt, params)).all()]
    vec_rows = [
        {"id": row[0], "text": row[1], "skill_tags": row[2] or []}
        for row in (await db.execute(vec_stmt, params)).all()
    ]
    vec_ids = [row["id"] for row in vec_rows]
    fused = rrf_fuse(lex_ids, vec_ids, limit=5)
    by_id = {row["id"]: row for row in vec_rows}
    missing = [cid for cid in fused if cid not in by_id]
    if missing:
        extra = (
            await db.execute(select(KnowledgeChunk).where(KnowledgeChunk.id.in_([uuid.UUID(cid) for cid in missing])))
        ).scalars()
        for chunk in extra:
            by_id[str(chunk.id)] = {"id": str(chunk.id), "text": chunk.text, "skill_tags": chunk.skill_tags or []}
    return [by_id[cid] for cid in fused if cid in by_id]


async def search_reference(db: AsyncSession, session: InterviewSession, query: str, skill: str | None) -> list[dict]:
    version_ids = await _snapshot_version_ids(db, session)
    if not version_ids:
        return []
    try:
        hits = await _search_reference_sql(db, version_ids, query, skill)
        if hits:
            return hits[:5]
        if skill:
            hits = await _search_reference_sql(db, version_ids, query, None)
            if hits:
                return hits[:5]
    except Exception:
        hits = []
    chunks = (
        await db.execute(select(KnowledgeChunk).where(KnowledgeChunk.version_id.in_(version_ids)))
    ).scalars().all()
    payload = []
    for chunk in chunks:
        emb = await db.execute(select(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id == chunk.id))
        row = emb.scalar_one_or_none()
        payload.append(
            {
                "id": str(chunk.id),
                "text": chunk.text,
                "skill_tags": chunk.skill_tags,
                "vector": list(row.vector) if row and row.vector is not None else embed(chunk.text),
            }
        )
    return search_chunks(payload, query, skill)[:5]


async def _training_block_reason(db: AsyncSession, session: InterviewSession, report: Report | None) -> str | None:
    """Use the same eligibility checks for the report UI and actual plan creation."""
    if session.status != SessionStatus.COMPLETED.value:
        return "面试尚未结束，结束后才能生成训练计划。"
    pending = (await db.execute(select(func.count()).select_from(Job).where(
        Job.session_id == session.id, Job.kind == JobKind.EVALUATE_TURN.value,
        Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.RETRY_WAIT.value]),
    ))).scalar() or 0
    if pending:
        return "评分任务仍在处理中，请等待评分完成后再生成训练计划。"
    if report is None:
        return "这场面试还没有报告，请等待报告生成。"
    if not (report.body_json or {}).get("next_training"):
        return "本场报告暂无需要训练的已确认弱项，因此不生成弱项训练计划。"
    return None


async def create_training_plan(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID) -> uuid.UUID | None:
    session = await owned_session(db, session_id, owner_id)
    report = (await db.execute(select(Report).where(Report.session_id == session_id)
                              .order_by(Report.revision.desc()).limit(1))).scalar_one_or_none()
    reason = await _training_block_reason(db, session, report)
    if reason:
        raise ActionNotAllowed(reason)
    dedupe_key = f"training:{session.id}:v1"
    job_id = await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=session.id,
        kind=JobKind.BUILD_TRAINING_PLAN,
        dedupe_key=dedupe_key,
        payload={"session_id": str(session.id)},
        priority=70,
    )
    if job_id:
        return job_id
    existing = (await db.execute(select(Job).where(Job.dedupe_key == dedupe_key)
                                .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if existing and existing.status == JobStatus.DEAD.value:
        # Preserve attempt_count / AgentRun history; grant a new bounded retry budget.
        existing.max_attempts = existing.attempt_count + 3
        existing.status = JobStatus.QUEUED.value
        existing.available_at = _now()
        existing.last_error_code = None
        existing.result_ref = None
        existing.lease_epoch += 1
        existing.lease_owner = None
        existing.lease_expires_at = None
    return existing.id if existing else None


async def submit_exercise(
    db: AsyncSession,
    owner_id: uuid.UUID,
    item_id: uuid.UUID,
    text: str,
    hint_level: str,
) -> ExerciseSubmission:
    item = await db.get(TrainingItem, item_id)
    if item is None:
        raise ResourceNotFound("training item not found")
    plan = await db.get(TrainingPlan, item.plan_id)
    assert_owner(plan.owner_id, owner_id)
    try:
        hint = HintLevel(hint_level)
    except ValueError as exc:
        raise InvalidAnswer("hint_level must be NONE, NUDGE, STRUCTURE, or EXAMPLE") from exc
    if item.kind == "RETEST" and hint is not HintLevel.NONE:
        raise ActionNotAllowed("retest must be answered independently")
    row = ExerciseSubmission(
        owner_id=owner_id,
        item_id=item.id,
        normalized_text=normalize_text(text),
        hint_level=hint.value,
    )
    db.add(row)
    await db.flush()
    await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=plan.session_id,
        kind=JobKind.EVALUATE_EXERCISE,
        dedupe_key=f"evaluate_exercise:{row.id}",
        payload={"submission_id": str(row.id), "session_id": str(plan.session_id)},
        priority=110,
    )
    return row


async def create_audio_draft(
    db: AsyncSession,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    data: bytes,
    filename: str,
) -> AudioDraft:
    session = await owned_session(db, session_id, owner_id)
    turn = await db.get(InterviewTurn, turn_id)
    if turn is None or turn.session_id != session.id:
        raise ResourceNotFound("turn not found")
    if turn.status != TurnStatus.READY.value:
        raise ActionNotAllowed("audio drafts can only bind the current READY turn")
    if len(data) > 20 * 1024 * 1024:
        raise InvalidAnswer("audio exceeds 20MB")
    key = f"audio/{owner_id}/{session.id}/{uuid.uuid4().hex}"
    _store().write(key, data)
    draft = AudioDraft(
        owner_id=owner_id,
        session_id=session.id,
        turn_id=turn.id,
        file_key=key,
        status="PENDING",
    )
    db.add(draft)
    await db.flush()
    await enqueue_job(
        db,
        owner_id=owner_id,
        session_id=session.id,
        kind=JobKind.TRANSCRIBE_AUDIO,
        dedupe_key=f"transcribe:{draft.id}",
        payload={"draft_id": str(draft.id), "filename": filename},
        priority=140,
    )
    return draft


async def confirm_audio_draft(db: AsyncSession, owner_id: uuid.UUID, draft_id: uuid.UUID, edited_text: str) -> AudioDraft:
    draft = await db.get(AudioDraft, draft_id)
    if draft is None:
        raise ResourceNotFound("audio draft not found")
    assert_owner(draft.owner_id, owner_id)
    draft.transcript_edited = normalize_text(edited_text)
    draft.status = "CONFIRMED"
    return draft


async def skill_timeline(db: AsyncSession, owner_id: uuid.UUID) -> list[dict]:
    rows = (
        await db.execute(
            select(SkillObservation)
            .where(SkillObservation.owner_id == owner_id, SkillObservation.invalidated_at.is_(None))
            .order_by(SkillObservation.observed_at.desc())
        )
    ).scalars().all()
    items = [
        {
            "id": str(r.id),
            "skill_id": r.skill_id,
            "requirement_id": r.skill_id,
            "level": r.level,
            "hint_level": r.hint_level,
            "independent": is_independent_hint(r.hint_level),
            "evidence_strength": r.evidence_strength,
            "observed_at": r.observed_at.isoformat(),
            "source": "interview",
            "source_assessment_id": str(r.source_assessment_id),
        }
        for r in rows
    ]
    submissions = (
        await db.execute(
            select(ExerciseSubmission)
            .where(ExerciseSubmission.owner_id == owner_id)
            .order_by(ExerciseSubmission.created_at.desc())
        )
    ).scalars().all()
    for row in submissions:
        result = row.result_json or {}
        if not result:
            continue
        dims = result.get("dimensions") or [{}]
        dim = dims[0] if dims else {}
        item = await db.get(TrainingItem, row.item_id)
        req_id = dim.get("skill_id") or requirement_id_from_key(item.template_key if item else "")
        items.append(
            {
                "id": str(row.id),
                "skill_id": req_id,
                "requirement_id": req_id,
                "level": dim.get("level"),
                "hint_level": row.hint_level,
                "independent": is_independent_hint(row.hint_level),
                "evidence_strength": dim.get("evidence_status") or "PENDING",
                "observed_at": row.created_at.isoformat(),
                "source": "exercise",
                "source_assessment_id": None,
            }
        )
    items.sort(key=lambda item: item.get("observed_at") or "", reverse=True)
    return items


async def serialize_training_plans(db: AsyncSession, owner_id: uuid.UUID) -> list[dict]:
    plans = (
        await db.execute(select(TrainingPlan).where(TrainingPlan.owner_id == owner_id).order_by(TrainingPlan.created_at.desc()))
    ).scalars().all()
    items = []
    for plan in plans:
        train_items = (
            await db.execute(select(TrainingItem).where(TrainingItem.plan_id == plan.id).order_by(TrainingItem.created_at))
        ).scalars().all()
        session = await db.get(InterviewSession, plan.session_id)
        jd = await db.get(JobProfileVersion, session.job_profile_version_id) if session else None
        from domain.requirements import coerce_requirements

        frozen = coerce_requirements(jd.requirements_json if jd else None, jd.text if jd else "")
        labels = {row.get("id"): row.get("text") for row in frozen.get("items") or []}
        serialized = []
        for item in train_items:
            subs = (
                await db.execute(
                    select(ExerciseSubmission)
                    .where(ExerciseSubmission.item_id == item.id)
                    .order_by(ExerciseSubmission.created_at.desc())
                )
            ).scalars().all()
            req_id = requirement_id_from_key(item.template_key)
            serialized.append(
                {
                    "id": str(item.id),
                    "kind": item.kind,
                    "question_text": item.question_text,
                    "template_key": item.template_key,
                    "variant_template_key": item.variant_template_key,
                    "hint_templates": item.hint_templates,
                    "requirement_id": req_id,
                    "requirement_label": labels.get(req_id, req_id),
                    "submissions": [
                        {
                            "id": str(sub.id),
                            "hint_level": sub.hint_level,
                            "independent": is_independent_hint(sub.hint_level),
                            "text": sub.normalized_text,
                            "result": sub.result_json or None,
                        }
                        for sub in subs
                    ],
                }
            )
        items.append(
            {
                "id": str(plan.id),
                "session_id": str(plan.session_id),
                "skill_ids": plan.skill_ids,
                "rationale": plan.rationale,
                "items": serialized,
            }
        )
    return items


async def freeze_jobs(db: AsyncSession, *, owner_id: uuid.UUID, session_id: uuid.UUID | None = None, resume_id: uuid.UUID | None = None) -> int:
    jobs = (
        await db.execute(
            select(Job).where(
                Job.owner_id == owner_id,
                Job.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value, JobStatus.RUNNING.value]),
            )
        )
    ).scalars()
    frozen = 0
    for job in jobs:
        payload = job.payload_json or {}
        match_session = session_id is not None and job.session_id == session_id
        match_resume = resume_id is not None and payload.get("resume_id") == str(resume_id)
        if session_id is None and resume_id is None:
            continue
        if not (match_session or match_resume):
            continue
        job.cancel_requested_at = _now()
        if job.status != JobStatus.RUNNING.value:
            job.status = JobStatus.CANCELLED.value
        frozen += 1
    return frozen


async def delete_resume(db: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID) -> None:
    resume = await db.get(Resume, resume_id)
    if resume is None:
        raise ResourceNotFound("resume not found")
    assert_owner(resume.owner_id, owner_id)
    resume.status = ResourceStatus.DELETING.value
    versions = (await db.execute(select(ResumeVersion).where(ResumeVersion.resume_id == resume.id))).scalars().all()
    version_ids = [v.id for v in versions]
    sessions = []
    if version_ids:
        sessions = (
            await db.execute(select(InterviewSession).where(InterviewSession.resume_version_id.in_(version_ids)))
        ).scalars().all()
    for session in sessions:
        if session.status in {SessionStatus.CREATED.value, SessionStatus.ACTIVE.value, SessionStatus.CLOSING.value}:
            session.status = SessionStatus.CANCELLED.value
            bump_state(session)
        await freeze_jobs(db, owner_id=owner_id, session_id=session.id)
    await freeze_jobs(db, owner_id=owner_id, resume_id=resume.id)
    resume.deleted_at = _now()
    resume.status = ResourceStatus.DELETED.value
    if resume.file_key:
        _store().delete(resume.file_key)


async def delete_interview(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID) -> None:
    session = await owned_session(db, session_id, owner_id)
    await freeze_jobs(db, owner_id=owner_id, session_id=session.id)
    if session.status not in {SessionStatus.COMPLETED.value, SessionStatus.CANCELLED.value, SessionStatus.FAILED.value}:
        session.status = SessionStatus.CANCELLED.value
        bump_state(session)
        await append_event(db, session, EventType.SESSION_CANCELLED, {"state_version": session.state_version})
