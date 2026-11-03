import pytest

from agents import evaluate_turn
from agents.evaluator_tools import consult_evaluator_tools, get_reference_chunk, read_transcript
from domain.enums import EvidenceStatus


def _snapshot(**overrides):
    base = {
        "id": "ev-1",
        "turn_id": "turn-1",
        "cutoff_turn_no": 1,
        "rubric_snapshot_id": "rubric-1",
        "rubric_criteria": {"levels": {"0": "error", "2": "ok"}},
        "skill_ids": ["rag.evaluation_design"],
        "answer_text": "我会先固定测试集和生成配置，再比较两种检索。",
        "answer_message_id": "msg-answer",
        "question_text": "你如何评估检索是否变好？",
        "transcript": [
            {"turn_no": 1, "kind": "QUESTION", "message_id": "msg-q", "text": "你如何评估检索是否变好？"},
            {"turn_no": 1, "kind": "ANSWER", "message_id": "msg-answer", "text": "我会先固定测试集和生成配置，再比较两种检索。"},
            {"turn_no": 2, "kind": "QUESTION", "message_id": "msg-future", "text": "未来的题"},
        ],
        "reference_hits": [{"id": "chunk-1", "text": "固定测试集后再改检索。", "skill_tags": ["rag.evaluation_design"]}],
        "hint_level": "NONE",
    }
    base.update(overrides)
    return base


def test_transcript_hides_future_turns():
    visible = read_transcript(_snapshot())
    texts = [m["text"] for m in visible["messages"]]
    assert "未来的题" not in texts
    assert any("测试集" in t for t in texts)


def test_chunk_must_belong_to_snapshot():
    assert "error" in get_reference_chunk(_snapshot(), "other-chunk")


def test_evaluator_tools_capped():
    results = consult_evaluator_tools(_snapshot(), owner_id="owner-1")
    assert "get_question_rubric" in results
    assert "read_transcript" in results
    assert len(results) <= 5


@pytest.mark.asyncio
async def test_evaluator_keeps_real_quote_drops_injected_fake():
    result = await evaluate_turn(_snapshot(inject_bad_quote=True), owner_id="u1", session_id="s1")
    quotes = [span.quote for dim in result.dimensions for span in dim.answer_evidence]
    assert "这段话并不存在于答案里" not in quotes
    if result.dimensions and result.dimensions[0].level is not None:
        assert result.dimensions[0].answer_evidence
        assert all(span.message_id == "msg-answer" for dim in result.dimensions for span in dim.answer_evidence)


@pytest.mark.asyncio
async def test_empty_answer_is_insufficient_not_zero():
    result = await evaluate_turn(
        _snapshot(answer_text="   ", inject_bad_quote=False),
        owner_id="u1",
        session_id="s1",
    )
    assert all(dim.level is None for dim in result.dimensions)
    assert all(dim.level != 0 for dim in result.dimensions)
    assert result.dimensions[0].evidence_status is EvidenceStatus.INSUFFICIENT_EVIDENCE
