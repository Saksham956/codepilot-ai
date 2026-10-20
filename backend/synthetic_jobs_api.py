"""Opt-in authenticated API for fixed trusted synthetic profiles only."""
import hmac
import os
from pathlib import Path
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from synthetic_execution_policy import public_profiles
from synthetic_job_service import QueueFull, ServiceUnavailable, SyntheticJobService


router = APIRouter(prefix="/synthetic-jobs", tags=["trusted synthetic jobs"])
_creation_lock = threading.Lock()


class SyntheticJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["arithmetic-baseline-v1", "arithmetic-patched-v1", "cancellation-demo-v1"]


async def authenticated(request: Request):
    # No filesystem or Docker access before explicit opt-in and authentication.
    if os.environ.get("CODEPILOT_SYNTHETIC_API_ENABLED") != "1":
        raise HTTPException(503, "Synthetic API is disabled")
    token = os.environ.get("CODEPILOT_SYNTHETIC_API_TOKEN", "")
    if len(token) < 32 or len(token) > 256 or not token.isascii():
        raise HTTPException(503, "Synthetic API credentials are not configured")
    supplied = request.headers.get("authorization", "")
    if not supplied.isascii() or not hmac.compare_digest(supplied, "Bearer " + token):
        raise HTTPException(401, "Synthetic API authentication required", headers={"WWW-Authenticate": "Bearer"})


def service(request):
    with _creation_lock:
        instance = getattr(request.app.state, "synthetic_job_service", None)
        if instance is None:
            root = os.environ.get("CODEPILOT_SYNTHETIC_RUNTIME")
            docker = os.environ.get("CODEPILOT_SYNTHETIC_DOCKER")
            if not root or not docker or not Path(root).is_absolute() or not Path(docker).is_absolute():
                raise HTTPException(503, "Synthetic runtime is not configured")
            try:
                instance = SyntheticJobService(root, docker)
            except Exception:
                raise HTTPException(503, "Synthetic controller is unavailable") from None
            request.app.state.synthetic_job_service = instance
        return instance


@router.get("/profiles", dependencies=[Depends(authenticated)])
async def profiles():
    return {"profiles": public_profiles(), "repository_execution_enabled": False}


@router.post("", status_code=202, dependencies=[Depends(authenticated)])
async def create_job(body: SyntheticJobRequest, request: Request):
    try:
        return service(request).submit(body.profile)
    except QueueFull:
        raise HTTPException(429, "Synthetic queue is full") from None
    except ServiceUnavailable:
        raise HTTPException(503, "Synthetic controller is not ready") from None


@router.get("/{job_id}", dependencies=[Depends(authenticated)])
async def job_status(job_id: str, request: Request):
    _check_id(job_id)
    try:
        return service(request).status(job_id)
    except KeyError:
        raise HTTPException(404, "Synthetic job not found") from None


@router.post("/{job_id}/cancel", status_code=202, dependencies=[Depends(authenticated)])
async def cancel_job(job_id: str, request: Request):
    _check_id(job_id)
    try:
        return service(request).cancel(job_id)
    except KeyError:
        raise HTTPException(404, "Synthetic job not found") from None


def _check_id(job_id):
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(422, "Invalid synthetic job ID")


async def close_synthetic_service(app):
    instance = getattr(app.state, "synthetic_job_service", None)
    if instance is not None:
        # The constructor and shutdown run on the ASGI event-loop thread, which
        # also owns the lifetime SQLite/OS lease. close waits for cleanup.
        instance.close()
