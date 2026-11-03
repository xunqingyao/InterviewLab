import pytest

from domain.claims import (
    coerce_claims,
    extract_claims,
    ground_extracted_claims,
    used_claim_ids_from_questions,
)


def test_coerce_claims_reads_saved_resume_structure_without_using_field_names():
    claim = {
        "id": "claim_07",
        "project": "模拟面试系统",
        "kind": "project",
        "text": "基于 LangGraph 开发多 Agent 模拟面试系统。",
        "probe_hints": ["约束与取舍", "失败与边界"],
    }
    structured = {
        "summary": "AI 应用开发",
        "projects": ["模拟面试系统"],
        "claims": {"items": [claim], "source": "model"},
    }

    frozen = coerce_claims(structured, claim["text"])

    assert frozen == {"items": [claim], "source": "model"}
    assert all(item["text"] not in {"items", "source"} for item in frozen["items"])


@pytest.mark.parametrize(
    "blob",
    [
        {"claims": {"items": [], "source": "model"}},
        {"claims": {"unexpected": "metadata"}},
        {"claims": "items"},
        {"items": {"unexpected": "metadata"}},
        {"items": "items"},
    ],
)
def test_coerce_claims_invalid_containers_fall_back_to_resume_text(blob):
    resume = "负责基于 LangGraph 的 Agent 开发和出题流程。"
    assert coerce_claims(blob, resume) == extract_claims(resume)


@pytest.mark.parametrize("shape", ["items", "claims", "list"])
def test_coerce_claims_keeps_supported_list_formats(shape):
    raw = ["负责基于 LangGraph 的 Agent 开发和出题流程。"]
    blob = raw if shape == "list" else {shape: raw}
    assert coerce_claims(blob)["items"][0]["text"] == raw[0]


def test_extract_claims_keeps_original_project_line():
    frozen = extract_claims("梁开新主导过 FastAPI 任务队列，电话 13812345678\n熟悉 PostgreSQL 和幂等提交")
    texts = [item["text"] for item in frozen["items"]]
    assert frozen["source"] == "backend"
    assert any("FastAPI" in text and "任务队列" in text for text in texts)
    assert all(item["id"].startswith("claim_") for item in frozen["items"])
    assert "13812345678" not in " ".join(texts)


def test_used_claim_ids_follow_published_question_text():
    claims = extract_claims("主导过 FastAPI 任务队列\n负责 PostgreSQL 备份").get("items") or []
    first = claims[0]
    used = used_claim_ids_from_questions(claims, [f"你的简历写到「{first['text']}」。该岗位要求幂等。"])
    assert first["id"] in used
    assert len(used) == 1
    assert len(first["probe_hints"]) == 5


def test_ground_extracted_claims_drops_invented_text():
    resume = "主导过 FastAPI 任务队列，用 PostgreSQL 做幂等。"
    grounded = ground_extracted_claims(
        resume,
        [
            {"project": "队列", "text": "主导过 FastAPI 任务队列", "probe_hints": ["如何验证"]},
            {"project": "编造", "text": "我还做了从未写过的分布式事务中台", "probe_hints": ["约束与取舍"]},
        ],
    )
    assert len(grounded) == 1
    assert "FastAPI 任务队列" in grounded[0]["text"]
    assert all("分布式事务中台" not in item["text"] for item in grounded)


@pytest.mark.asyncio
async def test_model_extract_uses_resume_spans():
    from agents.extractor import extract_resume_claims

    frozen = await extract_resume_claims(
        "项目一：主导过 FastAPI 任务队列。\n项目二：负责 PostgreSQL 备份。",
        owner_id="u1",
    )
    assert frozen["items"]
    assert all(item["id"].startswith("claim_") for item in frozen["items"])
    assert any("FastAPI" in item["text"] for item in frozen["items"])
    assert all(len(item["probe_hints"]) == 5 for item in frozen["items"])
