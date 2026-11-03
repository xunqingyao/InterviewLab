from __future__ import annotations

from domain.enums import InterviewerAction
from domain.errors import StateConflict
from domain.policies import MAX_FOLLOWUPS_PER_TOPIC
from domain.schemas import AssessmentResult, CoachProposal, QuestionProposal


async def propose_next_question(snapshot: dict, *, owner_id: str, session_id: str) -> QuestionProposal:
    from agents.interviewer import interviewer_graph

    final = await interviewer_graph().ainvoke(
        {"snapshot": snapshot, "owner_id": owner_id, "session_id": session_id}
    )
    proposal = QuestionProposal.model_validate(final["proposal"])
    proposal.validate_semantics()
    depth = int(snapshot.get("followup_depth") or 0)
    if proposal.action is InterviewerAction.FOLLOW_UP and depth >= MAX_FOLLOWUPS_PER_TOPIC:
        raise StateConflict("follow-up depth exceeded")
    used = set(snapshot.get("used_question_texts") or [])
    if proposal.question_text and proposal.question_text in used:
        raise StateConflict("duplicate question")
    return proposal


async def evaluate_turn(snapshot: dict, *, owner_id: str, session_id: str) -> AssessmentResult:
    from agents.evaluator import evaluator_graph

    final = await evaluator_graph().ainvoke(
        {"snapshot": snapshot, "owner_id": owner_id, "session_id": session_id}
    )
    return AssessmentResult.model_validate(final["result"])


async def propose_training(snapshot: dict, *, owner_id: str, session_id: str) -> CoachProposal:
    from agents.context import build_coach_context
    from llm_gateway.factory import build_gateway
    from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext

    gateway = build_gateway()
    from domain.training import TrainingProposalError, validate_training_proposal
    from observability.logging import logger

    pack = build_coach_context(snapshot)
    for attempt in range(2):
        result = await gateway.generate_structured(
            role=AgentRole.COACH,
            input_snapshot=AgentInputSnapshot(
                id=snapshot.get("id", "snap"),
                role=AgentRole.COACH,
                payload=pack,
            ),
            output_schema=CoachProposal,
            tools=[],
            budget=BudgetContext(owner_id=owner_id, session_id=session_id),
        )
        proposal = CoachProposal.model_validate(result.data)
        try:
            validate_training_proposal(proposal, snapshot)
            return proposal
        except TrainingProposalError as exc:
            logger.warning(
                "coach.proposal_rejected snapshot_id=%s reason=%s repair=%s",
                snapshot.get("id"),
                exc.reason_code,
                attempt == 0,
            )
            if attempt:
                raise
            pack = {
                **pack,
                "validation_feedback": {
                    "code": exc.reason_code,
                    "message": str(exc),
                    "instruction": "重新生成完整提案；只能选择输入中的 ID，每条要求最多一个目标，练习和复测角度必须不同。",
                },
            }
    raise RuntimeError("unreachable")
