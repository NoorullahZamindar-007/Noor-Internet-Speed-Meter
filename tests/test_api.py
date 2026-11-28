import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _clear_speed_tests(monkeypatch, tmp_path):
    # Tests share the module-global speed_tests store; without this a
    # test asserting on initial/empty state can see records left by an
    # earlier test, depending on order or on running a subset.
    import app.main as main_module

    main_module.speed_tests.clear()
    main_module.cancelled_tests.clear()
    # History is saved on every record change: point it at a temp file
    # so test runs never write into the repo (and never read a real
    # history file left by a previous run).
    monkeypatch.setattr(main_module, "HISTORY_FILE", tmp_path / "speedtest_history.json")
    yield
    main_module.speed_tests.clear()
    main_module.cancelled_tests.clear()


client = TestClient(app)  
 
 
def test_health() -> None: 
    response = client.get("/api/health")  

    assert response.status_code == 200 
    assert response.json() == {"status": "ok", "app": "Noor Internet Speed Meter"} 


def test_start_speedtest(monkeypatch) -> None:
    def fake_run_speed_test(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {
            "download_mbps": 50.25,  
            "upload_mbps": 12.75,
            "ping_ms": 23.5, 
            "server_name": "Example Server",
            "server_location": "City, Country",
        } 

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)

    response = client.post("/api/speedtest/start")

    assert response.status_code == 200
    data = response.json()
    assert data["test_id"]   
    assert data["status"] == "started"


