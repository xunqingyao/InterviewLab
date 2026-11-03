from __future__ import annotations

from enum import StrEnum


class SessionStatus(StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class SessionPhase(StrEnum):
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    AWAITING_ANSWER = "AWAITING_ANSWER"
    IDLE = "IDLE"


class InterviewMode(StrEnum):
    STRICT = "STRICT"
    ASSISTED = "ASSISTED"
    RETEST = "RETEST"


class TurnStatus(StrEnum):
    RESERVED = "RESERVED"
    READY = "READY"
    ANSWERED = "ANSWERED"
    SKIPPED = "SKIPPED"
    ABANDONED = "ABANDONED"


class MessageKind(StrEnum):
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    SYSTEM = "SYSTEM"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    DEAD = "DEAD"
    CANCELLED = "CANCELLED"


class JobKind(StrEnum):
    PARSE_RESUME = "PARSE_RESUME"
    PARSE_JOB_PROFILE = "PARSE_JOB_PROFILE"
    BUILD_PLAN = "BUILD_PLAN"
    GENERATE_QUESTION = "GENERATE_QUESTION"
    EVALUATE_TURN = "EVALUATE_TURN"
    BUILD_REPORT = "BUILD_REPORT"
    REVIEW_ASSESSMENT = "REVIEW_ASSESSMENT"
    EXTRACT_OBSERVATIONS = "EXTRACT_OBSERVATIONS"
    BUILD_TRAINING_PLAN = "BUILD_TRAINING_PLAN"
    EVALUATE_EXERCISE = "EVALUATE_EXERCISE"
    TRANSCRIBE_AUDIO = "TRANSCRIBE_AUDIO"


class AssessmentStatus(StrEnum):
    PENDING = "PENDING"
    ASSESSED = "ASSESSED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    FAILED = "FAILED"


class EvidenceStatus(StrEnum):
    DEMONSTRATED = "DEMONSTRATED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REFERENCE_CONFLICT = "REFERENCE_CONFLICT"


class HintLevel(StrEnum):
    NONE = "NONE"
    NUDGE = "NUDGE"
    STRUCTURE = "STRUCTURE"
    EXAMPLE = "EXAMPLE"


class ReportIntegrity(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


class ResourceStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    NEEDS_TEXT_INPUT = "NEEDS_TEXT_INPUT"
    CONFIRMED = "CONFIRMED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class InterviewerAction(StrEnum):
    ASK = "ASK"
    FOLLOW_UP = "FOLLOW_UP"
    END = "END"


class GenerationMode(StrEnum):
    MODEL = "MODEL"
    FALLBACK_TEMPLATE = "FALLBACK_TEMPLATE"
    SEEDED = "SEEDED"


class EventType(StrEnum):
    SESSION_STARTED = "session.started"
    PHASE_CHANGED = "phase.changed"
    ANSWER_ACCEPTED = "answer.accepted"
    QUESTION_READY = "question.ready"
    EVALUATION_PROGRESS = "evaluation.progress"
    REPORT_READY = "report.ready"
    SESSION_COMPLETED = "session.completed"
    SESSION_CANCELLED = "session.cancelled"
    JOB_FAILED = "job.failed"
    RESYNC_REQUIRED = "resync.required"
    TRAINING_READY = "training.ready"
    AUDIO_TRANSCRIBED = "audio.transcribed"
