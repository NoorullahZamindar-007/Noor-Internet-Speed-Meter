from __future__ import annotations

from collections.abc import Callable
from typing import Any

import speedtest


def _mbps(bits_per_second: float) -> float:
    return round(bits_per_second / 1024 / 1024, 2)


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
        ping_ms = round(float(st.results.ping), 2)

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
