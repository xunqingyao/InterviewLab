from domain.requirements import extract_requirements


def test_extracts_must_list_from_paragraph():
    frozen = extract_requirements("AI 应用 / Python 后端。必须熟悉 FastAPI、PostgreSQL、幂等、Agent 评测与失败恢复。")
    texts = [item["text"] for item in frozen["items"]]
    assert frozen["source"] == "backend"
    assert texts == ["必须熟悉 FastAPI、PostgreSQL、幂等、Agent 评测与失败恢复"]
    assert all(item["id"].startswith("req_") for item in frozen["items"])


def test_ignores_empty_and_still_returns_one_item_from_blob():
    frozen = extract_requirements("负责支付对账")
    assert frozen["items"][0]["text"] == "支付对账" or "支付对账" in frozen["items"][0]["text"]
