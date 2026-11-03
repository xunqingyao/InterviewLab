from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("REPORT_GRACE_SECONDS", "0")

from db.settings import settings

settings.report_grace_seconds = 0
settings.interviewlab_dev_auth = True
settings.inline_worker = False
settings.heartbeat_seconds = 0
# Unit/integration tests must not spend the live key or depend on the network.
settings.model_provider = "fake"
settings.model_api_key = ""


@pytest.fixture
async def client():
    from api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as ac:
        yield ac
