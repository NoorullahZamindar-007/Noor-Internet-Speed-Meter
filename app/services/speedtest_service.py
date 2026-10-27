from __future__ import annotations

from collections.abc import Callable
from typing import Any

import speedtest


def _mbps(bits_per_second: float | None) -> float:
    # Mbps = megabits per second; speedtest-cli returns bits per second.
    # A failed measurement can come back as None (or a non-numeric value);
    # that used to fail the whole test with a TypeError instead of
    # reporting a 0 Mbps measurement alongside the other results.
    try:
        value = float(bits_per_second) if bits_per_second is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if value != value or value in (float("inf"), float("-inf")):
        return 0.0
    # A negative bitrate is not a measurement; clamp it instead of
    # reporting a nonsensical negative Mbps to the API and frontend.
    return round(max(value, 0.0) / 1_000_000, 2)


def run_speed_test(progress_callback: Callable[[int], None] | None = None) -> dict[str, Any]:
    def progress(value: int) -> None:
        if progress_callback:
            progress_callback(value)

    try:
        st = speedtest.Speedtest()
        progress(10)
        best_server = st.get_best_server()

        progress(40)
        download_mbps = _mbps(st.download())

        progress(75)
        upload_mbps = _mbps(st.upload())
        # get_best_server() can return None and results.ping can be None
        # when no server answers; both used to crash here with a bare
        # TypeError inside the generic wrapper instead of a usable result.
        # Ping falls back to 0.0 (not None) because the API schema and the
        # frontend both expect a number here.
        best_server = best_server or {}
        ping = st.results.ping
        try:
            ping_ms = round(float(ping), 2) if ping is not None else 0.0
        except (TypeError, ValueError):
            ping_ms = 0.0
        # NaN survives float()/round() above, and a negative ping is
        # not a measurement; either would otherwise reach the API
        # schema (and the frontend's toFixed) as an invalid value.
        if ping_ms != ping_ms or ping_ms < 0:
            ping_ms = 0.0

        server_name = best_server.get("sponsor") or best_server.get("name") or "Unknown"
        server_location = ", ".join(
            part for part in [best_server.get("name"), best_server.get("country")] if part
        )

        return {
            "download_mbps": download_mbps,
            "upload_mbps": upload_mbps,
            "ping_ms": ping_ms,
            "server_name": server_name,
            "server_location": server_location or "Unknown",
        }
    except Exception as exc:
        raise RuntimeError(f"Speed test failed: {exc}") from exc
