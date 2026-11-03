from retrieval import embed, rrf_fuse, search_chunks, tsquery_string, tsvector_document


def test_rrf_merges_lists():
    fused = rrf_fuse(["a", "b", "c"], ["c", "a", "d"], limit=3)
    assert fused[0] in {"a", "c"}
    assert "d" in fused or "b" in fused


def test_search_respects_skill_filter():
    chunks = [
        {"id": "1", "text": "固定测试集和基线", "skill_tags": ["rag.evaluation_design"], "vector": embed("固定测试集和基线")},
        {"id": "2", "text": "幂等键与 fencing", "skill_tags": ["backend.idempotency"], "vector": embed("幂等键与 fencing")},
    ]
    hits = search_chunks(chunks, "测试集 基线", "rag.evaluation_design")
    assert hits and hits[0]["id"] == "1"


def test_jieba_keeps_domain_terms():
    doc = tsvector_document("用 PostgreSQL 保存幂等键，避免重复提交。")
    assert "postgresql" in doc
    assert "幂等键" in doc
    query = tsquery_string("PostgreSQL 幂等键")
    assert "postgresql" in query
    assert "&" in query
