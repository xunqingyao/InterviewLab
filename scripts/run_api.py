"""Start the local API using the same root .env as the application."""

import uvicorn

from db.settings import settings


def main() -> None:
    if settings.model_provider == "live" and not settings.model_api_key.strip():
        raise SystemExit("MODEL_PROVIDER=live requires MODEL_API_KEY in .env.")
    print(
        f"InterviewLab: provider={settings.model_provider}, model={settings.model_identifier}, "
        f"inline_worker={settings.inline_worker}",
        flush=True,
    )
    uvicorn.run("api.main:app", host=settings.app_host, port=settings.app_port, reload=True)


if __name__ == "__main__":
    main()
