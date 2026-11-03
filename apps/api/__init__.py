from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import assessments, health, interviews, jobs, me, resumes, skills, training
from db.settings import settings
from domain.errors import DomainError
from observability.logging import configure_logging

configure_logging()


def create_app() -> FastAPI:
    if settings.is_production and settings.interviewlab_dev_auth:
        raise RuntimeError("dev auth cannot be enabled in production")
    app = FastAPI(title="InterviewLab", version="0.1.0")
    if settings.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def csrf_and_origin(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in settings.origin_list and not origin.startswith("http://127.0.0.1") and not origin.startswith("http://localhost"):
                return JSONResponse({"code": "ACTION_NOT_ALLOWED", "message": "origin rejected", "request_id": request.headers.get("x-request-id", ""), "retryable": False}, status_code=403)
            if request.cookies.get("session"):
                header = request.headers.get("x-csrf-token")
                if not header or header != request.cookies.get("csrf_token"):
                    return JSONResponse({"code": "ACTION_NOT_ALLOWED", "message": "csrf token missing or mismatched", "request_id": "", "retryable": False}, status_code=403)
        return await call_next(request)

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return JSONResponse(
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": str(_request.headers.get("x-request-id") or ""),
                "retryable": exc.retryable,
                "details": exc.details,
            },
            status_code=exc.http_status,
        )

    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(resumes.router)
    app.include_router(interviews.router)
    app.include_router(assessments.router)
    app.include_router(jobs.router)
    app.include_router(skills.router)
    app.include_router(training.router)

    @app.on_event("startup")
    async def start_inline_worker() -> None:
        if not settings.inline_worker:
            return

        async def loop() -> None:
            from worker.runner import drain_jobs

            try:
                from agents.checkpoints import setup_checkpoint_tables

                await setup_checkpoint_tables()
            except Exception:
                pass
            while True:
                try:
                    await drain_jobs(6, "api-inline")
                except Exception:
                    pass
                await asyncio.sleep(0.4)

        asyncio.create_task(loop())

    return app


app = create_app()
