"""
FastAPI server for DevOps Incident Responder — OpenEnv Environment.

Endpoints
─────────
GET   /              — Web UI (interactive terminal)
GET   /health        — liveness probe
GET   /tasks         — task catalogue + action schema
GET   /state         — episode state
GET   /validate      — OpenEnv spec compliance report
GET   /metrics       — aggregate episode statistics
POST  /reset         — start / restart episode (session-based)
POST  /step          — execute one action
POST  /grader        — score current episode
POST  /baseline      — run heuristic baseline, return scores
GET   /replay/{sid}  — replay command history for a session
WS    /ws            — WebSocket interface (low-latency)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from environment import DevOpsEnv, TASKS
from models import Action, GraderResult, ResetResult, State, StepResult, TaskInfo
from tasks import run_grader, TASK_CATALOGUE
from scripts import BASELINE_SCRIPTS as _BASELINE_SCRIPTS

# ──────────────────────────── Session store ────────────────────────────

_sessions: Dict[str, DevOpsEnv] = {}          # session_id -> env
_episode_logs: Dict[str, List[Dict]] = defaultdict(list)  # session_id -> steps
_metrics_store: Dict[str, List[float]] = defaultdict(list) # task_id -> [scores]
_total_episodes = 0


def _get_or_create_session(task_id: str, session_id: Optional[str] = None) -> tuple:
    if task_id not in TASKS:
        raise HTTPException(400, f"Unknown task_id '{task_id}'. Valid: {list(TASKS.keys())}")
    sid = session_id or str(uuid.uuid4())
    if sid not in _sessions:
        _sessions[sid] = DevOpsEnv(task_id=task_id)
    return sid, _sessions[sid]


def _get_session(session_id: str, task_id: str) -> DevOpsEnv:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    _, env = _get_or_create_session(task_id, session_id)
    return env


# ──────────────────────────── Startup ────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm one env per task
    for tid in TASKS:
        sid = f"default_{tid}"
        _sessions[sid] = DevOpsEnv(task_id=tid)
    yield
    _sessions.clear()
    _episode_logs.clear()


# ──────────────────────────── App ────────────────────────────

app = FastAPI(
    title="DevOps Incident Responder",
    description=(
        "OpenEnv environment: AI agent debugs a broken microservice cluster. "
        "DB password rotation caused cascading failures. Agent must diagnose and fix."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Serve static files (Web UI)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ──────────────────────────── Request models ────────────────────────────

class ResetRequest(BaseModel):
    task_id: str = "task3_remediation"
    session_id: Optional[str] = None

class StepRequest(BaseModel):
    task_id: str = "task3_remediation"
    session_id: Optional[str] = None
    action: Action

class GraderRequest(BaseModel):
    task_id: str = "task3_remediation"
    session_id: Optional[str] = None


# ──────────────────────────── Web UI ────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def web_ui():
    index = static_dir / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>DevOps Incident Responder</h1><p>API is running. See /docs</p>")


# ──────────────────────────── Core endpoints ────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(_sessions), "uptime": "running"}


@app.get("/info", response_model=Dict[str, Any])
@app.get("/", response_model=Dict[str, Any], include_in_schema=False)
async def root_json():
    """JSON info — returned when Accept: application/json"""
    return {
        "name": "devops-incident-responder",
        "version": "1.0.0",
        "description": "OpenEnv environment: AI agent debugs a microservice cluster broken by a database password rotation.",
        "tasks": list(TASKS.keys()),
        "endpoints": ["/reset", "/step", "/state", "/tasks", "/grader", "/baseline", "/health", "/validate", "/metrics", "/replay/{session_id}", "/ws"],
        "framework": "openenv-core",
    }


@app.post("/reset", response_model=Dict[str, Any])
async def reset(body: ResetRequest):
    global _total_episodes
    sid, env = _get_or_create_session(body.task_id, body.session_id)
    result = env.reset()
    _episode_logs[sid] = []
    _total_episodes += 1
    return {"session_id": sid, **result.model_dump()}


@app.post("/step", response_model=Dict[str, Any])
async def step(body: StepRequest):
    env = _get_session(body.session_id, body.task_id)
    result = env.step(body.action)
    if body.session_id:
        _episode_logs[body.session_id].append({
            "step": env._step_count,
            "command": body.action.command,
            "exit_code": result.observation.exit_code,
            "reward": result.reward,
            "done": result.done,
            "stdout_snippet": result.observation.stdout[:200],
        })
    if result.done:
        grade = run_grader(env)
        _metrics_store[env.task_id].append(grade.score)
    return result.model_dump()


@app.get("/state", response_model=State)
async def state(
    task_id: str = Query("task3_remediation"),
    session_id: Optional[str] = Query(None),
):
    env = _get_session(session_id, task_id)
    return env.state()


@app.get("/tasks", response_model=Dict[str, TaskInfo])
async def list_tasks():
    return TASK_CATALOGUE


@app.post("/grader", response_model=GraderResult)
async def grader(body: GraderRequest):
    env = _get_session(body.session_id, body.task_id)
    result = run_grader(env)
    _metrics_store[env.task_id].append(result.score)
    return result


# ──────────────────────────── Baseline ────────────────────────────

class BaselineResponse(BaseModel):
    results: Dict[str, Any]
    average_score: float
    summary: str


@app.post("/baseline", response_model=BaselineResponse)
async def baseline_endpoint():
    results: Dict[str, Any] = {}
    for task_id, task_cfg in TASKS.items():
        if task_id not in _BASELINE_SCRIPTS:
            continue
        env = DevOpsEnv(task_id=task_id)
        env.reset()
        episode_reward = 0.0
        steps = 0
        for cmd in _BASELINE_SCRIPTS[task_id]:
            r = env.step(Action(command=cmd))
            episode_reward += r.reward
            steps += 1
            if r.done:
                break
        grade = run_grader(env)
        _metrics_store[task_id].append(grade.score)
        results[task_id] = {
            "task_name": task_cfg["name"],
            "difficulty": task_cfg["difficulty"],
            "score": grade.score,
            "passed": grade.passed,
            "steps": steps,
            "reward": round(episode_reward, 4),
            "breakdown": grade.breakdown,
            "feedback": grade.feedback,
        }
    scores = [r["score"] for r in results.values()]
    avg = round(sum(scores) / len(scores), 4) if scores else 0.0
    return BaselineResponse(
        results=results, average_score=avg,
        summary=f"Heuristic baseline avg={avg:.2f} across {len(results)} tasks. " +
                ", ".join(f"{tid}={r['score']:.2f}" for tid, r in results.items()),
    )


# ──────────────────────────── Validate ────────────────────────────

@app.get("/validate")
async def validate():
    """OpenEnv spec compliance report."""
    checks = {}

    # Check typed models
    try:
        from models import Action, Observation, State, Reward, StepResult, ResetResult
        checks["typed_models"] = {"status": "pass", "models": ["Action", "Observation", "State", "Reward", "StepResult", "ResetResult"]}
    except Exception as e:
        checks["typed_models"] = {"status": "fail", "error": str(e)}

    # Check openenv.yaml
    yaml_path = Path(__file__).parent.parent / "openenv.yaml"
    checks["openenv_yaml"] = {"status": "pass" if yaml_path.exists() else "fail", "path": str(yaml_path)}

    # Check endpoints
    required_endpoints = ["/reset", "/step", "/state", "/tasks", "/grader", "/baseline"]
    checks["required_endpoints"] = {"status": "pass", "endpoints": required_endpoints}

    # Check 3+ tasks
    checks["task_count"] = {"status": "pass" if len(TASK_CATALOGUE) >= 3 else "fail", "count": len(TASK_CATALOGUE), "tasks": list(TASK_CATALOGUE.keys())}

    # Check graders
    grader_results = {}
    for tid in list(TASK_CATALOGUE.keys())[:3]:
        env = DevOpsEnv(task_id=tid)
        env.reset()
        g = run_grader(env)
        grader_results[tid] = {"score": g.score, "in_range": 0.0 <= g.score <= 1.0}
    checks["graders_in_range"] = {"status": "pass" if all(v["in_range"] for v in grader_results.values()) else "fail", "results": grader_results}

    # Check Dockerfile
    docker_path = Path(__file__).parent.parent / "Dockerfile"
    checks["dockerfile"] = {"status": "pass" if docker_path.exists() else "fail"}

    all_pass = all(v.get("status") == "pass" for v in checks.values())
    return {"compliant": all_pass, "spec_version": "0.2", "checks": checks}


# ──────────────────────────── Metrics ────────────────────────────

@app.get("/metrics")
async def metrics():
    """Aggregate statistics across all episodes."""
    task_metrics = {}
    for tid in TASKS:
        scores = _metrics_store.get(tid, [])
        task_metrics[tid] = {
            "episodes": len(scores),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "best_score": round(max(scores), 4) if scores else 0.0,
            "pass_rate": round(sum(1 for s in scores if s >= 0.7) / len(scores), 4) if scores else 0.0,
        }
    return {
        "total_episodes": _total_episodes,
        "active_sessions": len(_sessions),
        "tasks": task_metrics,
        "framework": "openenv-core",
        "version": "1.0.0",
    }


# ──────────────────────────── Replay ────────────────────────────

@app.get("/replay/{session_id}")
async def replay(session_id: str):
    """Return full command history for a session."""
    if session_id not in _episode_logs:
        raise HTTPException(404, f"No episode log found for session '{session_id}'")
    log = _episode_logs[session_id]
    total_reward = sum(s["reward"] for s in log)
    return {
        "session_id": session_id,
        "steps": len(log),
        "total_reward": round(total_reward, 4),
        "history": log,
    }


# ──────────────────────────── WebSocket ────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket interface for low-latency agent interaction.

    Protocol (JSON messages):
        Client → Server: {"type": "reset", "task_id": "task3_remediation"}
        Client → Server: {"type": "step",  "command": "systemctl status auth-service"}
        Client → Server: {"type": "state"}
        Client → Server: {"type": "grade"}

        Server → Client: {"type": "reset_result", "session_id": "...", "observation": {...}}
        Server → Client: {"type": "step_result",  "observation": {...}, "reward": 0.02, "done": false}
        Server → Client: {"type": "state",         "state": {...}}
        Server → Client: {"type": "grade_result",  "score": 0.85, ...}
        Server → Client: {"type": "error",         "message": "..."}
    """
    await websocket.accept()
    sid = str(uuid.uuid4())
    env: Optional[DevOpsEnv] = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            mtype = msg.get("type", "")

            if mtype == "reset":
                task_id = msg.get("task_id", "task3_remediation")
                if task_id not in TASKS:
                    await websocket.send_json({"type": "error", "message": f"Unknown task_id: {task_id}"})
                    continue
                env = DevOpsEnv(task_id=task_id)
                result = env.reset()
                _sessions[sid] = env
                _episode_logs[sid] = []
                await websocket.send_json({
                    "type": "reset_result",
                    "session_id": sid,
                    "observation": result.observation.model_dump(),
                })

            elif mtype == "step":
                if env is None:
                    await websocket.send_json({"type": "error", "message": "Call reset first"})
                    continue
                command = msg.get("command", "")
                result = env.step(Action(command=command))
                _episode_logs[sid].append({
                    "step": env._step_count, "command": command,
                    "reward": result.reward, "done": result.done,
                })
                await websocket.send_json({
                    "type": "step_result",
                    "observation": result.observation.model_dump(),
                    "reward": result.reward,
                    "done": result.done,
                    "info": result.info,
                })

            elif mtype == "state":
                if env is None:
                    await websocket.send_json({"type": "error", "message": "No active episode"})
                    continue
                await websocket.send_json({"type": "state", "state": env.state().model_dump()})

            elif mtype == "grade":
                if env is None:
                    await websocket.send_json({"type": "error", "message": "No active episode"})
                    continue
                grade = run_grader(env)
                await websocket.send_json({"type": "grade_result", **grade.model_dump()})

            elif mtype == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({"type": "error", "message": f"Unknown message type: {mtype}"})

    except WebSocketDisconnect:
        _sessions.pop(sid, None)


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
