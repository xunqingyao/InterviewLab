"""Run local Agent regressions in a disposable PostgreSQL database, never the app database."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "pgvector/pgvector:pg16"


def loopback_port(binding: str) -> int:
    address, port = binding.strip().rsplit(":", 1)
    if address != "127.0.0.1" or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("test database must use one explicit loopback port")
    return int(port)


def docker_output(*arguments: str) -> str:
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError("Docker operation failed: " + arguments[0])
    return result.stdout.strip()


def main() -> int:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    directory = ROOT / "evals" / "system_runs" / run_id
    directory.mkdir(parents=True)
    manifest = {
        "run_id": run_id,
        "model_mode": "fake",
        "live_requests": 0,
        "database_isolation": "disposable Docker tmpfs, no application database or mounted volumes",
        "stages": [],
        "status": "starting",
        "database_removed": False,
    }
    container = "interviewlab-validation-" + uuid.uuid4().hex[:12]
    created = False
    exit_code = 1
    try:
        if shutil.which("docker") is None:
            raise RuntimeError("Docker is required for the isolated database tests")
        docker_output("info", "--format", "{{.ServerVersion}}")
        docker_output("image", "inspect", IMAGE, "--format", "{{.Id}}")
        password = uuid.uuid4().hex
        docker_output(
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--label",
            "codex.task=interviewlab-agent-validation",
            "--env",
            "POSTGRES_USER=agent_test",
            "--env",
            "POSTGRES_DB=agent_test",
            "--env",
            "POSTGRES_PASSWORD=" + password,
            "--publish",
            "127.0.0.1::5432",
            "--tmpfs",
            "/var/lib/postgresql/data:size=512m",
            IMAGE,
        )
        created = True
        port = loopback_port(docker_output("port", container, "5432/tcp"))
        for _ in range(40):
            ready = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "agent_test", "-d", "agent_test"],
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("temporary PostgreSQL did not become ready")
        with tempfile.TemporaryDirectory(prefix="interviewlab-agent-validation-") as files:
            environment = dict(os.environ)
            environment.update(
                {
                    "DATABASE_URL": f"postgresql+asyncpg://agent_test:{password}@127.0.0.1:{port}/agent_test",
                    "FILE_ROOT": files,
                    "MODEL_PROVIDER": "fake",
                    "MODEL_API_KEY": "",
                }
            )
            stages = [
                ("migrations", [sys.executable, "-m", "alembic", "upgrade", "head"]),
                (
                    "regression",
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "tests",
                        "-q",
                        "--tb=short",
                        "--junitxml=" + str(directory / "regression.xml"),
                    ],
                ),
                (
                    "four_role_smoke",
                    [
                        sys.executable,
                        "-m",
                        "evals.agent_suite",
                        "--mode",
                        "fake",
                        "--output-dir",
                        str(directory / "agent_samples"),
                    ],
                ),
                (
                    "evaluator_smoke",
                    [
                        sys.executable,
                        "-m",
                        "evals.evaluator_quality",
                        "run",
                        "--mode",
                        "fake",
                        "--allow-draft",
                        "--split",
                        "all",
                        "--output-dir",
                        str(directory / "evaluator_samples"),
                    ],
                ),
            ]
            for name, command in stages:
                print("Running " + name, flush=True)
                started = time.monotonic()
                result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
                manifest["stages"].append(
                    {"name": name, "exit_code": result.returncode, "seconds": time.monotonic() - started}
                )
                if result.returncode:
                    break
            exit_code = 0 if all(stage["exit_code"] == 0 for stage in manifest["stages"]) else 1
            xml_path = directory / "regression.xml"
            if xml_path.exists():
                suites = ET.parse(xml_path).getroot().iter("testsuite")
                manifest["regression_counts"] = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
                for suite in suites:
                    for key in manifest["regression_counts"]:
                        manifest["regression_counts"][key] += int(suite.get(key, "0"))
            manifest["status"] = "passed" if exit_code == 0 else "failed"
    except Exception as exc:
        manifest["status"] = "environment_error"
        manifest["error_type"] = type(exc).__name__
        # Error messages are deliberately authored above; no commands or credentials are printed.
        if isinstance(exc, (RuntimeError, ValueError)):
            print(str(exc), file=sys.stderr)
    finally:
        if created:
            removed = subprocess.run(["docker", "rm", "--force", container], capture_output=True, check=False)
            manifest["database_removed"] = removed.returncode == 0
            if not manifest["database_removed"]:
                exit_code = 1
                manifest["status"] = "cleanup_failed"
        (directory / "summary.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Validation summary: " + str(directory / "summary.json"), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
