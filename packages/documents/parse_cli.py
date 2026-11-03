from __future__ import annotations

import json
import sys
from pathlib import Path

from documents import PDF_MAGIC, _limit_child_resources, extract_text


def main() -> None:
    _limit_child_resources()
    if len(sys.argv) < 2:
        json.dump({"ok": False, "error": "missing file path"}, sys.stdout)
        return
    data = Path(sys.argv[1]).read_bytes()
    kind = "pdf" if data.startswith(PDF_MAGIC) else "text"
    try:
        text = extract_text(kind, data)
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc), "code": getattr(exc, "code", "UNREADABLE_RESUME")}, sys.stdout)
        return
    json.dump({"ok": True, "text": text}, sys.stdout)


if __name__ == "__main__":
    main()
