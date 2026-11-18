from __future__ import annotations

import json
import os
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
from pydantic import ValidationError
from starlette.requests import Request

from app.schemas import SpeedTestResult, SpeedTestStartResponse, SpeedTestStatus
from app.services.speedtest_service import run_speed_test

APP_NAME = "Noor Internet Speed Meter"
BASE_DIR = Path(__file__).resolve().parent
# Completed results survive a restart: without this, a restart wiped the
# in-memory store and the frontend's poll for a finished test 404d with
# the misleading "server may have restarted" message. Gitignored: it is
# local runtime state, not source.
HISTORY_FILE = BASE_DIR / "speedtest_history.json"

executor = ThreadPoolExecutor(max_workers=2)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _load_history()
    yield
    # Release the pool's threads on shutdown so reloads/restarts do not
    # leak threads or delay worker exit.
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title=APP_NAME, lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


speed_tests: dict[str, dict[str, Any]] = {}
speed_tests_lock = Lock()
# Tests the user cancelled: the background job may still be running (its
# thread cannot be stopped), so its later _update_test() calls must be
# ignored instead of overwriting the cancelled record with "running" or
# "completed". The id is dropped again when the record is evicted (see
# _evict_old_tests): without that, every cancelled test left its id in
# this set forever, a slow leak on a long-running server.
cancelled_tests: set[str] = set()
# The in-memory history is never persisted, so cap it to avoid unbounded
# memory growth on a long-running server.
MAX_STORED_TESTS = 100
# The executor has max_workers=2; this bounds how many tests may be queued or
# running at once so rapid-fire start requests cannot pile up indefinitely.
# Kept at 4 (2 running + 2 queued): with 8 active tests the last one could
# wait behind ~3 sequential batches of 60-120 s tests plus its own run,
# reaching the frontend's 10-minute poll deadline even though nothing
# failed. At 4 the worst case is about 6 minutes including the run.
MAX_ACTIVE_TESTS = 4
# Seconds a rejected client should wait before retrying; sent as the
# Retry-After header on 429 responses (RFC 7231 §7.1.3).
RETRY_AFTER_SECONDS = 30
ACTIVE_STATUSES = {"pending", "running"}
# A record in one of these states is finished for good: no later update
# may change it (see _update_test).
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
# A speed test normally takes 60-120 s and the frontend stops polling
# after 10 minutes, but nothing bounded the server-side run: a hung test
# stayed pending/running forever, and active records are never evicted,
# so it permanently consumed one of the MAX_ACTIVE_TESTS slots. 300 s
# sits inside the frontend's 10-minute poll deadline.
SPEED_TEST_TIMEOUT_SECONDS = 300


