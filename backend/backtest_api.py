"""Backtest runner API.

Runs the existing backtest scripts as subprocesses and exposes the output
through a small job API. Protected by the BACKTEST_TOKEN shared secret.

    POST /api/backtest/run   {"type": "twelvedata" | "production", "strategy_mode": optional}
                                                                    -> {"job_id": ...}
    GET  /api/backtest/jobs/{job_id}                                -> status + output

The scripts are started WITHOUT MONGO_URL so they never write to the
production database.
"""

import asyncio
import hmac
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

_BASE = Path(__file__).resolve().parent
_SCRIPTS = {
    "twelvedata": "backtest_twelvedata.py",
    "production": "backtest_production_signal.py",
}
_STRATEGY_MODES = {"original", "mean_reversion", "price_action", "macro_filtered", "consensus", "all"}
_TIMEOUT_SECONDS = 20 * 60
_MAX_OUTPUT = 100_000
_MAX_JOBS = 20

_jobs: Dict[str, dict] = {}
_running: Optional[str] = None


class RunRequest(BaseModel):
    type: str
    # Only used by the "production" backtest (sets STRATEGY_MODE for the script).
    strategy_mode: Optional[str] = None


def _check_token(token: Optional[str]) -> None:
    expected = os.environ.get("BACKTEST_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="BACKTEST_TOKEN is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _run_job(job_id: str, script: str, strategy_mode: Optional[str]) -> None:
    global _running
    job = _jobs[job_id]
    env = {k: v for k, v in os.environ.items() if k not in ("MONGO_URL", "BACKTEST_TOKEN")}
    key = os.environ.get("TWELVE_DATA_API_KEY") or os.environ.get("TWELVEDATA_API_KEY") or ""
    env["TWELVEDATA_API_KEY"] = key
    env["TWELVE_DATA_API_KEY"] = key
    env["PYTHONUNBUFFERED"] = "1"
    if strategy_mode:
        env["STRATEGY_MODE"] = strategy_mode
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script,
            cwd=str(_BASE), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_SECONDS)
            job["output"] = out.decode("utf-8", "replace")[-_MAX_OUTPUT:]
            job["exit_code"] = proc.returncode
            job["status"] = "done" if proc.returncode == 0 else "failed"
            if proc.returncode != 0:
                job["error"] = f"{script} exited with code {proc.returncode}"
        except asyncio.TimeoutError:
            proc.kill()
            job["status"] = "failed"
            job["error"] = f"Timed out after {_TIMEOUT_SECONDS // 60} minutes"
    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        job["finished_at"] = time.time()
        _running = None


@router.post("/run")
async def run_backtest(req: RunRequest, x_backtest_token: Optional[str] = Header(default=None)):
    global _running
    _check_token(x_backtest_token)
    script = _SCRIPTS.get(req.type)
    if script is None:
        raise HTTPException(status_code=400, detail=f"Unknown type. Use one of: {', '.join(_SCRIPTS)}")
    mode = req.strategy_mode.strip().lower() if req.strategy_mode else None
    if mode and (req.type != "production" or mode not in _STRATEGY_MODES):
        raise HTTPException(
            status_code=400,
            detail=f"strategy_mode applies to type 'production' only; one of: {', '.join(sorted(_STRATEGY_MODES))}",
        )
    if _running is not None:
        raise HTTPException(status_code=409, detail=f"A backtest is already running (job {_running})")

    for old in sorted(_jobs, key=lambda j: _jobs[j]["started_at"])[:-_MAX_JOBS]:
        _jobs.pop(old, None)

    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "job_id": job_id, "type": req.type, "strategy_mode": mode, "status": "running",
        "started_at": time.time(), "output": "",
    }
    _running = job_id
    asyncio.create_task(_run_job(job_id, script, mode))
    return {"job_id": job_id, "status": "running"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, x_backtest_token: Optional[str] = Header(default=None)):
    _check_token(x_backtest_token)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
