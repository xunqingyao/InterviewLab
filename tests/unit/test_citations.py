from domain.enums import MessageKind
from domain.schemas import TextSpan, bind_citation
from domain.text import locate_quote, normalize_text


def test_nfc_and_lf():
    assert normalize_text("A\r\nB") == "A\nB"


def test_unique_quote_offsets():
    text = normalize_text("我没有设置固定测试集，只凭感觉。")
    quote = "我没有设置固定测试集，只凭感觉。"
    loc = locate_quote(text, quote)
    assert loc == (0, len(list(quote)))


def test_ambiguous_quote_rejected():
    text = "好。好。"
    assert locate_quote(text, "好。") is None


def test_bind_citation_emoji_and_cjk():
    text = normalize_text("效果😊提升了，但没有对照组。")
    span = TextSpan(message_id="msg_1", quote="没有对照组")
    bound = bind_citation(span, MessageKind.ANSWER, text)
    assert text[bound.start_char : bound.end_char] == "没有对照组"
