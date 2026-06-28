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
    test_id = str(uuid4())
    with speed_tests_lock:
        speed_tests[test_id] = {
            "test_id": test_id,
            "status": "pending",
            "progress": 0,
            "result": None,
            "error": None,
        }

    executor.submit(_run_speed_test_job, test_id)
    return {"test_id": test_id, "status": "started"}


@app.get("/api/speedtest/status/{test_id}", response_model=SpeedTestStatus)
def speedtest_status(test_id: str) -> dict[str, Any]:
    with speed_tests_lock:
        test = speed_tests.get(test_id)
        if test is None:
            raise HTTPException(status_code=404, detail="Speed test not found")
        return dict(test)
