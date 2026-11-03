from __future__ import annotations

import asyncio

from observability.logging import configure_logging
from worker.runner import run_forever


def main() -> None:
    configure_logging()
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
