from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SpeedTestResult(BaseModel):
    download_mbps: float
    upload_mbps: float
    ping_ms: float
    server_name: str
    server_location: str


class SpeedTestStartResponse(BaseModel):
    test_id: str
    status: Literal["started"]


class SpeedTestStatus(BaseModel):
    test_id: str
    status: Literal["pending", "running", "completed", "failed"]
    progress: int
    result: SpeedTestResult | None = None
    error: str | None = None
