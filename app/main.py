from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.schemas import SpeedTestStartResponse, SpeedTestStatus
from app.services.speedtest_service import run_speed_test

APP_NAME = "Noor Internet Speed Meter"
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

executor = ThreadPoolExecutor(max_workers=2)
speed_tests: dict[str, dict[str, Any]] = {}
speed_tests_lock = Lock()
# The in-memory history is never persisted, so cap it to avoid unbounded
# memory growth on a long-running server.
MAX_STORED_TESTS = 100
# The executor has max_workers=2; this bounds how many tests may be queued or
# running at once so rapid-fire start requests cannot pile up indefinitely.
# The history-cap test posts 5 at a time, so keep the default comfortably above that.
MAX_ACTIVE_TESTS = 8
ACTIVE_STATUSES = {"pending", "running"}


def _update_test(test_id: str, **changes: Any) -> None:
    with speed_tests_lock:
        speed_tests[test_id].update(changes)


def _run_speed_test_job(test_id: str) -> None:
    try:
        _update_test(test_id, status="running", progress=10)
        result = run_speed_test(
            progress_callback=lambda progress: _update_test(test_id, progress=progress)
        )
        _update_test(
            test_id,
            status="completed",
            progress=100,
            result=result,
            error=None,
        )
    except Exception as exc:
        _update_test(test_id, status="failed", error=str(exc))


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": APP_NAME},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": APP_NAME}


@app.post("/api/speedtest/start", response_model=SpeedTestStartResponse)
def start_speedtest() -> dict[str, str]:
    with speed_tests_lock:
        active = sum(
            1 for test in speed_tests.values() if test["status"] in ACTIVE_STATUSES
        )
        if active >= MAX_ACTIVE_TESTS:
            raise HTTPException(
                status_code=429,
                detail="Too many active speed tests. Wait for one to finish and retry.",
            )
        test_id = str(uuid4())
        speed_tests[test_id] = {
            "test_id": test_id,
            "status": "pending",
            "progress": 0,
            "result": None,
            "error": None,
        }
        # Dicts preserve insertion order: drop the oldest records first.
        while len(speed_tests) > MAX_STORED_TESTS:
            speed_tests.pop(next(iter(speed_tests)))

    executor.submit(_run_speed_test_job, test_id)
    return {"test_id": test_id, "status": "started"}


@app.get("/api/speedtest/status/{test_id}", response_model=SpeedTestStatus)
def speedtest_status(test_id: str) -> dict[str, Any]:
    with speed_tests_lock:
        test = speed_tests.get(test_id)
        if test is None:
            raise HTTPException(status_code=404, detail="Speed test not found")
        return dict(test)
