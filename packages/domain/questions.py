"""Validate the exact server-authorized direction before publishing a question."""

from domain.claims import probe_from_key, unused_probes
from domain.enums import InterviewerAction
from domain.errors import InvalidAgentOutput
from domain.policies import MAX_FOLLOWUPS_PER_TOPIC
from domain.requirements import requirement_by_id
from domain.rubrics import RUBRIC_POLICY_ID
from domain.schemas import QuestionDecision, QuestionProposal


def materialize_question_decision(raw: dict, snapshot: dict) -> QuestionProposal:
    decision = QuestionDecision.model_validate(raw).root
    fields = {
        "action": decision.action,
        "reason": decision.reason,
        "decision_summary": decision.decision_summary,
    }
    if decision.action == "FOLLOW_UP":
        req_id = (snapshot.get("last_skill_ids") or [None])[0]
        fields.update(
            claim_id=(snapshot.get("last_claim_ids") or [None])[0],
            probe_key=snapshot.get("last_probe_key"),
            topic_id=req_id,
            skill_ids=[req_id] if req_id else [],
            parent_turn_id=snapshot.get("last_turn_id"),
            question_text=decision.question_text,
            rubric_template_id=RUBRIC_POLICY_ID,
        )
    elif decision.action == "ASK":
        fields.update(
            claim_id=decision.claim_id,
            probe_key=decision.probe_key,
            topic_id=decision.requirement_id,
            skill_ids=[decision.requirement_id],
            question_text=decision.question_text,
            rubric_template_id=RUBRIC_POLICY_ID,
        )
    proposal = QuestionProposal.model_validate(fields)
    validate_question_binding(proposal, snapshot)
    return proposal


def validate_question_binding(proposal: QuestionProposal, snapshot: dict) -> None:
    proposal.validate_semantics()
    if proposal.action is InterviewerAction.END:
        return
    if proposal.rubric_template_id not in {RUBRIC_POLICY_ID, "rubric_evidence_v1"}:
        raise InvalidAgentOutput("interviewer cannot select a different grading policy")
    requirement = requirement_by_id(snapshot, proposal.skill_ids[0])
    probe = probe_from_key(snapshot, proposal.probe_key)
    if requirement is None or probe is None or probe["id"] != proposal.claim_id:
        raise InvalidAgentOutput("question requirement, claim or probe does not belong to this snapshot")
    allowed_gaps = {
        str(row["id"]) for row in snapshot.get("gaps") or [] if row.get("skill_id") == proposal.skill_ids[0]
    }
    if not set(proposal.source_gap_ids).issubset(allowed_gaps):
        raise InvalidAgentOutput("question gap does not belong to its requirement")
    if proposal.action is InterviewerAction.ASK:
        if proposal.probe_key not in {row["probe_key"] for row in unused_probes(snapshot)}:
            raise InvalidAgentOutput("question probe was already used")
    elif not snapshot.get("last_answer") or any(
        (
            proposal.parent_turn_id != snapshot.get("last_turn_id"),
            proposal.claim_id != (snapshot.get("last_claim_ids") or [None])[0],
            proposal.probe_key != snapshot.get("last_probe_key"),
            proposal.skill_ids != snapshot.get("last_skill_ids"),
            int(snapshot.get("followup_depth") or 0) >= MAX_FOLLOWUPS_PER_TOPIC,
        )
    ):
        raise InvalidAgentOutput("follow-up must keep its current answered parent and direction")
    if int(snapshot.get("remaining_question_budget", 1)) <= 0 or snapshot.get("remaining_seconds") == 0:
        raise InvalidAgentOutput("question budget is exhausted")
    if proposal.question_text in set(snapshot.get("used_question_texts") or []):
        raise InvalidAgentOutput("question text was already published")
