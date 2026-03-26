"""
Pydantic models for DevOps Incident Responder — OpenEnv environment.
All models are typed, validated, and serialisable.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ──────────────────────────── Enums ────────────────────────────


class ServiceStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    CRASHED = "crashed"


# ──────────────────────────── Sub-models ────────────────────────────


class ServiceHealth(BaseModel):
    """Health snapshot of a single microservice."""

    name: str = Field(..., description="Service identifier")
    status: ServiceStatus = Field(..., description="Operational status")
    cpu_percent: float = Field(0.0, ge=0.0, le=100.0, description="CPU usage %")
    memory_mb: float = Field(0.0, ge=0.0, description="RSS memory in MB")
    uptime_seconds: Optional[float] = Field(
        None, description="Seconds since last start; None when not running"
    )
    pid: Optional[int] = Field(None, description="Process ID; None when not running")
    error_message: Optional[str] = Field(
        None, description="Most recent error message, if any"
    )


class SystemMetrics(BaseModel):
    """Aggregated infrastructure health metrics."""

    services: Dict[str, ServiceHealth] = Field(
        ..., description="Per-service health keyed by service name"
    )
    total_errors_last_minute: int = Field(
        0, ge=0, description="Total error log entries in the last 60 s"
    )
    active_connections: int = Field(
        0, ge=0, description="Active inbound HTTP connections to the API gateway"
    )
    timestamp: float = Field(
        default_factory=time.time, description="Unix timestamp of this snapshot"
    )


# ──────────────────────────── Core API models ────────────────────────────


class Action(BaseModel):
    """
    A single action the agent takes — a bash command to execute.

    Supported commands (subset of real bash):
        systemctl {status|restart|stop|list-units} [service]
        cat / head / tail  <file>
        grep [-r] [-n] [-i] <pattern> <path>
        ls [-la] [dir]
        find <dir> -name <pattern>
        curl http://<host>/endpoint
        ping <host>
        ps aux
        journalctl -u <service> [-n N]
        sed -i 's/old/new/g' <file>
        echo <text> [> / >>] <file>
        printenv / env
        netstat -tlnp
        df -h
        free -h
        date / uptime / hostname / whoami / uname -a
    """

    command: str = Field(
        ...,
        min_length=1,
        description="Bash command to run inside the simulated cloud environment",
    )


class Observation(BaseModel):
    """Everything the agent sees after executing an action."""

    stdout: str = Field("", description="Standard output of the command")
    stderr: str = Field("", description="Standard error output of the command")
    exit_code: int = Field(0, description="Exit code (0 = success)")
    system_metrics: SystemMetrics = Field(
        ..., description="Current infrastructure health metrics"
    )
    step_count: int = Field(0, ge=0, description="Steps taken so far this episode")
    timestamp: float = Field(
        default_factory=time.time, description="Unix timestamp of this observation"
    )


class Reward(BaseModel):
    """Structured reward returned alongside an observation."""

    value: float = Field(0.0, ge=-1.0, le=1.0, description="Per-step reward")
    breakdown: Dict[str, float] = Field(
        default_factory=dict, description="Named reward components"
    )
    cumulative: float = Field(
        0.0, description="Total reward accumulated this episode"
    )


# ──────────────────────────── Step / Reset results ────────────────────────────


class StepResult(BaseModel):
    """Return value of POST /step."""

    observation: Observation
    reward: float = Field(..., description="Scalar reward for this step")
    done: bool = Field(False, description="True when the episode is finished")
    info: Dict[str, Any] = Field(
        default_factory=dict, description="Auxiliary diagnostic information"
    )


class ResetResult(BaseModel):
    """Return value of POST /reset."""

    observation: Observation


# ──────────────────────────── State ────────────────────────────


class State(BaseModel):
    """Full serialisable state of a running episode."""

    episode_id: str = Field(..., description="Unique episode UUID")
    step_count: int = Field(0, ge=0)
    task_id: str = Field(..., description="Active task identifier")
    task_name: str = Field(..., description="Human-readable task name")
    done: bool = Field(False)
    services: Dict[str, str] = Field(
        ..., description="service_name -> status string"
    )
    discovered_root_cause: bool = Field(
        False, description="Agent has read postgres log showing password rotation"
    )
    configs_fixed: List[str] = Field(
        default_factory=list,
        description="Config files successfully updated with new password",
    )
    services_restarted: List[str] = Field(
        default_factory=list,
        description="Services successfully restarted with working config",
    )
    cumulative_reward: float = Field(0.0)
    max_steps: int = Field(..., description="Episode step budget")


# ──────────────────────────── Task catalogue ────────────────────────────


class TaskInfo(BaseModel):
    """Descriptor for a task surfaced via GET /tasks."""

    task_id: str
    name: str
    description: str
    difficulty: str = Field(..., description="easy | medium | hard")
    max_steps: int
    objectives: List[str]
    action_schema: Dict[str, Any] = Field(
        ..., description="JSON Schema fragment describing the Action model"
    )


# ──────────────────────────── Grader / Baseline ────────────────────────────


class GraderResult(BaseModel):
    """Grader output returned by POST /grader."""

    task_id: str
    score: float = Field(..., ge=0.0, le=1.0, description="Normalised score [0, 1]")
    breakdown: Dict[str, float] = Field(
        default_factory=dict, description="Per-criterion scores"
    )
    passed: bool = Field(..., description="True when score >= 0.7")
    feedback: str = Field(..., description="Human-readable grader commentary")


class BaselineResult(BaseModel):
    """Single-task result from the baseline inference script."""

    task_id: str
    score: float = Field(..., ge=0.0, le=1.0)
    steps_taken: int
    done: bool
    summary: str
