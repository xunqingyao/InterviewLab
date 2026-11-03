from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents import evaluate_turn, propose_next_question, propose_training
from agents.interviewer import fallback_from_requirements
from agents.personalize import compose_training_pair
from domain.claims import (
    claim_items,
    coerce_claims,
    unused_probes,
)
from domain.requirements import RUBRIC_EVIDENCE_ID, coerce_requirements, requirement_by_id
from domain.questions import validate_question_binding
from domain.rubrics import build_question_rubric
from domain.thread import parent_chain
from db.commands import append_event, apply_parsed_resume, bump_state, enqueue_job, mark_resume_needs_text, search_reference
from db.jobs_repo import fence, heartbeat
from db.models import (
    AgentRun,
    Assessment,
    AssessmentEvidence,
    AudioDraft,
    ExerciseSubmission,
    InterviewPlan,
    InterviewSession,
    InterviewTurn,
    Job,
    JobProfileVersion,
    Message,
    Report,
    Resume,
    ResumeVersion,
    ReviewRequest,
    RubricSnapshot,
    SkillObservation,
    TrainingItem,
    TrainingPlan,
    VerificationGapRow,
)
from db.settings import settings
from domain.assessments import sanitize_assessment_result
from domain.enums import (
    AssessmentStatus,
    EventType,
    GenerationMode,
    InterviewerAction,
    JobKind,
    JobStatus,
    MessageKind,
    ReportIntegrity,
    ResourceStatus,
    SessionPhase,
    SessionStatus,
    TurnStatus,
)
from domain.errors import ActionNotAllowed, InvalidAgentOutput, StateConflict, UnreadableResume
from domain.policies import MAX_OFFICIAL_QUESTIONS
from domain.reports import aggregate_report
from domain.training import (
    LAYERED_HINTS,
    exercise_key,
    is_independent_hint,
    requirement_id_from_key,
    resolve_training_targets,
    retest_key,
)
from domain.text import redact_identity
from domain.turns import OPEN_TURN_STATUSES, assert_can_publish
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext
from observability.logging import hash_text


def _now() -> datetime:
    return datetime.now(UTC)


def _sanitize_snapshot(result, snapshot: dict[str, Any]):
    from agents.context import build_evaluator_context

    sources = {row["message_id"]: row["text"]
               for row in build_evaluator_context(snapshot)["evidence_answers"] if row.get("message_id")}
    return sanitize_assessment_result(
        result, answer_message_id=snapshot["answer_message_id"], answer_text=snapshot["answer_text"],
        answer_sources=sources, expected_turn_id=snapshot["turn_id"],
        expected_rubric_id=snapshot["rubric_snapshot_id"], allowed_skill_ids=snapshot["skill_ids"],
        allowed_reference_ids=[row["id"] for row in snapshot.get("reference_hits") or [] if row.get("id")],
        expected_hint_level=snapshot.get("hint_level") or "NONE",
    )


def _assert_job_session(job: Job, session: InterviewSession) -> None:
    if session is None or job.session_id != session.id or job.owner_id != session.owner_id:
        raise InvalidAgentOutput("job does not belong to its session and owner")
    payload_session = job.payload_json.get("session_id")
    if payload_session is not None and payload_session != str(session.id):
        raise InvalidAgentOutput("job payload session_id does not match the persisted session")


def _rubric_row(question_text: str, criteria: dict) -> RubricSnapshot:
    return RubricSnapshot(
        template_id=criteria["template_id"], question_hash=hash_text(question_text),
        criteria_json=criteria, reference_versions=[], schema_version="v2",
    )


async def process_job(db: AsyncSession, job: Job, worker_id: str) -> None:
    job_id = job.id
    epoch = job.lease_epoch
    job = await db.get(Job, job_id)
    run = (
        await db.execute(
            select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.attempt_no == job.attempt_count)
        )
    ).scalar_one_or_none()
    if run is None:
        run = AgentRun(
            job_id=job.id,
            attempt_no=job.attempt_count,
            lease_epoch=epoch,
            checkpoint_thread_id=f"{job.id}:{job.attempt_count}",
            status="RUNNING",
            model_config_id=settings.model_identifier,
        )
        db.add(run)
    else:
        run.lease_epoch = epoch
        run.status = "RUNNING"
        run.ended_at = None
        run.checkpoint_thread_id = f"{job.id}:{job.attempt_count}"
    await db.commit()
    job = await db.get(Job, job_id)
    if job and job.cancel_requested_at is not None:
        if job.status == JobStatus.RUNNING.value:
            job.status = JobStatus.CANCELLED.value
        run.status = "CANCELLED"
        run.ended_at = _now()
        await db.commit()
        return
    stop = asyncio.Event()

    async def _heartbeat_loop() -> None:
        from db.session import SessionLocal

        while not stop.is_set():
            for _ in range(max(1, settings.heartbeat_seconds * 5)):
                if stop.is_set():
                    return
                await asyncio.sleep(0.2)
            try:
                async with SessionLocal() as beat_db:
                    row = await beat_db.get(Job, job_id)
                    if row is None:
                        return
                    await heartbeat(beat_db, row, worker_id, epoch)
            except ActionNotAllowed:
                return
            except Exception:
                return

    beater = asyncio.create_task(_heartbeat_loop()) if settings.heartbeat_seconds > 0 else None
    try:
        kind = JobKind(job.kind)
        if kind is JobKind.BUILD_PLAN:
            await handle_build_plan(db, job, worker_id, epoch)
        elif kind is JobKind.GENERATE_QUESTION:
            await handle_generate_question(db, job, worker_id, epoch)
        elif kind is JobKind.EVALUATE_TURN:
            await handle_evaluate_turn(db, job, worker_id, epoch)
        elif kind is JobKind.BUILD_REPORT:
            await handle_build_report(db, job, worker_id, epoch)
        elif kind is JobKind.REVIEW_ASSESSMENT:
            await handle_review(db, job, worker_id, epoch)
        elif kind is JobKind.BUILD_TRAINING_PLAN:
            await handle_training(db, job, worker_id, epoch)
        elif kind is JobKind.EVALUATE_EXERCISE:
            await handle_exercise(db, job, worker_id, epoch)
        elif kind is JobKind.TRANSCRIBE_AUDIO:
            await handle_transcribe(db, job, worker_id, epoch)
        elif kind is JobKind.PARSE_RESUME:
            await handle_parse_resume(db, job, worker_id, epoch)
        elif kind in {JobKind.PARSE_JOB_PROFILE, JobKind.EXTRACT_OBSERVATIONS}:
            await _succeed(db, job, worker_id, epoch, {"ok": True})
        else:
            await _succeed(db, job, worker_id, epoch, {"skipped": True})
        run.status = "SUCCEEDED"
        run.ended_at = _now()
        await db.commit()
    except ActionNotAllowed:
        run.status = "FENCED"
        run.ended_at = _now()
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        job = await db.get(Job, job_id, with_for_update=True, populate_existing=True)
        if not (
            job and job.status == JobStatus.RUNNING.value and job.lease_owner == worker_id
            and job.lease_epoch == epoch and job.lease_expires_at and job.lease_expires_at > _now()
        ):
            run.status = "FENCED"
            run.ended_at = _now()
            await db.commit()
            return
        if job and job.status == JobStatus.RUNNING.value:
            job.last_error_code = type(exc).__name__
            from domain.training import TrainingProposalError
            if isinstance(exc, TrainingProposalError):
                job.result_ref = {"error_reason": exc.public_message, "reason_code": exc.reason_code}
            if job.attempt_count >= job.max_attempts:
                job.status = JobStatus.DEAD.value
                if job.kind == JobKind.EVALUATE_TURN.value:
                    await _record_failed_assessment(db, job)
                if job.session_id:
                    session = await db.get(InterviewSession, job.session_id)
                    if session:
                        await append_event(
                            db,
                            session,
                            EventType.JOB_FAILED,
                            {"code": job.last_error_code or type(exc).__name__, "kind": job.kind},
                        )
            else:
                job.status = JobStatus.RETRY_WAIT.value
        run.status = "FAILED"
        run.ended_at = _now()
        await db.commit()
        raise
    finally:
        stop.set()
        if beater is not None:
            beater.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await beater


