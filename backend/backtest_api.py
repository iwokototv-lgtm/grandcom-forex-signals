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
import re
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
_STRATEGY_MODES = {"original", "mean_reversion", "price_action", "macro_filtered", "consensus", "trend_breakout", "ma_adx", "pullback", "tb_union", "tb_confirmed", "all"}
_TIMEOUT_SECONDS = 20 * 60          # default; longer runs get more, see _timeout_for()
_MAX_CANDLES = 6000
# Experiment knobs a caller may set for the "production" backtest. Values must
# be plain numbers or true/false, so nothing else can reach the subprocess.
_ALLOWED_PARAMS = {
    "BT_ATR_SL", "BT_ATR_TP1", "BT_ATR_TP2", "BT_ATR_TP3", "BT_BE_R",
    "BT_ONE_AT_A_TIME", "BT_CONF_THRESHOLD", "HONEST_CONFIDENCE",
    "BT_BREAKOUT_N", "BT_EMA", "BT_BREAKOUT_BUFFER_ATR", "BT_TREND_EMA", "BT_TRAIL_ATR", "BT_VOL_MIN", "MIN_MTF_ALIGNMENT_PCT", "MIN_SMC_SCORE", "CONSENSUS_THRESHOLD",
}
_PARAM_VALUE = re.compile(r"^(true|false|\d{1,4}(\.\d{1,4})?)$")                 # about 4 years of 4H candles
_MAX_OUTPUT = 100_000
_MAX_JOBS = 20

_jobs: Dict[str, dict] = {}
_running: Optional[str] = None


class RunRequest(BaseModel):
    type: str
    # Only used by the "production" backtest (sets STRATEGY_MODE for the script).
    strategy_mode: Optional[str] = None
    # Optional, "production" only: how many 4H candles to test (default 1000 =
    # about 8 months; max 6000) and how many equal time windows to report.
    candles: Optional[int] = None
    folds: Optional[int] = None
    # Optional, "production" only: experiment knobs, see _ALLOWED_PARAMS.
    params: Optional[Dict[str, str]] = None


def _timeout_for(candles: Optional[int]) -> int:
    if not candles or candles <= 1000:
        return _TIMEOUT_SECONDS
    return _TIMEOUT_SECONDS + int(candles / 1000 * 10 * 60)


def _check_token(token: Optional[str]) -> None:
    expected = os.environ.get("BACKTEST_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="BACKTEST_TOKEN is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _run_job(job_id: str, script: str, strategy_mode: Optional[str],
                   extra_env: Dict[str, str], timeout: int) -> None:
    global _running
    job = _jobs[job_id]
    env = {k: v for k, v in os.environ.items() if k not in ("MONGO_URL", "BACKTEST_TOKEN")}
    key = os.environ.get("TWELVE_DATA_API_KEY") or os.environ.get("TWELVEDATA_API_KEY") or ""
    env["TWELVEDATA_API_KEY"] = key
    env["TWELVE_DATA_API_KEY"] = key
    env["PYTHONUNBUFFERED"] = "1"
    if strategy_mode:
        env["STRATEGY_MODE"] = strategy_mode
    env.update(extra_env)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script,
            cwd=str(_BASE), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            job["output"] = out.decode("utf-8", "replace")[-_MAX_OUTPUT:]
            job["exit_code"] = proc.returncode
            job["status"] = "done" if proc.returncode == 0 else "failed"
            if proc.returncode != 0:
                job["error"] = f"{script} exited with code {proc.returncode}"
        except asyncio.TimeoutError:
            proc.kill()
            job["status"] = "failed"
            job["error"] = f"Timed out after {timeout // 60} minutes"
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
    extra_env: Dict[str, str] = {}
    if req.candles is not None or req.folds is not None:
        if req.type != "production":
            raise HTTPException(status_code=400, detail="candles/folds apply to type 'production' only")
        if req.candles is not None:
            if not 300 <= req.candles <= _MAX_CANDLES:
                raise HTTPException(status_code=400, detail=f"candles must be between 300 and {_MAX_CANDLES}")
            extra_env["BACKTEST_CANDLES"] = str(req.candles)
        if req.folds is not None:
            if not 0 <= req.folds <= 12:
                raise HTTPException(status_code=400, detail="folds must be between 0 and 12")
            extra_env["BACKTEST_FOLDS"] = str(req.folds)
    if req.params:
        if req.type != "production":
            raise HTTPException(status_code=400, detail="params apply to type 'production' only")
        for k, v in req.params.items():
            if k not in _ALLOWED_PARAMS:
                raise HTTPException(status_code=400, detail=f"Unknown param {k}. Allowed: {', '.join(sorted(_ALLOWED_PARAMS))}")
            if not _PARAM_VALUE.match(str(v).strip().lower()):
                raise HTTPException(status_code=400, detail=f"Param {k} must be a number or true/false")
            extra_env[k] = str(v).strip().lower()
    if _running is not None:
        raise HTTPException(status_code=409, detail=f"A backtest is already running (job {_running})")

    for old in sorted(_jobs, key=lambda j: _jobs[j]["started_at"])[:-_MAX_JOBS]:
        _jobs.pop(old, None)

    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "job_id": job_id, "type": req.type, "strategy_mode": mode, "candles": req.candles, "folds": req.folds, "params": req.params,
        "status": "running",
        "started_at": time.time(), "output": "",
    }
    _running = job_id
    asyncio.create_task(_run_job(job_id, script, mode, extra_env, _timeout_for(req.candles)))
    return {"job_id": job_id, "status": "running"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, x_backtest_token: Optional[str] = Header(default=None)):
    _check_token(x_backtest_token)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
