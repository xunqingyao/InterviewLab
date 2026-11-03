import json

from agents.context import (
    PACK_COACH,
    PACK_EVALUATOR,
    PACK_INTERVIEWER,
    build_coach_context,
    build_evaluator_context,
    build_interviewer_context,
)


def _interviewer_snapshot(**overrides):
    base = {
        "id": "snap-1",
        "templates": [
            {
                "template_key": "idempotency",
                "question_text": "用户重试提交时如何避免两条答案？",
                "criteria_json": {"secret": "hidden-pass-threshold"},
            }
        ],
        "rubric_criteria": {"secret": "hidden-pass-threshold", "levels": {"3": "专家"}},
        "interviewer_decision": {"action": "ASK"},
        "coach_feedback": "去背题库",
        "other_assessment_scores": [{"level": 3}],
        "requirements": {
            "source": "backend",
            "items": [
                {"id": "req_01", "priority": "must", "text": "幂等"},
                {"id": "req_02", "priority": "must", "text": "任务恢复"},
            ],
        },
        "used_requirement_ids": [],
        "answered_count": 0,
        "remaining_question_budget": 8,
        "max_questions": 8,
        "resume_plain": "梁开新主导过 FastAPI 任务队列，电话 13812345678，邮箱 a@b.com",
        "jd_plain": "必须熟悉幂等与任务恢复",
        "claims": {
            "items": [
                {"id": "claim_01", "kind": "project", "text": "梁开新主导过 FastAPI 任务队列"},
                {"id": "claim_02", "kind": "stack", "text": "熟悉 PostgreSQL 备份"},
            ]
        },
        "used_claim_ids": [],
        "gaps": [{"id": "gap-1", "skill_id": "req_01"}],
    }
    base.update(overrides)
    return base


def _evaluator_snapshot(**overrides):
    base = {
        "id": "ev-1",
        "turn_id": "turn-1",
        "cutoff_turn_no": 1,
        "rubric_snapshot_id": "rubric-1",
        "rubric_criteria": {"lenses": ["constraint"]},
        "skill_ids": ["req_01"],
        "requirements": {"items": [{"id": "req_01", "text": "幂等"}]},
        "answer_text": "我会先固定测试集和生成配置。",
        "answer_message_id": "msg-answer",
        "question_text": "结合 FastAPI 队列说明幂等。",
        "resume_plain": "整份简历不该进评分官",
        "interviewer_decision": {"action": "ASK", "decision_summary": "出下一题"},
        "coach_feedback": "示范答案",
        "transcript": [
            {"turn_no": 1, "kind": "QUESTION", "message_id": "msg-q", "text": "结合 FastAPI 队列说明幂等。"},
            {"turn_no": 1, "kind": "ANSWER", "message_id": "msg-answer", "text": "我会先固定测试集和生成配置。"},
            {"turn_no": 2, "kind": "QUESTION", "message_id": "msg-future", "text": "未来的题"},
        ],
        "reference_hits": [{"id": "chunk-1", "text": "固定测试集后再改检索。"}],
    }
    base.update(overrides)
    return base


def test_interviewer_pack_excludes_rubric_templates_and_scores():
    pack = build_interviewer_context(_interviewer_snapshot())
    blob = json.dumps(pack, ensure_ascii=False)
    assert pack["pack"] == PACK_INTERVIEWER
    assert "templates" not in pack
    assert "rubric" not in pack
    assert "rubric_criteria" not in pack
    assert "interviewer_decision" not in pack
    assert "coach_feedback" not in pack
    assert "hidden-pass-threshold" not in blob
    assert "用户重试提交时如何避免两条答案" not in blob
    assert "13812345678" not in blob
    assert "a@b.com" not in blob
    assert pack["requirements"]["unused"][0]["id"] == "req_01"
    assert "resume" not in pack
    assert pack["claims"]["unused"][0]["id"] == "claim_01"
    assert pack["claims"]["unused"][0]["text"] == "梁开新主导过 FastAPI 任务队列"


def test_interviewer_second_followup_keeps_root_question():
    pack = build_interviewer_context(
        _interviewer_snapshot(
            last_question="失败案例是什么？",
            last_answer="租约过期后旧 Worker 回来了。",
            last_turn_id="turn-3",
            last_skill_ids=["req_01"],
            followup_depth=2,
            thread=[
                {
                    "turn_id": "turn-1",
                    "turn_no": 1,
                    "followup_depth": 0,
                    "question": "结合 FastAPI 队列说明幂等。",
                    "answer": "我们做了幂等键。",
                },
                {
                    "turn_id": "turn-2",
                    "turn_no": 2,
                    "followup_depth": 1,
                    "question": "幂等键存在哪里？",
                    "answer": "存在 Redis。",
                },
                {
                    "turn_id": "turn-3",
                    "turn_no": 3,
                    "followup_depth": 2,
                    "question": "失败案例是什么？",
                    "answer": "租约过期后旧 Worker 回来了。",
                },
            ],
        )
    )
    questions = [row["question"] for row in pack["thread"]]
    assert "结合 FastAPI 队列说明幂等。" in questions
    assert pack["thread"][0]["followup_depth"] == 0
    assert pack["last_turn"]["question"] == "失败案例是什么？"