async def _succeed(db: AsyncSession, job: Job, worker_id: str, epoch: int, result: dict[str, Any]) -> None:
    locked = await fence(db, job, worker_id, epoch)
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = result


async def handle_parse_resume(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    from documents import extract_text_isolated
    from db.commands import _store

    resume = await db.get(Resume, uuid.UUID(job.payload_json["resume_id"]))
    if resume is None or resume.status != ResourceStatus.PENDING.value or not resume.file_key:
        await _succeed(db, job, worker_id, epoch, {"skipped": True})
        return
    path = _store().root / resume.file_key
    try:
        text = await extract_text_isolated(path)
        await apply_parsed_resume(db, resume, text)
        await _succeed(db, job, worker_id, epoch, {"status": ResourceStatus.READY.value})
    except UnreadableResume as exc:
        await mark_resume_needs_text(db, resume, str(exc))
        await _succeed(db, job, worker_id, epoch, {"status": ResourceStatus.NEEDS_TEXT_INPUT.value})


async def _qa_for_turns(db: AsyncSession, turns: list[InterviewTurn]) -> list[dict[str, Any]]:
    rows = []
    for turn in turns:
        q = (
            await db.execute(select(Message).where(Message.turn_id == turn.id, Message.kind == MessageKind.QUESTION.value))
        ).scalar_one_or_none()
        a = (
            await db.execute(select(Message).where(Message.turn_id == turn.id, Message.kind == MessageKind.ANSWER.value))
        ).scalar_one_or_none()
        rows.append(
            {
                "turn_id": str(turn.id),
                "turn_no": turn.turn_no,
                "followup_depth": turn.followup_depth,
                "question": q.normalized_text if q else "",
                "answer": a.normalized_text if a else "",
                "answer_message_id": str(a.id) if a else None,
            }
        )
    return rows


async def _build_interviewer_snapshot(
    db: AsyncSession,
    session: InterviewSession,
    job: Job,
    turns: list[InterviewTurn],
) -> dict[str, Any]:
    used_texts: list[str] = []
    questions_by_turn: dict[uuid.UUID, str] = {}
    for turn in turns:
        q = await db.execute(select(Message).where(Message.turn_id == turn.id, Message.kind == MessageKind.QUESTION.value))
        msg = q.scalar_one_or_none()
        if msg:
            used_texts.append(msg.normalized_text)
            questions_by_turn[turn.id] = msg.normalized_text
    last_answer = ""
    last_question = ""
    last_turn_id = None
    followup_depth = 0
    last_topic_id = None
    last_skill_ids: list[str] = []
    thread: list[dict[str, Any]] = []
    if turns:
        last = turns[-1]
        last_turn_id = str(last.id)
        followup_depth = last.followup_depth
        last_topic_id = last.topic_id
        last_skill_ids = last.skill_ids or []
        last_question = questions_by_turn.get(last.id, "")
        a = await db.execute(select(Message).where(Message.turn_id == last.id, Message.kind == MessageKind.ANSWER.value))
        ans = a.scalar_one_or_none()
        last_answer = ans.normalized_text if ans else ""
        thread = await _qa_for_turns(db, parent_chain(turns, last))
    gaps = (
        await db.execute(
            select(VerificationGapRow).where(
                VerificationGapRow.session_id == session.id, VerificationGapRow.status == "OPEN"
            )
        )
    ).scalars().all()
    resume = await db.get(ResumeVersion, session.resume_version_id)
    jd = await db.get(JobProfileVersion, session.job_profile_version_id)
    remaining_seconds = None
    if session.soft_deadline_at:
        remaining_seconds = max(0, int((session.soft_deadline_at - _now()).total_seconds()))
    answered_count = len([t for t in turns if t.status == TurnStatus.ANSWERED.value])
    max_questions = min(8, MAX_OFFICIAL_QUESTIONS)
    covered = []
    for turn in turns:
        if turn.status != TurnStatus.ANSWERED.value:
            continue
        for skill in turn.skill_ids or []:
            if skill not in covered:
                covered.append(skill)
    frozen_claims = coerce_claims(resume.structured_json if resume else None, resume.normalized_text if resume else "")
    claim_rows = frozen_claims.get("items") or []
    used_probe_keys = list(dict.fromkeys(turn.probe_key for turn in turns if turn.probe_key))
    last_claim_id = turns[-1].claim_id if turns else None
    last_probe_key = turns[-1].probe_key if turns else None
    remaining_claims = {item["id"] for item in unused_probes({"claims": frozen_claims, "used_probe_keys": used_probe_keys})}
    used_claim_ids = [str(item["id"]) for item in claim_rows if item["id"] not in remaining_claims]
    return {
        "id": str(job.input_snapshot_id or job.id),
        "mode": session.mode,
        "resume_plain": redact_identity(resume.normalized_text if resume else ""),
        "jd_plain": redact_identity(jd.text if jd else ""),
        "requirements": coerce_requirements(jd.requirements_json if jd else None, jd.text if jd else ""),
        "claims": frozen_claims,
        "used_claim_ids": used_claim_ids,
        "used_probe_keys": used_probe_keys,
        "last_claim_ids": [last_claim_id] if last_claim_id else [],
        "last_probe_key": last_probe_key,
        "used_requirement_ids": covered,
        "used_question_texts": used_texts,
        "covered_skill_ids": covered,
        "thread": thread,
        "last_answer": last_answer,
        "last_question": last_question,
        "last_turn_id": last_turn_id,
        "last_topic_id": last_topic_id,
        "last_skill_ids": last_skill_ids,
        "last_rubric_template_id": RUBRIC_EVIDENCE_ID if turns else None,
        "answered_count": answered_count,
        "followup_depth": followup_depth,
        "remaining_question_budget": max(0, max_questions - len(turns)),
        "max_questions": max_questions,
        "remaining_seconds": remaining_seconds,
        "gaps": [{"id": str(g.id), "skill_id": g.skill_id} for g in gaps],
    }


async def handle_build_plan(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    session = await db.get(InterviewSession, job.session_id)
    jd = await db.get(JobProfileVersion, session.job_profile_version_id)
    frozen = coerce_requirements(jd.requirements_json if jd else None, jd.text if jd else "")
    topics = [
        {"requirement_id": item.get("id"), "text": item.get("text"), "priority": item.get("priority")}
        for item in frozen.get("items") or []
    ]
    locked = await fence(db, job, worker_id, epoch)
    db.add(
        InterviewPlan(
            session_id=session.id,
            version=1,
            topics_json=topics,
            skill_catalog_version=session.skill_catalog_version,
            knowledge_snapshot_id=session.knowledge_snapshot_id,
        )
    )
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = {"topics": len(topics)}


async def handle_generate_question(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    session = await db.get(InterviewSession, uuid.UUID(job.payload_json["session_id"]))
    _assert_job_session(job, session)
    if session.status not in {SessionStatus.ACTIVE.value}:
        await _succeed(db, job, worker_id, epoch, {"abandoned": True})
        return
    open_count = (
        await db.execute(
            select(func.count()).select_from(InterviewTurn).where(
                InterviewTurn.session_id == session.id, InterviewTurn.status.in_(list(OPEN_TURN_STATUSES))
            )
        )
    ).scalar() or 0
    if open_count:
        await _succeed(db, job, worker_id, epoch, {"skipped": "open_turn_exists"})
        return

    turns = (await db.execute(select(InterviewTurn).where(InterviewTurn.session_id == session.id).order_by(InterviewTurn.turn_no))).scalars().all()
    snapshot = await _build_interviewer_snapshot(db, session, job, turns)
    after_turn_id = job.payload_json.get("after_turn_id")
    if after_turn_id is not None and after_turn_id != snapshot["last_turn_id"]:
        await _succeed(db, job, worker_id, epoch, {"skipped": "stale_parent"})
        return
    generation_mode = GenerationMode.MODEL
    try:
        proposal = await propose_next_question(snapshot, owner_id=str(session.owner_id), session_id=str(session.id))
    except Exception:
        proposal = None
        generation_mode = GenerationMode.FALLBACK_TEMPLATE

    if proposal is None or (proposal.action is not InterviewerAction.END and not proposal.question_text):
        generation_mode = GenerationMode.FALLBACK_TEMPLATE
        proposal = fallback_from_requirements(snapshot)
    try:
        validate_question_binding(proposal, snapshot)
    except (ValueError, InvalidAgentOutput):
        proposal = fallback_from_requirements(snapshot)
        generation_mode = GenerationMode.FALLBACK_TEMPLATE
        validate_question_binding(proposal, snapshot)

    locked = await fence(db, job, worker_id, epoch)
    session = await db.get(InterviewSession, session.id, with_for_update=True, populate_existing=True)
    if session.status != SessionStatus.ACTIVE.value:
        locked.status = JobStatus.SUCCEEDED.value
        locked.result_ref = {"abandoned": True}
        return
    latest = (await db.execute(select(InterviewTurn).where(InterviewTurn.session_id == session.id)
                              .order_by(InterviewTurn.turn_no.desc()).limit(1))).scalar_one_or_none()
    if (str(latest.id) if latest else None) != snapshot["last_turn_id"]:
        locked.status = JobStatus.SUCCEEDED.value
        locked.result_ref = {"skipped": "stale_input"}
        return
    open_count = (
        await db.execute(
            select(func.count()).select_from(InterviewTurn).where(
                InterviewTurn.session_id == session.id, InterviewTurn.status.in_(list(OPEN_TURN_STATUSES))
            )
        )
    ).scalar() or 0
    try:
        assert_can_publish(open_count)
    except StateConflict:
        locked.status = JobStatus.SUCCEEDED.value
        locked.result_ref = {"skipped": "open_turn_exists"}
        return

    if proposal.action is InterviewerAction.END:
        from db.commands import _enter_closing

        await _enter_closing(db, session, "agenda_done")
        locked.status = JobStatus.SUCCEEDED.value
        locked.result_ref = {"ended": True}
        return

    if proposal.action is InterviewerAction.FOLLOW_UP:
        parent = latest
        parent_rubric = await db.get(RubricSnapshot, parent.rubric_snapshot_id)
        if parent_rubric is None:
            raise InvalidAgentOutput("follow-up parent has no frozen rubric")
        snapshot_row = RubricSnapshot(
            template_id=parent_rubric.template_id, question_hash=hash_text(proposal.question_text),
            criteria_json=parent_rubric.criteria_json, reference_versions=parent_rubric.reference_versions,
            schema_version=parent_rubric.schema_version,
        )
    else:
        requirement = requirement_by_id(snapshot, proposal.skill_ids[0])
        from domain.claims import probe_from_key

        probe = probe_from_key(snapshot, proposal.probe_key)
        criteria = build_question_rubric(
            question_text=proposal.question_text, requirement_id=requirement["id"],
            requirement_text=requirement["text"], claim_id=proposal.claim_id,
            probe_key=proposal.probe_key, probe_hint=probe["probe_hint"],
        )
        snapshot_row = _rubric_row(proposal.question_text, criteria)
    db.add(snapshot_row)
    await db.flush()
    turn_no = (max((t.turn_no for t in turns), default=0) or 0) + 1
    followup_depth = int(snapshot.get("followup_depth") or 0)
    depth = followup_depth + 1 if proposal.action is InterviewerAction.FOLLOW_UP else 0
    turn = InterviewTurn(
        session_id=session.id,
        turn_no=turn_no,
        status=TurnStatus.READY.value,
        action=proposal.action.value,
        claim_id=proposal.claim_id,
        probe_key=proposal.probe_key,
        topic_id=proposal.topic_id,
        parent_turn_id=uuid.UUID(proposal.parent_turn_id) if proposal.parent_turn_id else None,
        rubric_snapshot_id=snapshot_row.id,
        skill_ids=proposal.skill_ids,
        difficulty=proposal.difficulty,
        generation_mode=generation_mode.value,
        issued_at=_now(),
        followup_depth=depth,
    )
    db.add(turn)
    await db.flush()
    db.add(
        Message(
            session_id=session.id,
            turn_id=turn.id,
            kind=MessageKind.QUESTION.value,
            normalized_text=proposal.question_text or "",
            text_sha256=hash_text(proposal.question_text or ""),
        )
    )
    session.phase = SessionPhase.AWAITING_ANSWER.value
    bump_state(session)
    await append_event(
        db,
        session,
        EventType.QUESTION_READY,
        {
            "session_id": str(session.id),
            "turn_id": str(turn.id),
            "question_text": proposal.question_text,
            "state_version": session.state_version,
        },
    )
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = {"turn_id": str(turn.id), "generation_mode": generation_mode.value}


async def _record_failed_assessment(db: AsyncSession, job: Job) -> None:
    """Technical failure is FAILED with no numeric score, never level 0."""
    turn_id = job.payload_json.get("turn_id")
    if not turn_id:
        return
    turn = await db.get(InterviewTurn, uuid.UUID(turn_id))
    if turn is None or turn.rubric_snapshot_id is None:
        return
    session = await db.get(InterviewSession, turn.session_id)
    try:
        _assert_job_session(job, session)
    except InvalidAgentOutput:
        return
    answer = (await db.execute(select(Message).where(Message.turn_id == turn.id,
                                                    Message.kind == MessageKind.ANSWER.value))).scalar_one_or_none()
    if (turn.status != TurnStatus.ANSWERED.value or answer is None
            or job.payload_json.get("answer_message_id") != str(answer.id)):
        return
    existing = (
        await db.execute(select(Assessment).where(Assessment.turn_id == turn.id, Assessment.revision == 1))
    ).scalar_one_or_none()
    if existing:
        return
    db.add(
        Assessment(
            turn_id=turn.id,
            session_id=turn.session_id,
            owner_id=job.owner_id,
            revision=1,
            status=AssessmentStatus.FAILED.value,
            rubric_snapshot_id=turn.rubric_snapshot_id,
            result_json={
                "turn_id": str(turn.id),
                "rubric_snapshot_id": str(turn.rubric_snapshot_id),
                "status": AssessmentStatus.FAILED.value,
                "dimensions": [],
                "verification_gaps": [],
            },
            model_config_id=settings.model_identifier,
        )
    )


async def _build_evaluator_snapshot(
    db: AsyncSession,
    session: InterviewSession,
    turn: InterviewTurn,
    job: Job,
    question: Message,
    answer: Message,
) -> dict[str, Any]:
    _assert_job_session(job, session)
    if turn.session_id != session.id or turn.status != TurnStatus.ANSWERED.value:
        raise InvalidAgentOutput("assessment requires an answered turn in this session")
    for message, kind in ((question, MessageKind.QUESTION.value), (answer, MessageKind.ANSWER.value)):
        if (message.session_id != session.id or message.turn_id != turn.id or message.kind != kind
                or message.text_sha256 != hash_text(message.normalized_text)):
            raise InvalidAgentOutput("assessment message is not the immutable question or answer of this turn")
    if job.kind == JobKind.EVALUATE_TURN.value and (
        job.payload_json.get("turn_id") != str(turn.id)
        or job.payload_json.get("answer_message_id") != str(answer.id)
    ):
        raise InvalidAgentOutput("evaluation job answer binding does not match this turn")
    rubric = await db.get(RubricSnapshot, turn.rubric_snapshot_id)
    if rubric is None or rubric.question_hash != hash_text(question.normalized_text):
        raise InvalidAgentOutput("frozen rubric is not bound to this question")
    turns = (
        await db.execute(
            select(InterviewTurn).where(InterviewTurn.session_id == session.id).order_by(InterviewTurn.turn_no)
        )
    ).scalars().all()
    chain = parent_chain(turns, turn)
    if not chain or chain[0].parent_turn_id is not None:
        raise InvalidAgentOutput("follow-up chain has no valid root")
    for item in chain:
        if (item.session_id != session.id or item.turn_no > turn.turn_no or item.skill_ids != turn.skill_ids
                or item.claim_id != turn.claim_id or item.probe_key != turn.probe_key
                or item.status != TurnStatus.ANSWERED.value):
            raise InvalidAgentOutput("follow-up chain crosses its question direction")
    if chain[0].followup_depth != 0 or any(
        child.turn_no <= parent.turn_no or child.followup_depth != parent.followup_depth + 1
        for parent, child in zip(chain, chain[1:])
    ):
        raise InvalidAgentOutput("follow-up chain order or depth is invalid")
    thread = await _qa_for_turns(db, chain)
    transcript = []
    for item in chain:
        if item.turn_no > turn.turn_no:
            continue
        msgs = (
            await db.execute(select(Message).where(Message.turn_id == item.id).order_by(Message.created_at))
        ).scalars().all()
        for msg in msgs:
            if msg.session_id != session.id or msg.text_sha256 != hash_text(msg.normalized_text):
                raise InvalidAgentOutput("transcript message crosses sessions or has changed")
            transcript.append(
                {
                    "turn_no": item.turn_no,
                    "kind": msg.kind,
                    "message_id": str(msg.id),
                    "text": msg.normalized_text,
                }
            )
    refs = await search_reference(db, session, answer.normalized_text, (turn.skill_ids or [None])[0])
    jd = await db.get(JobProfileVersion, session.job_profile_version_id)
    frozen = coerce_requirements(jd.requirements_json if jd else None, jd.text if jd else "")
    req = requirement_by_id({"requirements": frozen}, (turn.skill_ids or [None])[0])
    if req is None or len(turn.skill_ids) != 1:
        raise InvalidAgentOutput("assessed requirement is not in the session JD")
    if rubric.schema_version == "v2":
        scope = rubric.criteria_json.get("scope") or {}
        if (scope.get("requirement_id") != turn.skill_ids[0] or scope.get("claim_id") != turn.claim_id
                or scope.get("probe_key") != turn.probe_key
                or scope.get("requirement_text") != req["text"]
                or scope.get("question") != thread[0]["question"]):
            raise InvalidAgentOutput("rubric scope does not match this question direction")
    return {
        "id": str(job.input_snapshot_id or job.id),
        "turn_id": str(turn.id),
        "cutoff_turn_no": turn.turn_no,
        "rubric_snapshot_id": str(turn.rubric_snapshot_id),
        "rubric_criteria": rubric.criteria_json if rubric else {},
        "skill_ids": turn.skill_ids,
        "requirements": frozen,
        "requirement_text": (req or {}).get("text") or "",
        "answer_text": answer.normalized_text,
        "answer_message_id": str(answer.id),
        "question_text": question.normalized_text,
        "thread": thread,
        "transcript": transcript,
        "reference_hits": [{"id": r["id"], "text": r.get("text"), "skill_tags": r.get("skill_tags") or []} for r in refs],
        "hint_level": "NONE",
    }


async def handle_evaluate_turn(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    turn = await db.get(InterviewTurn, uuid.UUID(job.payload_json["turn_id"]))
    if turn is None:
        raise InvalidAgentOutput("evaluation turn does not exist")
    session = await db.get(InterviewSession, turn.session_id)
    _assert_job_session(job, session)
    existing = (
        await db.execute(select(Assessment).where(Assessment.turn_id == turn.id, Assessment.revision == 1))
    ).scalar_one_or_none()
    if existing:
        await _succeed(db, job, worker_id, epoch, {"assessment_id": str(existing.id), "duplicate": True})
        return
    answer = (
        await db.execute(select(Message).where(Message.turn_id == turn.id, Message.kind == MessageKind.ANSWER.value))
    ).scalar_one()
    question = (
        await db.execute(select(Message).where(Message.turn_id == turn.id, Message.kind == MessageKind.QUESTION.value))
    ).scalar_one()
    snapshot = await _build_evaluator_snapshot(db, session, turn, job, question, answer)
    result = await evaluate_turn(snapshot, owner_id=str(session.owner_id), session_id=str(session.id))
    result = _sanitize_snapshot(result, snapshot)

    locked = await fence(db, job, worker_id, epoch)
    session = await db.get(InterviewSession, session.id, with_for_update=True, populate_existing=True)
    again = (
        await db.execute(select(Assessment).where(Assessment.turn_id == turn.id, Assessment.revision == 1))
    ).scalar_one_or_none()
    if again:
        locked.status = JobStatus.SUCCEEDED.value
        locked.result_ref = {"assessment_id": str(again.id), "duplicate": True}
        return
    assessment = Assessment(
        turn_id=turn.id,
        session_id=session.id,
        owner_id=session.owner_id,
        revision=1,
        status=result.status,
        rubric_snapshot_id=turn.rubric_snapshot_id,
        result_json=result.model_dump(),
        model_config_id=settings.model_identifier,
    )
    db.add(assessment)
    await db.flush()
    for dim in result.dimensions:
        for span in dim.answer_evidence:
            db.add(
                AssessmentEvidence(
                    assessment_id=assessment.id,
                    dimension_id=dim.skill_id,
                    message_id=uuid.UUID(span.message_id),
                    start_char=span.start_char or 0,
                    end_char=span.end_char or 0,
                    quote=span.quote,
                )
            )
        db.add(
            SkillObservation(
                owner_id=session.owner_id,
                skill_id=dim.skill_id,
                source_assessment_id=assessment.id,
                level=dim.level,
                hint_level=dim.hint_level.value if hasattr(dim.hint_level, "value") else str(dim.hint_level),
                evidence_strength=dim.evidence_status.value if hasattr(dim.evidence_status, "value") else str(dim.evidence_status),
            )
        )
    for gap in result.verification_gaps:
        db.add(
            VerificationGapRow(
                assessment_id=assessment.id,
                session_id=session.id,
                skill_id=gap.skill_id,
                description=gap.description,
                priority=gap.priority,
                scope_topic_id=turn.topic_id,
            )
        )
    await append_event(
        db,
        session,
        EventType.EVALUATION_PROGRESS,
        {"completed": 1, "turn_id": str(turn.id)} if session.mode == "STRICT" else {"turn_id": str(turn.id), "assessment_id": str(assessment.id)},
    )
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = {"assessment_id": str(assessment.id)}
    await _release_closing_report(db, session, assessment.id)


async def _release_closing_report(db: AsyncSession, session: InterviewSession, assessment_id: uuid.UUID) -> None:
    """If the agenda already ended, do not leave BUILD_REPORT sleeping past the last eval."""
    if session.status == SessionStatus.COMPLETED.value:
        report = (await db.execute(select(Report).where(Report.session_id == session.id)
                                  .order_by(Report.revision.desc()).limit(1))).scalar_one_or_none()
        if report and report.status == ReportIntegrity.PARTIAL.value:
            await enqueue_job(
                db, owner_id=session.owner_id, session_id=session.id, kind=JobKind.BUILD_REPORT,
                dedupe_key=f"build_report:{session.id}:late_eval:{assessment_id}",
                payload={"session_id": str(session.id), "cutoff_turn_no": session.closed_turn_no or 0}, priority=90,
            )
        return
    if session.status != SessionStatus.CLOSING.value:
        return
    rows = (
        await db.execute(
            select(Job).where(
                Job.session_id == session.id,
                Job.kind == JobKind.BUILD_REPORT.value,
                Job.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]),
            ).with_for_update(skip_locked=True)
        )
    ).scalars().all()
    now = _now()
    for row in rows:
        row.available_at = now


async def handle_build_report(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    locked = await fence(db, job, worker_id, epoch)
    session = await db.get(InterviewSession, job.session_id, with_for_update=True, populate_existing=True)
    pending_eval = (
        await db.execute(
            select(func.count()).select_from(Job).where(
                Job.session_id == session.id,
                Job.kind == JobKind.EVALUATE_TURN.value,
                Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.RETRY_WAIT.value]),
            )
        )
    ).scalar() or 0
    deadline = job.created_at + timedelta(seconds=settings.report_grace_seconds)
    if pending_eval and _now() < deadline:
        locked.status = JobStatus.QUEUED.value
        locked.available_at = _now() + timedelta(seconds=1)
        locked.attempt_count = max(0, locked.attempt_count - 1)
        return
    cutoff = int(job.payload_json.get("cutoff_turn_no") or session.closed_turn_no or 0)
    turns = (
        await db.execute(
            select(InterviewTurn).where(
                InterviewTurn.session_id == session.id,
                InterviewTurn.turn_no <= cutoff,
                InterviewTurn.status == TurnStatus.ANSWERED.value,
            )
        )
    ).scalars().all()
    assessments = []
    missing = []
    for turn in turns:
        row = (
            await db.execute(
                select(Assessment).where(Assessment.turn_id == turn.id).order_by(Assessment.revision.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if row is None or row.status == AssessmentStatus.FAILED.value:
            missing.append(str(turn.id))
        else:
            payload = dict(row.result_json)
            payload["id"] = str(row.id)
            payload["turn_id"] = str(turn.id)
            payload["direction_id"] = str(parent_chain(turns, turn)[0].id)
            payload["turn_no"] = turn.turn_no
            payload["revision"] = row.revision
            assessments.append(payload)
    plan = (
        await db.execute(select(InterviewPlan).where(InterviewPlan.session_id == session.id).order_by(InterviewPlan.version.desc()).limit(1))
    ).scalar_one_or_none()
    body = aggregate_report(
        session_id=str(session.id),
        mode=session.mode,
        topics=plan.topics_json if plan else [],
        assessments=assessments,
        missing_turn_ids=missing,
        cutoff_turn_no=cutoff,
    )
    revision = (
        await db.execute(select(func.coalesce(func.max(Report.revision), 0)).where(Report.session_id == session.id))
    ).scalar() or 0
    report = Report(
        session_id=session.id,
        revision=revision + 1,
        status=body["integrity"],
        cutoff_turn_no=cutoff,
        assessment_ids=[a["id"] for a in assessments],
        coverage_json={"coverage": body["coverage"], "issues": body["issues"]},
        body_json=body,
    )
    db.add(report)
    if session.status == SessionStatus.CLOSING.value:
        session.status = SessionStatus.COMPLETED.value
        bump_state(session)
        await append_event(db, session, EventType.REPORT_READY, {"revision": report.revision, "integrity": report.status})
        await append_event(db, session, EventType.SESSION_COMPLETED, {"state_version": session.state_version})
    else:
        await append_event(db, session, EventType.REPORT_READY, {"revision": report.revision, "integrity": report.status})
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = {"report_id": str(report.id)}


async def handle_review(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    assessment = await db.get(Assessment, uuid.UUID(job.payload_json["assessment_id"]))
    req = await db.get(ReviewRequest, uuid.UUID(job.payload_json["review_id"]))
    turn = await db.get(InterviewTurn, assessment.turn_id)
    session = await db.get(InterviewSession, assessment.session_id)
    _assert_job_session(job, session)
    if (req.owner_id != session.owner_id or req.assessment_id != assessment.id
            or assessment.owner_id != session.owner_id or turn.session_id != session.id
            or assessment.rubric_snapshot_id != turn.rubric_snapshot_id):
        raise InvalidAgentOutput("review does not belong to this assessment, owner and question")
    answer = (
        await db.execute(select(Message).where(Message.turn_id == turn.id, Message.kind == MessageKind.ANSWER.value))
    ).scalar_one()
    question = (
        await db.execute(select(Message).where(Message.turn_id == turn.id, Message.kind == MessageKind.QUESTION.value))
    ).scalar_one_or_none()
    if question is None:
        raise InvalidAgentOutput("review question is missing; cannot substitute a grading standard")
    snapshot = await _build_evaluator_snapshot(db, session, turn, job, question, answer)
    result = await evaluate_turn(snapshot, owner_id=str(session.owner_id), session_id=str(session.id))
    result = _sanitize_snapshot(result, snapshot)
    locked = await fence(db, job, worker_id, epoch)
    session = await db.get(InterviewSession, session.id, with_for_update=True, populate_existing=True)
    latest_assessment = (await db.execute(select(Assessment).where(Assessment.turn_id == turn.id)
                                         .order_by(Assessment.revision.desc()).limit(1))).scalar_one()
    next_rev = latest_assessment.revision + 1
    new_row = Assessment(
        turn_id=turn.id,
        session_id=session.id,
        owner_id=session.owner_id,
        revision=next_rev,
        status=result.status,
        rubric_snapshot_id=turn.rubric_snapshot_id,
        result_json=result.model_dump(),
        supersedes_id=latest_assessment.id,
        model_config_id=settings.model_identifier,
    )
    db.add(new_row)
    await db.flush()
    req.status = "RESOLVED"
    req.resolved_assessment_id = new_row.id
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = {"assessment_id": str(new_row.id), "supersedes_id": str(latest_assessment.id)}
    await enqueue_job(
        db,
        owner_id=session.owner_id,
        session_id=session.id,
        kind=JobKind.BUILD_REPORT,
        dedupe_key=f"build_report:{session.id}:review:{new_row.id}",
        payload={"session_id": str(session.id), "cutoff_turn_no": session.closed_turn_no or turn.turn_no, "review_assessment_id": str(new_row.id)},
        priority=90,
    )


async def _build_coach_snapshot(db: AsyncSession, session: InterviewSession, job: Job) -> dict[str, Any]:
    report = (
        await db.execute(select(Report).where(Report.session_id == session.id).order_by(Report.revision.desc()).limit(1))
    ).scalar_one_or_none()
    jd = await db.get(JobProfileVersion, session.job_profile_version_id)
    resume = await db.get(ResumeVersion, session.resume_version_id)
    frozen_req = coerce_requirements(jd.requirements_json if jd else None, jd.text if jd else "")
    frozen_claims = coerce_claims(resume.structured_json if resume else None, resume.normalized_text if resume else "")
    body = (report.body_json if report else None) or {}
    coverage = list(body.get("coverage") or [])
    next_ids = {str(item) for item in (body.get("next_training") or [])}
    weak = []
    for item in coverage:
        rid = str(item.get("requirement_id") or item.get("skill_id") or "")
        if not rid:
            continue
        if rid not in next_ids:
            continue
        weak.append(
            {
                "id": rid,
                "text": item.get("label") or rid,
                "independent_sample_count": int(item.get("independent_sample_count") or 0),
                "levels": list(item.get("levels") or []),
                "missing": [row["missing"] for row in item.get("evidence") or [] if row.get("missing")],
            }
        )
    confirmed = []
    for item in coverage:
        for ev in item.get("evidence") or []:
            if not is_independent_hint(ev.get("hint_level")):
                continue
            if ev.get("demonstrated"):
                confirmed.append(
                    {
                        "requirement_id": item.get("requirement_id") or item.get("skill_id"),
                        "hint_level": "NONE",
                        "evidence_status": ev.get("evidence_status"),
                        "demonstrated": ev.get("demonstrated"),
                    }
                )
    return {
        "id": str(job.id),
        "weak_skills": [item["id"] for item in weak if item.get("id")][:3],
        "weak_requirements": weak[:3],
        "training_templates": [{"template_key": exercise_key(item["id"]), "requirement_id": item["id"]} for item in weak[:3] if item.get("id")],
        "confirmed_evidence": confirmed[:8],
        "claims": frozen_claims,
        "requirements": frozen_req,
    }


async def handle_training(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    session = await db.get(InterviewSession, job.session_id)
    _assert_job_session(job, session)
    existing = (
        await db.execute(select(TrainingPlan).where(TrainingPlan.session_id == session.id).order_by(TrainingPlan.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    if existing:
        await _succeed(db, job, worker_id, epoch, {"plan_id": str(existing.id), "duplicate": True})
        return
    snapshot = await _build_coach_snapshot(db, session, job)
    if not snapshot["weak_requirements"]:
        await _succeed(db, job, worker_id, epoch, {"no_confirmed_weakness": True})
        return
    proposal = await propose_training(snapshot, owner_id=str(session.owner_id), session_id=str(session.id))
    from domain.training import validate_training_proposal

    validate_training_proposal(proposal, snapshot)
    targets = {row["id"]: row for row in resolve_training_targets(
        [item.requirement_id for item in proposal.targets], snapshot,
    )}
    claims = {row["id"]: row for row in claim_items(snapshot)}
    locked = await fence(db, job, worker_id, epoch)
    again = (
        await db.execute(select(TrainingPlan).where(TrainingPlan.session_id == session.id).limit(1))
    ).scalar_one_or_none()
    if again:
        locked.status = JobStatus.SUCCEEDED.value
        locked.result_ref = {"plan_id": str(again.id), "duplicate": True}
        return
    plan = TrainingPlan(
        owner_id=session.owner_id,
        session_id=session.id,
        skill_ids=list(targets),
        rationale=proposal.rationale,
    )
    db.add(plan)
    await db.flush()
    for target in proposal.targets:
        req = targets[target.requirement_id]
        claim = claims.get(target.claim_id) or {}
        exercise_text, retest_text = compose_training_pair(
            requirement_text=req.get("text") or "",
            claim_text=claim.get("text") or "",
            exercise_hint=target.exercise_hint,
            retest_hint=target.retest_hint,
        )
        req_id = str(req["id"])
        exercise_rubric = _rubric_row(exercise_text, build_question_rubric(
            question_text=exercise_text, requirement_id=req_id, requirement_text=req["text"],
            claim_id=target.claim_id, probe_hint=target.exercise_hint,
        ))
        retest_rubric = _rubric_row(retest_text, build_question_rubric(
            question_text=retest_text, requirement_id=req_id, requirement_text=req["text"],
            claim_id=target.claim_id, probe_hint=target.retest_hint,
        ))
        db.add_all([exercise_rubric, retest_rubric])
        await db.flush()
        db.add(
            TrainingItem(
                plan_id=plan.id,
                template_key=exercise_key(req_id),
                question_text=exercise_text,
                rubric_template_id=exercise_rubric.template_id,
                rubric_snapshot_id=exercise_rubric.id,
                hint_templates=list(LAYERED_HINTS),
                variant_template_key=retest_key(req_id),
                kind="EXERCISE",
            )
        )
        db.add(
            TrainingItem(
                plan_id=plan.id,
                template_key=retest_key(req_id),
                question_text=retest_text,
                rubric_template_id=retest_rubric.template_id,
                rubric_snapshot_id=retest_rubric.id,
                hint_templates=[],
                kind="RETEST",
            )
        )
    await append_event(db, session, EventType.TRAINING_READY, {"plan_id": str(plan.id)})
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = {"plan_id": str(plan.id)}


async def handle_exercise(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    """Reuse Evaluator isolation. Do not open or write a STRICT interview turn."""
    submission = await db.get(ExerciseSubmission, uuid.UUID(job.payload_json["submission_id"]))
    item = await db.get(TrainingItem, submission.item_id)
    plan = await db.get(TrainingPlan, item.plan_id)
    session = await db.get(InterviewSession, plan.session_id)
    _assert_job_session(job, session)
    if submission.owner_id != plan.owner_id or plan.owner_id != session.owner_id:
        raise InvalidAgentOutput("exercise submission does not belong to this training plan and owner")
    if submission.result_json:
        await _succeed(db, job, worker_id, epoch, {"submission_id": str(submission.id), "duplicate": True})
        return
    jd = await db.get(JobProfileVersion, session.job_profile_version_id)
    frozen = coerce_requirements(jd.requirements_json if jd else None, jd.text if jd else "")
    req_id = requirement_id_from_key(item.template_key) or (plan.skill_ids[0] if plan.skill_ids else "req_01")
    req = requirement_by_id({"requirements": frozen}, req_id)
    if req is None or req_id not in plan.skill_ids:
        raise InvalidAgentOutput("training requirement is not in this plan and session JD")
    if item.rubric_snapshot_id is None:
        # Existing training items have no snapshot. Freeze once without reading the answer.
        await fence(db, job, worker_id, epoch)
        item = await db.get(TrainingItem, item.id, with_for_update=True, populate_existing=True)
        if item.rubric_snapshot_id is None:
            rubric = _rubric_row(item.question_text, build_question_rubric(
                question_text=item.question_text, requirement_id=req_id, requirement_text=req["text"],
            ))
            db.add(rubric)
            await db.flush()
            item.rubric_snapshot_id = rubric.id
            item.rubric_template_id = rubric.template_id
        await db.commit()
    rubric = await db.get(RubricSnapshot, item.rubric_snapshot_id)
    if (rubric is None or rubric.question_hash != hash_text(item.question_text)
            or rubric.criteria_json.get("scope", {}).get("requirement_id") != req_id):
        raise InvalidAgentOutput("training rubric does not belong to this question and requirement")
    snapshot = {
        "id": str(job.id),
        "turn_id": str(submission.id),
        "cutoff_turn_no": 1,
        "rubric_snapshot_id": str(rubric.id),
        "rubric_criteria": rubric.criteria_json,
        "skill_ids": [req_id],
        "requirements": frozen,
        "requirement_text": (req or {}).get("text") or "",
        "answer_text": submission.normalized_text,
        "answer_message_id": str(submission.id),
        "question_text": item.question_text,
        "thread": [],
        "transcript": [
            {"turn_no": 1, "kind": "QUESTION", "message_id": f"q-{submission.id}", "text": item.question_text},
            {"turn_no": 1, "kind": "ANSWER", "message_id": str(submission.id), "text": submission.normalized_text},
        ],
        "hint_level": submission.hint_level,
    }
    result = await evaluate_turn(snapshot, owner_id=str(submission.owner_id), session_id=str(session.id))
    result = _sanitize_snapshot(result, snapshot)
    locked = await fence(db, job, worker_id, epoch)
    if submission.result_json:
        locked.status = JobStatus.SUCCEEDED.value
        locked.result_ref = {"submission_id": str(submission.id), "duplicate": True}
        return
    payload = result.model_dump()
    payload["independent"] = is_independent_hint(submission.hint_level)
    submission.result_json = payload
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = {
        "submission_id": str(submission.id),
        "hint_level": submission.hint_level,
        "independent": payload["independent"],
    }


async def handle_transcribe(db: AsyncSession, job: Job, worker_id: str, epoch: int) -> None:
    from pydantic import BaseModel, Field

    from llm_gateway.fake import FakeGateway

    class Transcript(BaseModel):
        transcript: str = Field(min_length=1)

    draft = await db.get(AudioDraft, uuid.UUID(job.payload_json["draft_id"]))
    turn = await db.get(InterviewTurn, draft.turn_id)
    if turn.status != TurnStatus.READY.value:
        await _succeed(db, job, worker_id, epoch, {"ignored": "turn_closed"})
        return
    result = await FakeGateway().generate_structured(
        role=AgentRole.TRANSCRIBER,
        input_snapshot=AgentInputSnapshot(id=str(draft.id), role=AgentRole.TRANSCRIBER, payload={"hint": None}),
        output_schema=Transcript,
        tools=[],
        budget=BudgetContext(owner_id=str(draft.owner_id), session_id=str(draft.session_id)),
    )
    transcript = result.data["transcript"]
    locked = await fence(db, job, worker_id, epoch)
    draft.transcript_raw = transcript
    draft.status = "TRANSCRIBED"
    session = await db.get(InterviewSession, draft.session_id)
    await append_event(db, session, EventType.AUDIO_TRANSCRIBED, {"draft_id": str(draft.id), "transcript": transcript})
    locked.status = JobStatus.SUCCEEDED.value
    locked.result_ref = {"draft_id": str(draft.id)}
