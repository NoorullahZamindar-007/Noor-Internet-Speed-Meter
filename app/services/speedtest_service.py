from __future__ import annotations

from collections.abc import Callable
from typing import Any

import speedtest


def _mbps(bits_per_second: float) -> float:
    # Mbps = megabits per second; speedtest-cli returns bits per second.
    return round(bits_per_second / 1_000_000, 2)


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
        ping_ms = round(float(ping), 2) if ping is not None else 0.0

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
