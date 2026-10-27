"""Unit tests for the speedtest service with a stubbed speedtest library."""

import sys
import types


def _install_fake_speedtest(best_server, ping, download=50_000_000, upload=10_000_000):
    """Install a fake ``speedtest`` module, then import the service fresh."""
    fake = types.ModuleType("speedtest")

    class FakeResults:
        pass

    results = FakeResults()
    results.ping = ping

    class FakeSpeedtest:
        def __init__(self):
            self.results = results

        def get_best_server(self):
            return best_server

        def download(self):
            return download

        def upload(self):
            return upload

    fake.Speedtest = FakeSpeedtest
    sys.modules["speedtest"] = fake
    sys.modules.pop("app.services.speedtest_service", None)
    from app.services.speedtest_service import run_speed_test

    return run_speed_test()


def test_missing_server_and_ping_fall_back_to_unknown_and_zero():
    result = _install_fake_speedtest(best_server=None, ping=None)

    assert result["server_name"] == "Unknown"
    assert result["server_location"] == "Unknown"
    assert result["ping_ms"] == 0.0
    assert result["download_mbps"] == 50.0
    assert result["upload_mbps"] == 10.0


def test_missing_or_bad_speeds_fall_back_to_zero():
    result = _install_fake_speedtest(
        best_server={"sponsor": "Test ISP", "name": "Kabul", "country": "AF"},
        ping="not-a-number",
        download=None,
        upload=float("inf"),
    )

    assert result["download_mbps"] == 0.0
    assert result["upload_mbps"] == 0.0
    assert result["ping_ms"] == 0.0
    assert result["server_name"] == "Test ISP"


def test_negative_and_non_finite_measurements_clamp_to_zero():
    # Negative bitrates/ping are not measurements, and NaN survives
    # float()/round() — all three used to reach the API as-is.
    result = _install_fake_speedtest(
        best_server={"sponsor": "Test ISP", "name": "Kabul", "country": "AF"},
        ping=float("nan"),
        download=-10_000_000,
        upload=-5_000_000,
    )

    assert result["download_mbps"] == 0.0
    assert result["upload_mbps"] == 0.0
    assert result["ping_ms"] == 0.0

    result = _install_fake_speedtest(
        best_server={"sponsor": "Test ISP", "name": "Kabul", "country": "AF"},
        ping=-12.5,
    )

    assert result["ping_ms"] == 0.0
    assert result["download_mbps"] == 50.0
