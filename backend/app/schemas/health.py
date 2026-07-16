"""Health-check response contracts. See app/api/routes/health.py."""

from pydantic import BaseModel


class LivenessResponse(BaseModel):
    status: str = "alive"


class ComponentCheck(BaseModel):
    """One readiness sub-check. `status` is 'ok' or 'error'; `detail` is a
    short, safe (no secrets, no stack traces) human-readable note."""

    name: str
    status: str
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: str  # "ready" | "not_ready"
    checks: list[ComponentCheck]
