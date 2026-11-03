from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from documents import extract_text, extract_text_isolated, validate_upload
from domain.errors import FileTooLarge, UnreadableResume


def test_rejects_pdf_magic_mismatch():
    with pytest.raises(UnreadableResume):
        validate_upload(filename="resume.pdf", content_type="application/pdf", data=b"not-a-pdf", max_bytes=1000)


def test_rejects_oversize():
    with pytest.raises(FileTooLarge):
        validate_upload(filename="resume.txt", content_type="text/plain", data=b"x" * 20, max_bytes=10)


def test_blank_pdf_has_no_text():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    with pytest.raises(UnreadableResume):
        extract_text("pdf", buf.getvalue())


@pytest.mark.asyncio
async def test_isolated_parser_reads_text_file(tmp_path: Path):
    path = tmp_path / "resume.txt"
    path.write_text("梁开新，Python 后端。", encoding="utf-8")
    text = await extract_text_isolated(path)
    assert "梁开新" in text
