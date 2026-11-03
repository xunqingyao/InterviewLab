from observability.logging import redact


def test_redact_strips_secrets_and_answers():
    payload = redact(
        {
            "api_key": "sk-secret",
            "answer_text": "这是用户的完整答案，不应该出现在日志里。",
            "nested": {"token": "abc", "ok": True},
            "short": "hi",
        }
    )
    assert payload["api_key"] == "[redacted]"
    assert payload["answer_text"] == "[redacted]"
    assert payload["nested"]["token"] == "[redacted]"
    assert payload["nested"]["ok"] is True
    assert payload["short"] == "hi"