def test_start_then_status_completes(monkeypatch) -> None:
    import app.main as main_module

    def fake_run_speed_test(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {
            "download_mbps": 50.25,
            "upload_mbps": 12.75,
            "ping_ms": 23.5,
            "server_name": "Example Server",
            "server_location": "City, Country",
        }

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    # Run the background job inline so the test finishes before we poll.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    test_id = client.post("/api/speedtest/start").json()["test_id"]

    response = client.get(f"/api/speedtest/status/{test_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["progress"] == 100
    assert data["result"]["download_mbps"] == 50.25
    assert data["result"]["upload_mbps"] == 12.75
    assert data["result"]["ping_ms"] == 23.5


def test_failed_speedtest_is_reported_with_its_error(monkeypatch) -> None:
    import app.main as main_module

    # Every API test used a succeeding fake, so the except branch that
    # lands a failed run with its error had zero coverage.
    def fake_run_speed_test(progress_callback=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    # Run the background job inline so the test finishes before we poll.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    test_id = client.post("/api/speedtest/start").json()["test_id"]

    response = client.get(f"/api/speedtest/status/{test_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["error"] == "boom"


def test_hung_speedtest_fails_after_timeout(monkeypatch) -> None:
    import time

    import app.main as main_module

    # Nothing bounded how long run_speed_test could block, so a hung run
    # stayed pending/running forever and permanently consumed an active
    # slot. The job must fail it once the watchdog fires.
    def fake_run_speed_test(progress_callback=None):
        time.sleep(0.5)
        return {"download_mbps": 1.0, "upload_mbps": 1.0, "ping_ms": 1.0}

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    monkeypatch.setattr(main_module, "SPEED_TEST_TIMEOUT_SECONDS", 0.05)
    # Run the background job inline so the test finishes before we poll.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    test_id = client.post("/api/speedtest/start").json()["test_id"]

    response = client.get(f"/api/speedtest/status/{test_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["error"] == "Speed test timed out."


def test_history_endpoint_lists_past_tests_newest_first(monkeypatch) -> None:
    import app.main as main_module

    def fake_run_speed_test(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {
            "download_mbps": 50.25,
            "upload_mbps": 12.75,
            "ping_ms": 23.5,
            "server_name": "Example Server",
            "server_location": "City, Country",
        }

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    # Run the background job inline so the tests finish before we list.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    # Empty history lists nothing, and a failed run is listed too —
    # history is the record of what happened, not only successes.
    assert client.get("/api/speedtest/history").json() == []

    first_id = client.post("/api/speedtest/start").json()["test_id"]
    second_id = client.post("/api/speedtest/start").json()["test_id"]

    response = client.get("/api/speedtest/history")
    assert response.status_code == 200
    data = response.json()
    assert [item["test_id"] for item in data] == [second_id, first_id]
    assert all(item["status"] == "completed" for item in data)
    assert data[0]["result"]["download_mbps"] == 50.25


def test_unknown_speedtest_id_returns_404() -> None: 
    response = client.get("/api/speedtest/status/not-a-real-id") 

    assert response.status_code == 404


def test_home_exposes_progressbar_and_live_status_for_assistive_tech() -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    # The progress track must identify as a progressbar with a valid range so
    # screen readers can report it, not just see a generic div.
    assert 'role="progressbar"' in html
    assert 'aria-valuemin="0"' in html
    assert 'aria-valuemax="100"' in html
    assert 'aria-valuenow="0"' in html
    # Status changes (running/completed/failed) should be announced.
    assert 'id="statusText"' in html
    assert 'aria-live="polite"' in html
    # Past tests are listed on the page, filled in from the history
    # endpoint by the frontend script.
    assert 'id="historyList"' in html
    assert 'id="historyEmpty"' in html
    # The past-tests chart is a canvas (labelled, not read aloud — the
    # list above stays the accessible record).
    assert 'id="historyChart"' in html
    assert 'role="img"' in html
    assert 'id="historyChartLegend"' in html


def test_home_static_assets_serve() -> None:
    # The page links its stylesheet and script from /static: if either
    # file goes missing the page still returns 200 (and the home test
    # above still passes) while rendering unstyled and inert, so assert
    # the assets themselves serve.
    css = client.get("/static/css/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".progress-bar" in css.text
    js = client.get("/static/js/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "loadHistory" in js.text


def test_speedtest_history_is_capped(monkeypatch) -> None:
    import app.main as main_module

    def fake_run_speed_test(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {
            "download_mbps": 50.25,
            "upload_mbps": 12.75,
            "ping_ms": 23.5,
            "server_name": "Example Server",
            "server_location": "City, Country",
        }

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    monkeypatch.setattr(main_module, "MAX_STORED_TESTS", 3)
    # Run jobs inline so they finish; only finished records are evicted.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    ids = [client.post("/api/speedtest/start").json()["test_id"] for _ in range(5)]
    assert len(main_module.speed_tests) == 3
    # The two oldest records were evicted.
    assert ids[0] not in main_module.speed_tests
    assert ids[1] not in main_module.speed_tests
    assert client.get(f"/api/speedtest/status/{ids[0]}").status_code == 404


def test_start_returns_429_when_active_tests_full(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module, "MAX_ACTIVE_TESTS", 2)
    monkeypatch.setattr(main_module, "MAX_STORED_TESTS", 100)
    main_module.speed_tests.clear()
    try:
        main_module.speed_tests["running-1"] = {
            "test_id": "running-1", "status": "running", "progress": 50,
            "result": None, "error": None,
        }
        main_module.speed_tests["pending-1"] = {
            "test_id": "pending-1", "status": "pending", "progress": 0,
            "result": None, "error": None,
        }
        response = client.post("/api/speedtest/start")
        assert response.status_code == 429
    finally:
        main_module.speed_tests.clear()


def test_429_response_includes_retry_after(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module, "MAX_ACTIVE_TESTS", 2)
    monkeypatch.setattr(main_module, "MAX_STORED_TESTS", 100)
    main_module.speed_tests.clear()
    try:
        main_module.speed_tests["running-1"] = {
            "test_id": "running-1", "status": "running", "progress": 50,
            "result": None, "error": None,
        }
        main_module.speed_tests["pending-1"] = {
            "test_id": "pending-1", "status": "pending", "progress": 0,
            "result": None, "error": None,
        }
        response = client.post("/api/speedtest/start")
        assert response.status_code == 429
        # The client should be told when it may retry (RFC 7231 §7.1.3).
        assert response.headers["retry-after"] == "30"
    finally:
        main_module.speed_tests.clear()


def test_history_eviction_preserves_active_tests(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module, "MAX_STORED_TESTS", 3)
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: None)
    main_module.speed_tests.clear()
    try:
        main_module.speed_tests["old-completed"] = {
            "test_id": "old-completed", "status": "completed", "progress": 100,
            "result": None, "error": None,
        }
        main_module.speed_tests["active-running"] = {
            "test_id": "active-running", "status": "running", "progress": 50,
            "result": None, "error": None,
        }
        client.post("/api/speedtest/start")
        client.post("/api/speedtest/start")
        assert len(main_module.speed_tests) == 3
        # The finished record was evicted, but the in-flight test's record
        # survives so its background job can keep updating it.
        assert "old-completed" not in main_module.speed_tests
        assert "active-running" in main_module.speed_tests
        assert client.get("/api/speedtest/status/active-running").status_code == 200
    finally:
        main_module.speed_tests.clear()


def test_cancel_pending_speedtest_returns_cancelled(monkeypatch) -> None:
    import app.main as main_module

    # The job never runs (executor mocked to a no-op): cancelling a
    # pending test must still land it as cancelled, not leave it pending.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: None)

    test_id = client.post("/api/speedtest/start").json()["test_id"]

    response = client.post(f"/api/speedtest/cancel/{test_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "cancelled"
    assert data["error"] == "Cancelled by user."
    assert client.get(f"/api/speedtest/status/{test_id}").json()["status"] == "cancelled"


def test_cancel_unknown_speedtest_returns_404() -> None:
    response = client.post("/api/speedtest/cancel/not-a-real-id")

    assert response.status_code == 404


def test_cancel_completed_speedtest_returns_409(monkeypatch) -> None:
    import app.main as main_module

    def fake_run_speed_test(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {
            "download_mbps": 50.25,
            "upload_mbps": 12.75,
            "ping_ms": 23.5,
            "server_name": "Example Server",
            "server_location": "City, Country",
        }

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    # Run the background job inline so the test finishes before we cancel.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    test_id = client.post("/api/speedtest/start").json()["test_id"]
    assert client.get(f"/api/speedtest/status/{test_id}").json()["status"] == "completed"

    response = client.post(f"/api/speedtest/cancel/{test_id}")

    assert response.status_code == 409
    assert "already completed" in response.json()["detail"]


def test_cancelled_test_is_not_overwritten_by_late_job_update(monkeypatch) -> None:
    import app.main as main_module

    # The background thread cannot be stopped, so a job that starts
    # after the cancel (or was already running) must not resurrect the
    # record as "running" and then "completed".
    def fake_run_speed_test(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {
            "download_mbps": 50.25,
            "upload_mbps": 12.75,
            "ping_ms": 23.5,
            "server_name": "Example Server",
            "server_location": "City, Country",
        }

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: None)

    test_id = client.post("/api/speedtest/start").json()["test_id"]
    assert client.post(f"/api/speedtest/cancel/{test_id}").status_code == 200

    # The queued job finally runs — after the cancel.
    main_module._run_speed_test_job(test_id)

    data = client.get(f"/api/speedtest/status/{test_id}").json()
    assert data["status"] == "cancelled"
    assert data["error"] == "Cancelled by user."
    assert data["result"] is None


def test_evicted_cancelled_test_is_pruned_from_cancelled_set(monkeypatch) -> None:
    import app.main as main_module

    # A cancelled id used to stay in cancelled_tests forever after its
    # record was evicted: one leaked set entry per cancelled test for
    # the life of the server.
    monkeypatch.setattr(main_module, "MAX_STORED_TESTS", 1)
    main_module.speed_tests.clear()
    main_module.cancelled_tests.clear()
    try:
        main_module.speed_tests["cancelled-1"] = {
            "test_id": "cancelled-1", "status": "cancelled", "progress": 50,
            "result": None, "error": "Cancelled by user.",
        }
        main_module.cancelled_tests.add("cancelled-1")
        main_module.speed_tests["completed-1"] = {
            "test_id": "completed-1", "status": "completed", "progress": 100,
            "result": None, "error": None,
        }
        with main_module.speed_tests_lock:
            main_module._evict_old_tests()
        assert "cancelled-1" not in main_module.speed_tests
        assert "cancelled-1" not in main_module.cancelled_tests
        assert "completed-1" in main_module.speed_tests
    finally:
        main_module.speed_tests.clear()
        main_module.cancelled_tests.clear()


def test_update_for_evicted_test_is_ignored_not_an_error() -> None:
    import app.main as main_module

    # A cancelled record can be evicted while its background thread is
    # still running; its next progress update must be a no-op, not a
    # KeyError escaping the worker thread.
    main_module._update_test("no-such-test", status="completed", progress=100)
    assert "no-such-test" not in main_module.speed_tests


def test_timed_out_test_ignores_late_progress_from_orphan_thread(monkeypatch) -> None:
    import time

    import app.main as main_module

    # After a timeout the orphan thread keeps running: its progress
    # callbacks used to creep the failed record's progress up to 100
    # even though the test had already failed.
    def fake_run_speed_test(progress_callback=None):
        time.sleep(0.3)
        if progress_callback:
            progress_callback(100)
        return {"download_mbps": 1.0, "upload_mbps": 1.0, "ping_ms": 1.0}

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    monkeypatch.setattr(main_module, "SPEED_TEST_TIMEOUT_SECONDS", 0.05)
    # Run the background job inline so the test finishes before we poll.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    test_id = client.post("/api/speedtest/start").json()["test_id"]

    data = client.get(f"/api/speedtest/status/{test_id}").json()
    assert data["status"] == "failed"
    assert data["error"] == "Speed test timed out."
    assert data["progress"] == 10

    # Let the orphan thread finish and report its late progress.
    time.sleep(0.5)
    data = client.get(f"/api/speedtest/status/{test_id}").json()
    assert data["status"] == "failed"
    assert data["progress"] == 10
    assert data["result"] is None


def test_queued_job_for_evicted_record_does_not_run(monkeypatch) -> None:
    import app.main as main_module

    # A job queued behind the executor whose record was evicted (only a
    # cancelled record can be evicted while pending) must not run a
    # full speed test whose result has nowhere to go.
    called = []
    monkeypatch.setattr(
        "app.main.run_speed_test",
        lambda progress_callback=None: called.append(True) or {"download_mbps": 1.0},
    )
    main_module._run_speed_test_job("no-such-test")
    assert called == []


def test_completed_history_survives_restart(monkeypatch) -> None:
    import app.main as main_module

    def fake_run_speed_test(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {
            "download_mbps": 50.25,
            "upload_mbps": 12.75,
            "ping_ms": 23.5,
            "server_name": "Example Server",
            "server_location": "City, Country",
        }

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    # Run the background job inline so the test finishes before we poll.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    test_id = client.post("/api/speedtest/start").json()["test_id"]
    assert client.get(f"/api/speedtest/status/{test_id}").json()["status"] == "completed"

    # Simulate a restart: drop the in-memory records and load from disk.
    main_module.speed_tests.clear()
    main_module._load_history()

    response = client.get(f"/api/speedtest/status/{test_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["result"]["download_mbps"] == 50.25


def test_running_test_is_failed_after_restart() -> None:
    import app.main as main_module

    # A test that was still running when the server stopped has no thread
    # behind it any more: it must come back failed with a clear message,
    # not stay "running" forever and hold an active slot.
    main_module.speed_tests["running-1"] = {
        "test_id": "running-1", "status": "running", "progress": 50,
        "result": None, "error": None,
    }
    main_module._save_history()
    main_module.speed_tests.clear()
    main_module._load_history()

    data = client.get("/api/speedtest/status/running-1").json()
    assert data["status"] == "failed"
    assert data["error"] == "Server restarted while this test was running."
    assert data["progress"] == 50


def test_corrupt_or_wrong_shaped_history_loads_empty() -> None:
    import app.main as main_module

    main_module.HISTORY_FILE.write_text("{not json", encoding="utf-8")
    main_module._load_history()
    assert main_module.speed_tests == {}

    main_module.HISTORY_FILE.write_text('{"not": "a list"}', encoding="utf-8")
    main_module._load_history()
    assert main_module.speed_tests == {}

    # Entries that are not objects, or lack a usable id/status, are
    # skipped instead of crashing the load or landing as bad records.
    main_module.HISTORY_FILE.write_text(
        '[{"test_id": "ok-1", "status": "completed", "progress": 100, "result": null, "error": null},'
        ' "junk", {"test_id": 42, "status": "completed"}]',
        encoding="utf-8",
    )
    main_module._load_history()
    assert list(main_module.speed_tests) == ["ok-1"]


def test_history_load_keeps_only_the_newest_records(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module, "MAX_STORED_TESTS", 3)
    for i in range(5):
        main_module.speed_tests[f"test-{i}"] = {
            "test_id": f"test-{i}", "status": "completed", "progress": 100,
            "result": None, "error": None,
        }
    main_module._save_history()
    main_module.speed_tests.clear()
    main_module._load_history()

    assert list(main_module.speed_tests) == ["test-2", "test-3", "test-4"]


def test_start_returns_503_when_executor_shut_down(monkeypatch) -> None:
    import app.main as main_module

    # The pool-shutdown branch of start_speedtest had zero coverage: the
    # record must be dropped, not left "pending" forever holding an
    # active slot and a history slot.
    def _boom(fn, *a, **k):
        raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(main_module.executor, "submit", _boom)

    response = client.post("/api/speedtest/start")

    assert response.status_code == 503
    assert "shutting down" in response.json()["detail"]
    assert main_module.speed_tests == {}


def test_cancel_failed_speedtest_returns_409(monkeypatch) -> None:
    import app.main as main_module

    def fake_run_speed_test(progress_callback=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    # Run the background job inline so the test finishes before we cancel.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    test_id = client.post("/api/speedtest/start").json()["test_id"]
    assert client.get(f"/api/speedtest/status/{test_id}").json()["status"] == "failed"

    response = client.post(f"/api/speedtest/cancel/{test_id}")

    assert response.status_code == 409
    assert "already failed" in response.json()["detail"]


def test_history_load_drops_unusable_result_and_clamps_progress() -> None:
    import app.main as main_module

    # A hand-edited history file can hold a result whose fields are not
    # numbers and a progress outside 0-100: both passed the old load
    # checks, and the bad result then 500d the status endpoint on
    # response-model validation. The bad result must be dropped (the
    # frontend already handles a completed test with no result) and the
    # progress clamped, so one bad record cannot break its endpoint.
    # A bool progress is not an int for this purpose, and numeric
    # strings in a good result still coerce, as through the API.
    main_module.HISTORY_FILE.write_text(
        '[{"test_id": "bad-1", "status": "completed", "progress": 500,'
        ' "result": {"download_mbps": "abc", "upload_mbps": 1.0, "ping_ms": 1.0,'
        ' "server_name": "s", "server_location": "l"}, "error": null},'
        ' {"test_id": "bool-1", "status": "completed", "progress": true,'
        ' "result": null, "error": null},'
        ' {"test_id": "ok-1", "status": "completed", "progress": 100,'
        ' "result": {"download_mbps": "50.25", "upload_mbps": 12.75, "ping_ms": 23.5,'
        ' "server_name": "Example Server", "server_location": "City, Country"}, "error": null}]',
        encoding="utf-8",
    )
    main_module._load_history()

    bad = client.get("/api/speedtest/status/bad-1")
    assert bad.status_code == 200
    assert bad.json()["status"] == "completed"
    assert bad.json()["progress"] == 100
    assert bad.json()["result"] is None

    assert client.get("/api/speedtest/status/bool-1").json()["progress"] == 0

    ok = client.get("/api/speedtest/status/ok-1")
    assert ok.status_code == 200
    assert ok.json()["result"]["download_mbps"] == 50.25


def test_history_load_drops_non_finite_result() -> None:
    import app.main as main_module

    # NaN and Infinity pass pydantic's float check, but they are not
    # valid JSON: the status endpoint would serialize them as bare
    # NaN/Infinity tokens, which the browser's response.json() rejects,
    # so a completed test with a poisoned result looked like a
    # transient poll failure instead of showing its record. The result
    # must be dropped like any other unusable one (the frontend already
    # handles a completed test with no result). json.loads accepts
    # NaN/Infinity literals, so a hand-edited history file can carry
    # them even though the app itself never writes them.
    main_module.HISTORY_FILE.write_text(
        '[{"test_id": "nan-1", "status": "completed", "progress": 100,'
        ' "result": {"download_mbps": NaN, "upload_mbps": 1.0, "ping_ms": 1.0,'
        ' "server_name": "s", "server_location": "l"}, "error": null},'
        ' {"test_id": "inf-1", "status": "completed", "progress": 100,'
        ' "result": {"download_mbps": 1.0, "upload_mbps": Infinity, "ping_ms": 1.0,'
        ' "server_name": "s", "server_location": "l"}, "error": null}]',
        encoding="utf-8",
    )
    main_module._load_history()

    for test_id in ("nan-1", "inf-1"):
        response = client.get(f"/api/speedtest/status/{test_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert response.json()["result"] is None


def test_started_test_carries_start_timestamp(monkeypatch) -> None:
    import app.main as main_module

    # The job never runs (executor mocked to a no-op): the record must
    # still carry when it started, and no finish time yet.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: None)

    test_id = client.post("/api/speedtest/start").json()["test_id"]

    data = client.get(f"/api/speedtest/status/{test_id}").json()
    assert data["started_at"]
    assert data["finished_at"] is None


def test_completed_test_carries_finish_timestamp(monkeypatch) -> None:
    import app.main as main_module

    def fake_run_speed_test(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {
            "download_mbps": 50.25,
            "upload_mbps": 12.75,
            "ping_ms": 23.5,
            "server_name": "Example Server",
            "server_location": "City, Country",
        }

    monkeypatch.setattr("app.main.run_speed_test", fake_run_speed_test)
    # Run the background job inline so the test finishes before we poll.
    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: fn(*a, **k))

    test_id = client.post("/api/speedtest/start").json()["test_id"]

    data = client.get(f"/api/speedtest/status/{test_id}").json()
    assert data["status"] == "completed"
    assert data["started_at"]
    # The finish time is stamped when the record turns completed, so
    # it must not be earlier than the start.
    assert data["finished_at"] and data["finished_at"] >= data["started_at"]


def test_cancelled_test_carries_finish_timestamp(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module.executor, "submit", lambda fn, *a, **k: None)

    test_id = client.post("/api/speedtest/start").json()["test_id"]
    assert client.post(f"/api/speedtest/cancel/{test_id}").status_code == 200

    data = client.get(f"/api/speedtest/status/{test_id}").json()
    assert data["status"] == "cancelled"
    assert data["started_at"]
    assert data["finished_at"] and data["finished_at"] >= data["started_at"]


def test_history_load_keeps_valid_timestamps_and_drops_bad_ones() -> None:
    import app.main as main_module

    # A hand-edited history file can carry a non-string timestamp; the
    # API schema expects a string, so anything else would 500 the
    # status endpoint for that record. Valid strings load as-is.
    main_module.HISTORY_FILE.write_text(
        '[{"test_id": "ts-ok", "status": "completed", "progress": 100,'
        ' "result": null, "error": null,'
        ' "started_at": "2026-11-22T02:00:00+00:00",'
        ' "finished_at": "2026-11-22T02:01:30+00:00"},'
        ' {"test_id": "ts-bad", "status": "completed", "progress": 100,'
        ' "result": null, "error": null,'
        ' "started_at": 12345, "finished_at": ["not", "a", "string"]}]',
        encoding="utf-8",
    )
    main_module._load_history()

    ok = client.get("/api/speedtest/status/ts-ok").json()
    assert ok["started_at"] == "2026-11-22T02:00:00+00:00"
    assert ok["finished_at"] == "2026-11-22T02:01:30+00:00"

    bad = client.get("/api/speedtest/status/ts-bad").json()
    assert bad["started_at"] is None
    assert bad["finished_at"] is None


def test_status_schema_rejects_progress_outside_0_to_100() -> None:
    import pytest

    from app.schemas import SpeedTestStatus
    from pydantic import ValidationError

    # Progress is a percentage: the producers clamp it and history
    # loading clamps it too, but the schema is the API's contract —
    # an out-of-range value must fail validation here instead of
    # reaching a client as "150%".
    with pytest.raises(ValidationError):
        SpeedTestStatus(test_id="x", status="running", progress=150)
    with pytest.raises(ValidationError):
        SpeedTestStatus(test_id="x", status="running", progress=-1)
    assert SpeedTestStatus(test_id="x", status="running", progress=100).progress == 100
    assert SpeedTestStatus(test_id="x", status="pending", progress=0).progress == 0
