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
