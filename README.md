---
title: DevOps Incident Responder
emoji: 🔧
colorFrom: red
colorTo: orange
sdk: docker
pinned: true
license: mit
tags:
  - openenv
  - devops
  - incident-response
  - sre
  - microservices
  - reinforcement-learning
short_description: OpenEnv environment - AI agent debugs a broken microservice cluster
---

# DevOps Incident Responder — OpenEnv Environment

> **OpenEnv hackathon submission** · Real-world task · 3 tasks · Full spec compliance

An AI agent plays the role of a DevOps Site Reliability Engineer responding to a live production incident. An automated cron job silently rotated the database password at 07:55, invalidating credentials still hardcoded in `auth-service` and `user-service` configs. The cascade: auth crashes → API gateway degrades → notification service crashes. The agent must diagnose and fix everything using only simulated bash commands.

---

## Environment Description

| Property | Value |
|---|---|
| Domain | DevOps / Site Reliability Engineering |
| Task type | Diagnosis + Remediation |
| Action space | Structured bash command string |
| Observation space | stdout, stderr, exit_code, system metrics |
| Episodes | 3 tasks (easy → medium → hard) |
| Framework | OpenEnv (Meta × Hugging Face) |

### The Broken Cluster

```
┌─────────────────────────────────────────────────────┐
│  postgres          ✅ RUNNING  (new password active) │
│  auth-service      ❌ CRASHED  (old password in cfg) │
│  user-service      ⚠️  DEGRADED (cache-only mode)    │
│  api-gateway       ⚠️  DEGRADED (auth upstream down) │
│  notification-svc  ❌ CRASHED  (needs auth-service)  │
└─────────────────────────────────────────────────────┘
```

### Root Cause

```
/etc/crontab  →  55 7 * * * devops /opt/scripts/rotate_db_passwords.sh
                   ↓
          postgres password rotated at 07:55
                   ↓
     /etc/secrets/db_credentials  ← new credentials written here
                   ↓
  auth-service/config.yml still has old password  ← NEVER UPDATED
  user-service/config.yml still has old password  ← NEVER UPDATED
                   ↓
       Both services crash on DB connect → cascade
```

---

## Action Space

```python
class Action(BaseModel):
    command: str  # A bash command to execute
```

**Supported commands:**

| Command | Description |
|---|---|
| `systemctl {status\|restart\|stop\|list-units} [svc]` | Service management |
| `cat / head / tail <file>` | Read files |
| `grep [-rni] <pattern> <path>` | Search files |
| `ls [-la] [dir]` | List directories |
| `find <dir> -name <pattern>` | Find files |
| `curl http://<host>/endpoint` | HTTP requests |
| `ping <host>` | Network connectivity |
| `journalctl -u <service> [-n N]` | Service logs |
| `sed -i 's/old/new/g' <file>` | Edit files in-place |
| `echo "text" > /path` | Write to file |
| `ps aux` | Process list |
| `netstat -tlnp` | Listening ports |
| `df -h` / `free -h` | Disk / memory stats |
| `date` / `uptime` / `hostname` | System info |
| `printenv` / `env` | Environment variables |
| Pipes (`cmd1 \| cmd2`) | Basic piping supported |

---

## Observation Space

```python
class Observation(BaseModel):
    stdout: str            # Command standard output
    stderr: str            # Command standard error
    exit_code: int         # 0 = success
    step_count: int        # Steps taken this episode
    timestamp: float       # Unix timestamp
    system_metrics:
        services:          # Dict of service name → health snapshot
            status:        # running | crashed | degraded | stopped
            cpu_percent:   float
            memory_mb:     float
            uptime_seconds: float | None
            pid:           int | None
            error_message: str | None
        total_errors_last_minute: int
        active_connections: int
```

---

## Tasks

### Task 1 — Service Health Discovery `(easy, 15 steps)`

**Objective:** Identify the operational status of every service.

The agent must check all 5 services and determine which are CRASHED, DEGRADED, or RUNNING.

**Grader (5 criteria × 0.20):**
- ✅ postgres checked
- ✅ auth-service checked
- ✅ api-gateway checked
- ✅ user-service checked
- ✅ notification-service checked

**Expected score:** 1.0 if all 5 checked, partial credit per service.

---

### Task 2 — Root Cause Analysis `(medium, 25 steps)`

**Objective:** Trace the incident to its root cause.

The agent must read the postgres log showing the password rotation, find the stale credential in a service config, locate the new credentials file, and identify the rotation script.

**Grader (4 criteria × 0.25):**
- ✅ `/var/log/postgres/postgresql.log` read (shows rotation event)
- ✅ Old password found in service config
- ✅ `/etc/secrets/db_credentials` read (new password)
- ✅ Rotation script / crontab read

**Expected score:** 1.0 for full RCA, partial credit per milestone.

---

### Task 3 — Full Incident Remediation `(hard, 40 steps)`

**Objective:** Restore all 5 services to RUNNING status.

The agent must discover the new password, update both broken config files, and restart services in the correct dependency order (auth → user → notification → gateway).

