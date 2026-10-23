from fastapi.testclient import TestClient

from app.main import app


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


def test_unknown_speedtest_id_returns_404() -> None: 
    response = client.get("/api/speedtest/status/not-a-real-id") 

    assert response.status_code == 404


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
