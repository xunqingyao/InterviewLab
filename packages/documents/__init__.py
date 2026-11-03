from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

from pypdf import PdfReader

from domain.errors import FileTooLarge, UnreadableResume
from domain.text import normalize_text

MAX_PAGES = 10
MAX_CHARS = 30_000
PDF_MAGIC = b"%PDF"
PARSE_TIMEOUT_SECONDS = 15


class FileStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, key: str, data: bytes) -> Path:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def delete(self, key: str) -> None:
        path = self.root / key
        if path.exists():
            path.unlink()


def validate_upload(*, filename: str, content_type: str, data: bytes, max_bytes: int) -> str:
    if len(data) > max_bytes:
        raise FileTooLarge("file exceeds upload limit")
    name = filename.lower()
    if name.endswith(".txt") or content_type.startswith("text/plain"):
        if b"\x00" in data[:1024]:
            raise UnreadableResume("text upload must be UTF-8 text")
        return "text"
    if name.endswith(".pdf") or content_type == "application/pdf":
        if not data.startswith(PDF_MAGIC):
            raise UnreadableResume("PDF signature mismatch")
        return "pdf"
    raise UnreadableResume("only UTF-8 text and digital PDF are supported")


def extract_text(kind: str, data: bytes) -> str:
    if kind == "text":
        try:
            return normalize_text(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise UnreadableResume("text is not valid UTF-8") from exc
    from io import BytesIO

    reader = PdfReader(BytesIO(data))
    if len(reader.pages) > MAX_PAGES:
        raise UnreadableResume("PDF exceeds 10 pages")
    parts = [(page.extract_text() or "") for page in reader.pages]
    text = normalize_text("\n".join(parts))
    if not text.strip():
        raise UnreadableResume("PDF has no extractable text; paste the resume instead")
    if len(text) > MAX_CHARS:
        raise UnreadableResume("extracted text exceeds 30,000 characters")
    return text


def _limit_child_resources() -> None:
    try:
        import resource

        limit = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (PARSE_TIMEOUT_SECONDS, PARSE_TIMEOUT_SECONDS))
    except (ImportError, ValueError, OSError):
        return


async def extract_text_isolated(path: Path, *, timeout: float = PARSE_TIMEOUT_SECONDS) -> str:
    """Parse in a child process so a bad PDF cannot freeze the API/Worker."""
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    packages = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join([packages, pythonpath] if pythonpath else [packages])
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "documents.parse_cli",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise UnreadableResume("PDF parse timed out; paste the resume instead") from None
    if not stdout:
        detail = (stderr or b"").decode("utf-8", errors="replace")[:200]
        raise UnreadableResume(detail or "PDF parser returned no output; paste the resume instead")
    payload = json.loads(stdout.decode("utf-8"))
    if payload.get("ok"):
        return str(payload["text"])
    raise UnreadableResume(str(payload.get("error") or "PDF is unreadable; paste the resume instead"))