def _save_history() -> None:
    """Persist the in-memory records so a restart does not wipe history.

    Caller must hold speed_tests_lock. Best-effort and atomic (temp file
    + os.replace): a read-only folder or a crash mid-save must never break
    the request that triggered the save, and must never leave a
    half-written file behind. Records are JSON-plain (str/int/dict/None),
    so they serialize as-is.
    """
    payload = json.dumps(list(speed_tests.values()), indent=2)
    tmp = HISTORY_FILE.with_name(f".{HISTORY_FILE.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, HISTORY_FILE)
    except OSError:
        pass
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _sanitize_progress(value: Any) -> int:
    """Clamp a history-file progress value to a 0-100 percentage.

    The history file is hand-editable: a value outside the range, a
    float, or a bool (a bool is an int subclass) used to load as-is and
    reach the API even though progress is a percentage.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(100, value))


def _sanitize_result(value: Any) -> dict[str, Any] | None:
    """Return a history-file result only if it fits the API schema.

    A result dict with a non-numeric field passed the old "is it a
    dict" check, loaded fine, and then 500d the status endpoint when
    the response model rejected it. Validate it like the API does and
    drop it when it does not fit, so one bad record cannot break its
    own endpoint.
    """
    if not isinstance(value, dict):
        return None
    try:
        return SpeedTestResult(**value).model_dump()
    except ValidationError:
        return None


def _load_history() -> None:
    """Restore persisted records at startup; in-flight tests cannot resume.

    A record that was pending/running when the server stopped has no
    thread behind it any more, so it is marked failed with a clear message
    instead of staying active forever (active records are never evicted
    and count toward MAX_ACTIVE_TESTS). A missing, corrupt, or
    wrong-shaped file falls back to an empty history.
    """
    try:
        raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, list):
        return
    with speed_tests_lock:
        speed_tests.clear()
        for item in raw[-MAX_STORED_TESTS:]:
            if not isinstance(item, dict):
                continue
            test_id = item.get("test_id")
            status = item.get("status")
            if not isinstance(test_id, str) or status not in {
                "pending",
                "running",
                "completed",
                "failed",
                "cancelled",
            }:
                continue
            record: dict[str, Any] = {
                "test_id": test_id,
                "status": status,
                # A hand-edited history file can carry a progress outside
                # 0-100 (or a bool/float, which is not an int): it used to
                # load as-is and reach the API, even though progress is
                # a percentage. Clamp it; anything unusable becomes 0.
                "progress": _sanitize_progress(item.get("progress")),
                # Same class of problem for the result: a dict with a
                # non-numeric field passed the old "is it a dict" check,
                # loaded fine, and then 500d the status endpoint when
                # the response model rejected it. Validate it like the
                # API does and drop it when it does not fit, so one
                # bad record cannot break its own endpoint — the
                # frontend already handles a completed test with no
                # result. Numeric strings still coerce, as they do
                # through the API.
                "result": _sanitize_result(item.get("result")),
                "error": item.get("error")
                if isinstance(item.get("error"), str)
                else None,
            }
            if status in ACTIVE_STATUSES:
                record["status"] = "failed"
                record["error"] = "Server restarted while this test was running."
            speed_tests[test_id] = record


def _update_test(test_id: str, **changes: Any) -> None:
    with speed_tests_lock:
        # A cancelled test stays cancelled: the background job's thread
        # cannot be stopped, so without this its next progress update
        # would resurrect the record as "running" (and eventually
        # "completed") after the user cancelled it.
        if test_id in cancelled_tests:
            return
        # The record may have been evicted after the job's last check
        # (a cancelled record is inactive, so it is evictable while its
        # thread is still running): ignore the update instead of
        # raising KeyError out of the background thread.
        test = speed_tests.get(test_id)
        if test is None:
            return
        # A finished record must stay finished: after a timeout the
        # orphan thread keeps running and its progress callbacks used
        # to creep a failed record's progress up (to 100) even though
        # the test had already failed — the stored record then
        # disagreed with what the frontend had shown. The same guard
        # covers a completed record's late callbacks and doubles the
        # cancelled-tests check above.
        if test["status"] in TERMINAL_STATUSES:
            return
        test.update(changes)
        _save_history()


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
                # The record is gone, so its cancelled marker must go
                # too: _update_test() uses this set to ignore a
                # cancelled job's late updates, and once the record is
                # evicted there is nothing left to protect — keeping
                # the id would leak one entry per cancelled test for
                # the life of the server.
                cancelled_tests.discard(test_id)
                break
        else:
            break


def _run_speed_test_job(test_id: str) -> None:
    # Cancelled while still queued behind the executor's two workers:
    # do not start a speed test nobody is waiting for. The same goes
    # for a record that was evicted while queued (only a cancelled
    # record can be evicted while its job is still pending): without
    # this, pruning the cancelled marker on eviction would let the job
    # run a full test whose result has nowhere to go.
    with speed_tests_lock:
        if test_id in cancelled_tests or test_id not in speed_tests:
            return
    try:
        _update_test(test_id, status="running", progress=10)
        # Run the test on its own single-worker pool so a hung run can be
        # abandoned: future.result(timeout=...) only returns control to us,
        # it does not stop the thread, so the pool is shut down without
        # waiting and the orphan thread finishes on its own.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                run_speed_test,
                progress_callback=lambda progress: _update_test(test_id, progress=progress),
            )
            try:
                result = future.result(timeout=SPEED_TEST_TIMEOUT_SECONDS)
            except TimeoutError:
                _update_test(test_id, status="failed", error="Speed test timed out.")
                return
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
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
        _save_history()

    try:
        executor.submit(_run_speed_test_job, test_id)
    except RuntimeError:
        # Pool shut down (e.g. during reload/restart): drop the record
        # instead of leaving it "pending" forever — pending counts toward
        # MAX_ACTIVE_TESTS and is never evicted, so it would permanently
        # consume one active slot and one history slot.
        with speed_tests_lock:
            speed_tests.pop(test_id, None)
            _save_history()
        raise HTTPException(
            status_code=503,
            detail="Speed test service is shutting down. Please try again.",
        )
    return {"test_id": test_id, "status": "started"}


@app.post("/api/speedtest/cancel/{test_id}", response_model=SpeedTestStatus)
def cancel_speedtest(test_id: str) -> dict[str, Any]:
    # A test takes 60-120 s and the frontend polls for up to 10 minutes;
    # until now there was no way to stop one short of its timeout. The
    # background thread cannot be killed, so cancelling marks the record
    # and the job's later updates are ignored (see _update_test); a
    # queued job never starts (see _run_speed_test_job). A finished test
    # cannot be cancelled: 409, not a silent no-op, so the client knows
    # the result it already has stands.
    with speed_tests_lock:
        test = speed_tests.get(test_id)
        if test is None:
            raise HTTPException(status_code=404, detail="Speed test not found")
        if test["status"] not in ACTIVE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Speed test is already {test['status']} and cannot be cancelled.",
            )
        cancelled_tests.add(test_id)
        test.update(status="cancelled", progress=test["progress"], error="Cancelled by user.")
        _save_history()
        return dict(test)


@app.get("/api/speedtest/status/{test_id}", response_model=SpeedTestStatus)
def speedtest_status(test_id: str) -> dict[str, Any]:
    with speed_tests_lock:
        test = speed_tests.get(test_id)
        if test is None:
            raise HTTPException(status_code=404, detail="Speed test not found")
        return dict(test)
