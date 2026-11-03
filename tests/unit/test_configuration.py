from pathlib import Path

import pytest
from dotenv import dotenv_values

from db.settings import Settings, settings

ROOT = Path(__file__).resolve().parents[2]


def test_example_documents_every_application_setting_without_secrets():
    values = dotenv_values(ROOT / ".env.example")
    assert {name.upper() for name in Settings.model_fields} <= values.keys()
    assert values["MODEL_API_KEY"] == ""
    assert values["DATABASE_URL"].endswith("@127.0.0.1:5432/interviewlab")
    assert values["DOCKER_DATABASE_URL"].endswith("@postgres:5432/interviewlab")
    assert values["WEB_API_TARGET"] == "http://127.0.0.1:8000"


def test_local_launcher_rejects_live_without_key(monkeypatch):
    from scripts.run_api import main

    monkeypatch.setattr(settings, "model_provider", "live")
    monkeypatch.setattr(settings, "model_api_key", "")
    with pytest.raises(SystemExit, match="MODEL_API_KEY"):
        main()


def test_local_launcher_uses_configured_host_and_port(monkeypatch):
    import scripts.run_api as launcher

    monkeypatch.setattr(settings, "model_provider", "fake")
    monkeypatch.setattr(settings, "app_host", "127.0.0.1")
    monkeypatch.setattr(settings, "app_port", 8123)
    calls = []
    monkeypatch.setattr(launcher.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    launcher.main()
    assert calls == [(("api.main:app",), {"host": "127.0.0.1", "port": 8123, "reload": True})]
