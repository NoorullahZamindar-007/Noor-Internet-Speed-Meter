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
