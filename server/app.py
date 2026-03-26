"""
FastAPI server for the DevOps Incident Responder OpenEnv environment.

Endpoints
─────────
POST  /reset          — start or restart an episode
POST  /step           — execute one action
GET   /state          — current episode state
GET   /tasks          — list all tasks with action schema
POST  /grader         — score the current episode
POST  /baseline       — run the built-in baseline agent and return scores
GET   /health         — liveness probe
GET   /               — environment info
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Make sure sibling modules are importable when run from container root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from environment import DevOpsEnv, TASKS
from models import (
    Action,
    GraderResult,
    ResetResult,
    State,
    StepResult,
    TaskInfo,
)
from tasks import run_grader, TASK_CATALOGUE
from scripts import BASELINE_SCRIPTS as _BASELINE_SCRIPTS

# ──────────────────────────── State store ────────────────────────────
# We maintain one environment instance per task_id so the server can
# handle sequential calls from a single agent.  For a production
# multi-tenant deployment you would shard by session/episode UUID.

_envs: Dict[str, DevOpsEnv] = {}


def _get_env(task_id: str) -> DevOpsEnv:
    if task_id not in TASKS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown task_id '{task_id}'. Valid: {list(TASKS.keys())}",
        )
    if task_id not in _envs:
        _envs[task_id] = DevOpsEnv(task_id=task_id)
    return _envs[task_id]


# ──────────────────────────── App setup ────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm one env per task at startup
    for tid in TASKS:
        _envs[tid] = DevOpsEnv(task_id=tid)
    yield
    _envs.clear()


app = FastAPI(
    title="DevOps Incident Responder",
    description=(
        "An OpenEnv environment where an AI agent debugs a broken microservice "
        "cluster: a database password was rotated by a cron job, invalidating "
        "credentials in auth-service and user-service and causing cascading "
        "failures across the cluster. "
        "The agent must diagnose and remediate using simulated bash commands."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────── Request bodies ────────────────────────────

class ResetRequest(BaseModel):
    task_id: str = "task3_remediation"


class StepRequest(BaseModel):
    task_id: str = "task3_remediation"
    action: Action


class GraderRequest(BaseModel):
    task_id: str = "task3_remediation"


# ──────────────────────────── Endpoints ────────────────────────────

@app.get("/", response_model=Dict[str, Any])
async def root():
    return {
        "name": "devops-incident-responder",
        "version": "1.0.0",
        "description": (
            "OpenEnv environment: AI agent debugs a microservice cluster "
            "broken by a database password rotation."
        ),
        "tasks": list(TASKS.keys()),
        "endpoints": ["/reset", "/step", "/state", "/tasks", "/grader", "/baseline", "/health"],
        "framework": "openenv-core",
    }


@app.get("/health")
async def health():
    return {"status": "ok", "envs_loaded": len(_envs)}


# ── /reset ──

@app.post("/reset", response_model=ResetResult)
async def reset(body: ResetRequest):
    env = _get_env(body.task_id)
    return env.reset()


# ── /step ──

@app.post("/step", response_model=StepResult)
async def step(body: StepRequest):
    env = _get_env(body.task_id)
    return env.step(body.action)


# ── /state ──

@app.get("/state", response_model=State)
async def state(task_id: str = Query("task3_remediation", description="Task identifier")):
    env = _get_env(task_id)
    return env.state()


# ── /tasks ──

@app.get("/tasks", response_model=Dict[str, TaskInfo])
async def list_tasks():
    """
    Returns all available tasks and their action schema.
    Required by the hackathon spec.
    """
    return TASK_CATALOGUE


# ── /grader ──

@app.post("/grader", response_model=GraderResult)
async def grader(body: GraderRequest):
    """
    Score the current episode for the given task.
    Can be called at any time; reflects progress so far.
    """
    env = _get_env(body.task_id)
    return run_grader(env)


# ── /baseline ──

class BaselineResponse(BaseModel):
    results: Dict[str, Any]
    average_score: float
    summary: str


@app.post("/baseline", response_model=BaselineResponse)
async def baseline_endpoint():
    """
    Trigger the built-in heuristic baseline agent against all 3 tasks.
    Returns scores without requiring an external API key.
    Uses a deterministic rule-based agent for reproducibility.
    """
    results: Dict[str, Any] = {}

    for task_id, task_cfg in TASKS.items():
        env = DevOpsEnv(task_id=task_id)
        env.reset()

        script = _BASELINE_SCRIPTS[task_id]
        episode_reward = 0.0
        steps = 0

        for cmd in script:
            result = env.step(Action(command=cmd))
            episode_reward += result.reward
            steps += 1
            if result.done:
                break

        grade = run_grader(env)
        results[task_id] = {
            "task_name":  task_cfg["name"],
            "difficulty": task_cfg["difficulty"],
            "score":      grade.score,
            "passed":     grade.passed,
            "steps":      steps,
            "reward":     round(episode_reward, 4),
            "breakdown":  grade.breakdown,
            "feedback":   grade.feedback,
        }

    scores = [r["score"] for r in results.values()]
    avg = round(sum(scores) / len(scores), 4)

    return BaselineResponse(
        results=results,
        average_score=avg,
        summary=(
            f"Heuristic baseline scored {avg:.2f} avg across {len(results)} tasks. "
            f"Task scores: " +
            ", ".join(f"{tid}={r['score']:.2f}" for tid, r in results.items())
        ),
    )


# ──────────────────────────── Entry point ────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=port,
        workers=int(os.environ.get("WORKERS", 1)),
        log_level="info",
    )
