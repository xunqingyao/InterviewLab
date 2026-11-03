from __future__ import annotations

from db.settings import settings
from llm_gateway.fake import FakeGateway, ReplayGateway
from llm_gateway.providers import LiveGateway


def build_gateway():
    fake = FakeGateway()
    provider = settings.model_provider.lower()
    if provider == "replay":
        return ReplayGateway()
    if provider == "live":
        return LiveGateway(fallback=fake)
    return fake