def test_evaluator_pack_excludes_interviewer_resume_and_future_turns():
    pack = build_evaluator_context(_evaluator_snapshot())
    blob = json.dumps(pack, ensure_ascii=False)
    assert pack["pack"] == PACK_EVALUATOR
    assert "interviewer_decision" not in pack
    assert "coach_feedback" not in pack
    assert "resume" not in pack
    assert "resume_plain" not in pack
    assert "出下一题" not in blob
    assert "整份简历不该进评分官" not in blob
    assert "未来的题" not in blob
    assert pack["prior_thread"] == []
    assert pack["requirement"]["id"] == "req_01"
    assert pack["requirement"]["text"] == "幂等"
    assert any(row["text"] == "我会先固定测试集和生成配置。" for row in pack["transcript_upto_cutoff"])


def test_evaluator_followup_sees_prior_thread_not_other_topics():
    pack = build_evaluator_context(
        _evaluator_snapshot(
            turn_id="turn-3",
            cutoff_turn_no=3,
            question_text="失败案例是什么？",
            answer_text="租约过期后用 fencing 挡住旧写入。",
            answer_message_id="msg-a3",
            thread=[
                {
                    "turn_id": "turn-1",
                    "turn_no": 1,
                    "followup_depth": 0,
                    "question": "结合 FastAPI 队列说明幂等。",
                    "answer": "我们做了幂等键。",
                    "answer_message_id": "msg-a1",
                },
                {
                    "turn_id": "turn-3",
                    "turn_no": 3,
                    "followup_depth": 2,
                    "question": "失败案例是什么？",
                    "answer": "租约过期后用 fencing 挡住旧写入。",
                    "answer_message_id": "msg-a3",
                },
            ],
            transcript=[
                {"turn_no": 1, "kind": "QUESTION", "message_id": "msg-q1", "text": "结合 FastAPI 队列说明幂等。"},
                {"turn_no": 1, "kind": "ANSWER", "message_id": "msg-a1", "text": "我们做了幂等键。"},
                {"turn_no": 2, "kind": "QUESTION", "message_id": "msg-other", "text": "你怎么做 RAG 评测？"},
                {"turn_no": 3, "kind": "QUESTION", "message_id": "msg-q3", "text": "失败案例是什么？"},
                {"turn_no": 3, "kind": "ANSWER", "message_id": "msg-a3", "text": "租约过期后用 fencing 挡住旧写入。"},
            ],
        )
    )
    prior_questions = [row["question"] for row in pack["prior_thread"]]
    assert "结合 FastAPI 队列说明幂等。" in prior_questions
    assert all(row["turn_id"] != "turn-3" for row in pack["prior_thread"])
    blob = json.dumps(pack, ensure_ascii=False)
    assert "你怎么做 RAG 评测？" not in json.dumps(pack["prior_thread"], ensure_ascii=False)
    assert "你怎么做 RAG 评测？" not in blob


def test_coach_pack_keeps_weak_requirements_drops_example_mastery():
    pack = build_coach_context(
        {
            "weak_requirements": [{"id": "req_01", "text": "幂等", "independent_sample_count": 0, "levels": [1]}],
            "confirmed_evidence": [
                {"requirement_id": "req_01", "hint_level": "NONE", "demonstrated": "提到了 fencing。"},
                {"requirement_id": "req_01", "hint_level": "EXAMPLE", "demonstrated": "照着示范抄的测试集。"},
            ],
            "claims": {"items": [{"id": "claim_01", "text": "主导过 FastAPI 任务队列", "probe_hints": ["约束"]}]},
            "rubric_criteria": {"secret": "hidden-pass-threshold"},
            "live_strict_answer": "进行中的严格面试回答不该进教练",
        }
    )
    blob = json.dumps(pack, ensure_ascii=False)
    assert pack["pack"] == PACK_COACH
    assert pack["weak_requirements"][0]["id"] == "req_01"
    assert all(row.get("hint_level") == "NONE" for row in pack["confirmed_evidence"])
    assert "照着示范抄的测试集" not in blob
    assert "hidden-pass-threshold" not in blob
    assert "进行中的严格面试回答不该进教练" not in blob
    assert pack["training_templates"][0]["template_key"] == "ex:req_01"