**Grader (7 criteria × ~0.143):**
- ✅ `auth-service/config.yml` updated
- ✅ `user-service/config.yml` updated
- ✅ auth-service restarted
- ✅ user-service restarted
- ✅ notification-service restarted
- ✅ api-gateway restarted
- ✅ All 5 services RUNNING

**Dependency rules enforced:**
- `auth-service` won't start if old password still in config
- `notification-service` won't start if auth-service is down
- `api-gateway` stays DEGRADED until all upstreams are RUNNING

---

## Reward Function

| Event | Reward |
|---|---|
| Discovering a service status (per unique service) | +0.02 |
| Reading an error log (per unique file) | +0.03 |
| Reading postgres log with rotation event | +0.10 |
| Finding new credentials file | +0.05 |
| Spotting old password in a config | +0.03 |
| Identifying rotation script/cron | +0.03 |
| Correctly updating a config file | +0.15 |
| Successfully restarting a service | +0.10 |
| All 5 services healthy (bonus) | +0.20 |
| Step cost | -0.01 |
| Invalid/unknown command | -0.02 |
| Destructive command (rm -rf, dd, etc.) | -0.10 |

---

## Baseline Scores (Heuristic Agent)

| Task | Difficulty | Score | Passed |
|---|---|---|---|
| task1_discovery | easy | **1.00** | ✅ |
| task2_rca | medium | **1.00** | ✅ |
| task3_remediation | hard | **1.00** | ✅ |

*The heuristic baseline follows the optimal command sequence and solves all three tasks perfectly. An LLM agent (GPT-4o) typically scores 0.85–1.00 depending on exploration efficiency.*

---

## Setup & Usage

### Prerequisites

- Python 3.10+
- Docker (for containerised deployment)
- `pip install -r requirements.txt`

### Local development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn server.app:app --host 0.0.0.0 --port 7860 --reload

# The API is now live at http://localhost:7860
```

### Run tests

```bash
pip install pytest httpx
python -m pytest tests/ -v
```

### Run baseline

```bash
# Heuristic baseline (no API key needed)
python baseline.py --heuristic

# LLM baseline
export OPENAI_API_KEY=sk-...
python baseline.py --llm

# Single task
python baseline.py --task task3_remediation --heuristic -v
```

### Docker

```bash
# Build
docker build -t devops-incident-responder .

# Run
docker run -p 7860:7860 devops-incident-responder

# With OpenAI key for LLM baseline
docker run -p 7860:7860 -e OPENAI_API_KEY=sk-... devops-incident-responder
```

---

## API Reference

### `POST /reset`
Start a new episode.
```json
{ "task_id": "task3_remediation" }
```

### `POST /step`
Execute one action.
```json
{
  "task_id": "task3_remediation",
  "action": { "command": "cat /var/log/postgres/postgresql.log" }
}
```

### `GET /state?task_id=task3_remediation`
Inspect current episode state.

### `GET /tasks`
List all tasks with action schema.

### `POST /grader`
Score the current episode (callable mid-episode for partial score).
```json
{ "task_id": "task3_remediation" }
```

### `POST /baseline`
Trigger the heuristic baseline against all 3 tasks and return scores.

### `GET /health`
Liveness probe — returns `{"status": "ok"}`.

---

## Project Structure

```
devops-incident-responder/
├── openenv.yaml          # OpenEnv manifest
├── Dockerfile            # Container definition
├── requirements.txt      # Python dependencies
├── README.md             # This file
├── __init__.py           # Package entry point
├── models.py             # Pydantic models (Action, Observation, State, …)
├── environment.py        # Core environment simulation
├── tasks.py              # 3 tasks + graders
├── baseline.py           # Baseline inference script (heuristic + LLM)
└── server/
│   ├── __init__.py
│   └── app.py            # FastAPI server (all endpoints)
└── tests/
    ├── __init__.py
    └── test_environment.py  # 60+ tests covering all functionality
```

---

## HuggingFace Spaces Deployment

```bash
# Install HF CLI
pip install huggingface_hub

# Login
huggingface-cli login

# Create space and push
huggingface-cli repo create devops-incident-responder --type space --space_sdk docker
cd /path/to/repo
git remote add origin https://huggingface.co/spaces/YOUR_USERNAME/devops-incident-responder
git push origin main
```

The space will be live at:
`https://YOUR_USERNAME-devops-incident-responder.hf.space`

---

## Design Decisions

**Why this domain?**
Database credential rotation causing cascading microservice failures is one of the most common real-world production incidents. It happens regularly in every organisation that rotates credentials for security compliance. Training agents on this scenario has immediate practical value.

**Why shaped rewards?**
Sparse rewards (only at episode end) are hard to learn from. Every diagnostic step gives a signal: checking a service, reading a log, finding the root cause, fixing a file. This makes the environment learnable even for weaker models.

**Why enforce dependency order?**
In real systems, restarting a service before its dependencies are healthy causes immediate re-crash. Enforcing this teaches agents proper SRE procedures (start postgres → auth → user → notification → gateway).

**Why simulate the full filesystem?**
Real incident response involves reading configs, logs, secrets, cron entries, and scripts. Providing a realistic mock filesystem forces the agent to actually explore and reason, rather than guessing a single magic command.

---

## License

MIT
