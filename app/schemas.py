from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    # Progress is a percentage: the producers clamp it, but the schema
    # is the API's contract — an out-of-range value must fail
    # validation here instead of reaching a client as "150%".
    progress: int = Field(ge=0, le=100)
    result: SpeedTestResult | None = None
    error: str | None = None
    # UTC ISO 8601 timestamps: when the test was started, and when it
    # reached a terminal state (completed, failed, or cancelled). The
    # history endpoint used to return records with no time on them at
    # all, so a past result could not be told apart from another run
    # of the same test.
    started_at: str | None = None
    finished_at: str | None = None
