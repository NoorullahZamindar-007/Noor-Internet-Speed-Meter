"""Unit tests for the speedtest service with a stubbed speedtest library."""

import sys
import types


def _install_fake_speedtest(best_server, ping):
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
            return 50_000_000

        def upload(self):
            return 10_000_000

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
