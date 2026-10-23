from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
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

executor = ThreadPoolExecutor(max_workers=2)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    yield
    # Release the pool's threads on shutdown so reloads/restarts do not
    # leak threads or delay worker exit.
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title=APP_NAME, lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


speed_tests: dict[str, dict[str, Any]] = {}
speed_tests_lock = Lock()
# The in-memory history is never persisted, so cap it to avoid unbounded
# memory growth on a long-running server.
MAX_STORED_TESTS = 100
# The executor has max_workers=2; this bounds how many tests may be queued or
# running at once so rapid-fire start requests cannot pile up indefinitely.
# The history-cap test posts 5 at a time, so keep the default comfortably above that.
MAX_ACTIVE_TESTS = 8
# Seconds a rejected client should wait before retrying; sent as the
# Retry-After header on 429 responses (RFC 7231 §7.1.3).
RETRY_AFTER_SECONDS = 30
ACTIVE_STATUSES = {"pending", "running"}


def _update_test(test_id: str, **changes: Any) -> None:
    with speed_tests_lock:
        speed_tests[test_id].update(changes)


def _evict_old_tests() -> None:
    """Drop the oldest stored records, never evicting an active test.

    Caller must hold speed_tests_lock. Evicting a pending/running test
    would make the background job's _update_test() raise KeyError on its
    next progress update, so active tests are skipped; if everything left
    is active, eviction stops instead of breaking an in-flight test.
    """
    while len(speed_tests) > MAX_STORED_TESTS:
        for test_id, test in speed_tests.items():
            if test["status"] not in ACTIVE_STATUSES:
                del speed_tests[test_id]
                break
        else:
            break


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
                # Tell the client when it may retry instead of leaving it
                # to poll the endpoint blindly (RFC 7231 §7.1.3).
                headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        test_id = str(uuid4())
        speed_tests[test_id] = {
            "test_id": test_id,
            "status": "pending",
            "progress": 0,
            "result": None,
            "error": None,
        }
        # Dicts preserve insertion order: drop the oldest records first,
        # but never evict an active test (see _evict_old_tests).
        _evict_old_tests()

    executor.submit(_run_speed_test_job, test_id)
    return {"test_id": test_id, "status": "started"}


@app.get("/api/speedtest/status/{test_id}", response_model=SpeedTestStatus)
def speedtest_status(test_id: str) -> dict[str, Any]:
    with speed_tests_lock:
        test = speed_tests.get(test_id)
        if test is None:
            raise HTTPException(status_code=404, detail="Speed test not found")
        return dict(test)
