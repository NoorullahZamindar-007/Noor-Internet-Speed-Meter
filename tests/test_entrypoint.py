"""Tests for the `python main.py` entry point: reload is opt-in.

Running the app used to enable uvicorn's auto-reloader unconditionally,
so any file change restarted the server (and its in-flight tests). A
plain run now serves stably; reload is only enabled when the
NOOR_SPEEDMETER_RELOAD environment variable says so.
"""

import main as entrypoint


def test_entrypoint_defaults_to_no_reload(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.update(kwargs)
    )
    monkeypatch.delenv("NOOR_SPEEDMETER_RELOAD", raising=False)
    entrypoint.main()
    assert calls["reload"] is False
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8000


def test_entrypoint_reload_opt_in(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.update(kwargs)
    )
    monkeypatch.setenv("NOOR_SPEEDMETER_RELOAD", "1")
    entrypoint.main()
    assert calls["reload"] is True


def test_entrypoint_reload_opt_in_accepts_true(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.update(kwargs)
    )
    monkeypatch.setenv("NOOR_SPEEDMETER_RELOAD", "true")
    entrypoint.main()
    assert calls["reload"] is True


def test_entrypoint_host_and_port_overrides(monkeypatch):
    # Host and port used to be hardcoded, so the documented entry
    # point had no way to run on another port — the README's only
    # workaround was to abandon `python main.py` for uvicorn directly.
    calls = {}
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.update(kwargs)
    )
    monkeypatch.setenv("NOOR_SPEEDMETER_HOST", "0.0.0.0")
    monkeypatch.setenv("NOOR_SPEEDMETER_PORT", "8001")
    entrypoint.main()
    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 8001


def test_entrypoint_non_numeric_port_falls_back_to_8000(monkeypatch):
    # A typo in NOOR_SPEEDMETER_PORT must not crash startup.
    calls = {}
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.update(kwargs)
    )
    monkeypatch.setenv("NOOR_SPEEDMETER_PORT", "not-a-port")
    entrypoint.main()
    assert calls["port"] == 8000


def test_entrypoint_out_of_range_port_falls_back_to_8000(monkeypatch):
    # int() accepts "99999" and "0", but uvicorn raises on a port
    # outside 1-65535, so an out-of-range value used to crash startup
    # instead of falling back like a non-numeric one does.
    calls = {}
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.update(kwargs)
    )
    for bad_port in ("99999", "65536", "0", "-1"):
        monkeypatch.setenv("NOOR_SPEEDMETER_PORT", bad_port)
        entrypoint.main()
        assert calls["port"] == 8000
