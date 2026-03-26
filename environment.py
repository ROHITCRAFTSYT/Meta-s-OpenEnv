"""
DevOps Incident Responder — Core Environment Simulation.

Simulates a broken microservice cluster where an automated DB password
rotation ran at 07:55 but forgot to restart the dependent services,
causing cascading auth failures.

Architecture:
    postgres          — RUNNING  (new password active)
    auth-service      — CRASHED  (still has old password in config)
    api-gateway       — DEGRADED (auth upstream down)
    user-service      — DEGRADED (DB + auth unreachable, serving cache)
    notification-svc  — CRASHED  (hard dependency on auth-service)
"""
from __future__ import annotations

import copy
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from models import (
    Action,
    Observation,
    Reward,
    ResetResult,
    ServiceHealth,
    ServiceStatus,
    State,
    StepResult,
    SystemMetrics,
)

# ──────────────────────────── Credentials ────────────────────────────

OLD_PASSWORD = "db_pass_v1_abc123"
NEW_PASSWORD = "db_pass_v2_xyz789"

# ──────────────────────────── Mock filesystem ────────────────────────────

INITIAL_FS: Dict[str, str] = {
    "/etc/hostname": "incident-host-01\n",
    "/etc/os-release": (
        'NAME="Ubuntu"\nVERSION="22.04.3 LTS (Jammy Jellyfish)"\n'
        "ID=ubuntu\nID_LIKE=debian\n"
        'PRETTY_NAME="Ubuntu 22.04.3 LTS"\nVERSION_ID="22.04"\n'
    ),
    # ── Service configs (auth + user have the *old* password) ──
    "/etc/services/auth-service/config.yml": (
        "# Auth Service Configuration\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8001\n"
        "  workers: 4\n\n"
        "database:\n"
        "  host: postgres\n"
        "  port: 5432\n"
        "  name: authdb\n"
        "  user: authuser\n"
        f"  password: {OLD_PASSWORD}\n"
        "  pool_size: 10\n"
        "  timeout: 30\n\n"
        "jwt:\n"
        "  secret: jwt_secret_key_do_not_share\n"
        "  expiry: 3600\n"
    ),
    "/etc/services/user-service/config.yml": (
        "# User Service Configuration\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8002\n"
        "  workers: 2\n\n"
        "database:\n"
        "  host: postgres\n"
        "  port: 5432\n"
        "  name: userdb\n"
        "  user: userapp\n"
        f"  password: {OLD_PASSWORD}\n"
        "  pool_size: 5\n"
        "  timeout: 30\n\n"
        "auth:\n"
        "  service_url: http://auth-service:8001\n"
        "  cache_ttl: 300\n"
    ),
    "/etc/services/api-gateway/config.yml": (
        "# API Gateway Configuration\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 80\n"
        "  workers: 8\n\n"
        "upstreams:\n"
        "  auth_service: http://auth-service:8001\n"
        "  user_service: http://user-service:8002\n"
        "  notification_service: http://notification-service:8003\n\n"
        "timeouts:\n"
        "  connect: 5\n"
        "  read: 30\n"
        "  write: 30\n\n"
        "retry:\n"
        "  max_attempts: 3\n"
        "  backoff_ms: 200\n"
    ),
    "/etc/services/notification-service/config.yml": (
        "# Notification Service Configuration\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 8003\n"
        "  workers: 2\n\n"
        "auth:\n"
        "  service_url: http://auth-service:8001\n"
        "  require_auth: true\n\n"
        "smtp:\n"
        "  host: mail.internal\n"
        "  port: 587\n"
        "  user: notifications@company.com\n"
        "  password: smtp_pass_789\n\n"
        "rate_limit:\n"
        "  emails_per_minute: 100\n"
    ),
    # ── New credentials (agent must discover this file) ──
    "/etc/secrets/db_credentials": (
        "# Database Credentials — Updated by DevOps automation on 2026-03-27\n"
        "# CONFIDENTIAL: Do not commit to version control\n"
        "#\n"
        "# Password rotation was performed for security compliance (PCI-DSS §8.3.9).\n"
        "# All services connecting to postgres must be updated and restarted.\n"
        "#\n"
        "DB_HOST=postgres\n"
        "DB_PORT=5432\n"
        "\n"
        "# Auth service DB\n"
        "AUTH_DB_NAME=authdb\n"
        "AUTH_DB_USER=authuser\n"
        f"AUTH_DB_PASSWORD={NEW_PASSWORD}\n"
        "\n"
        "# User service DB\n"
        "USER_DB_NAME=userdb\n"
        "USER_DB_USER=userapp\n"
        f"USER_DB_PASSWORD={NEW_PASSWORD}\n"
        "\n"
        "# Postgres admin (read-only reference)\n"
        "POSTGRES_ADMIN_USER=postgres\n"
        "POSTGRES_ADMIN_PASSWORD=admin_secure_9871\n"
    ),
    # ── Logs ──
    "/var/log/auth-service/error.log": (
        "[2026-03-27 07:58:00] INFO:  Auth service starting (PID 2201)\n"
        "[2026-03-27 07:58:01] INFO:  Config loaded from /etc/services/auth-service/config.yml\n"
        "[2026-03-27 07:58:02] INFO:  Attempting database connection to postgres:5432 ...\n"
        "[2026-03-27 08:00:01] ERROR: Connection failed: FATAL: password authentication failed"
        ' for user "authuser"\n'
        "[2026-03-27 08:00:02] ERROR: Retry 1/3 — FATAL: password authentication failed"
        ' for user "authuser"\n'
        "[2026-03-27 08:00:05] ERROR: Retry 2/3 — FATAL: password authentication failed"
        ' for user "authuser"\n'
        "[2026-03-27 08:00:08] ERROR: Retry 3/3 — FATAL: password authentication failed"
        ' for user "authuser"\n'
        "[2026-03-27 08:00:08] FATAL: Max retries exceeded. DB pool failed to initialise.\n"
        "[2026-03-27 08:00:08] FATAL: Service shutting down (exit code 1).\n"
    ),
    "/var/log/api-gateway/access.log": (
        "[2026-03-27 07:59:55] GET  /api/products     200  45ms\n"
        "[2026-03-27 07:59:57] GET  /api/products/123 200  38ms\n"
        "[2026-03-27 08:00:01] POST /api/auth/login   503  upstream=auth-service ECONNREFUSED\n"
        "[2026-03-27 08:00:02] GET  /api/users/me     503  upstream=auth-service ECONNREFUSED\n"
        "[2026-03-27 08:00:10] GET  /api/products     200  42ms\n"
        "[2026-03-27 08:00:11] POST /api/auth/login   503  upstream=auth-service ECONNREFUSED\n"
        "[2026-03-27 08:00:15] GET  /api/users/orders 503  upstream=auth-service ECONNREFUSED\n"
        "[2026-03-27 08:00:20] POST /api/notify/send  503  upstream=notification-service"
        " ECONNREFUSED\n"
        "[2026-03-27 08:00:25] GET  /health            200   2ms\n"
    ),
    "/var/log/api-gateway/error.log": (
        "[2026-03-27 08:00:01] ERROR: auth-service unreachable — connection refused :8001\n"
        "[2026-03-27 08:00:12] ERROR: notification-service unreachable — connection refused :8003\n"
        "[2026-03-27 08:00:01] WARN:  Health-check failed for auth-service; marking unavailable\n"
        "[2026-03-27 08:00:12] WARN:  Health-check failed for notification-service;"
        " marking unavailable\n"
    ),
    "/var/log/user-service/app.log": (
        "[2026-03-27 07:58:05] INFO:  User service starting\n"
        "[2026-03-27 07:58:06] INFO:  Database connection established\n"
        "[2026-03-27 07:58:07] INFO:  Auth service reachable at http://auth-service:8001\n"
        "[2026-03-27 07:58:07] INFO:  Service ready, listening on :8002\n"
        "[2026-03-27 08:00:02] ERROR: DB connection lost: FATAL: password authentication"
        ' failed for user "userapp"\n'
        "[2026-03-27 08:00:02] WARN:  Switching to read-only cache mode\n"
        "[2026-03-27 08:00:09] WARN:  Auth-service unreachable; serving cached sessions\n"
        "[2026-03-27 08:00:09] WARN:  Cache may be stale (last synced 2026-03-27 07:55:00, TTL 300s)\n"
        "[2026-03-27 08:00:15] ERROR: Cache TTL exceeded for 14 user sessions\n"
    ),
    "/var/log/notification-service/error.log": (
        "[2026-03-27 07:58:10] INFO:  Notification service starting\n"
        "[2026-03-27 07:58:11] INFO:  Connecting to auth-service for token validation\n"
        "[2026-03-27 08:00:12] ERROR: Failed to reach auth-service:"
        " connect ECONNREFUSED auth-service:8001\n"
        "[2026-03-27 08:00:12] FATAL: Cannot initialise without auth (require_auth=true)\n"
        "[2026-03-27 08:00:12] FATAL: Exiting with code 1\n"
    ),
    "/var/log/postgres/postgresql.log": (
        "[2026-03-27 07:54:55] LOG: Received password rotation request (automation)\n"
        "[2026-03-27 07:55:00] LOG: ALTER USER authuser WITH PASSWORD '***' — OK\n"
        "[2026-03-27 07:55:00] LOG: ALTER USER userapp  WITH PASSWORD '***' — OK\n"
        "[2026-03-27 07:55:00] LOG: Password rotation completed for: authuser, userapp\n"
        "[2026-03-27 07:55:00] LOG: New credentials written to /etc/secrets/db_credentials\n"
        "[2026-03-27 07:55:00] LOG: Old credentials invalidated immediately\n"
        "[2026-03-27 08:00:01] LOG: FATAL: password authentication failed"
        ' for user "authuser" (client: auth-service)\n'
        "[2026-03-27 08:00:02] LOG: FATAL: password authentication failed"
        ' for user "authuser" (client: auth-service)\n'
        "[2026-03-27 08:00:05] LOG: FATAL: password authentication failed"
        ' for user "authuser" (client: auth-service)\n'
        "[2026-03-27 08:00:08] LOG: FATAL: password authentication failed"
        ' for user "authuser" (client: auth-service)\n'
        "[2026-03-27 08:00:09] LOG: FATAL: password authentication failed"
        ' for user "userapp" (client: user-service)\n'
    ),
    # ── Cron / scripts ──
    "/etc/crontab": (
        "SHELL=/bin/sh\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin\n\n"
        "# m   h   dom mon dow user   command\n"
        "17  *    *  *   *   root   run-parts /etc/cron.hourly\n"
        "55  7    *  *   *   devops /opt/scripts/rotate_db_passwords.sh\n"
    ),
    "/opt/scripts/rotate_db_passwords.sh": (
        "#!/bin/bash\n"
        "# Automated DB password rotation — runs daily at 07:55 via cron\n"
        "# New credentials are written to /etc/secrets/db_credentials\n"
        "# BUG: Does NOT restart dependent services after rotation!\n"
        "set -e\n"
        'echo "Starting password rotation at $(date)"\n'
        "NEW_PASS=$(openssl rand -base64 24)\n"
        "psql -U postgres -c \"ALTER USER authuser WITH PASSWORD '$NEW_PASS'\"\n"
        "psql -U postgres -c \"ALTER USER userapp  WITH PASSWORD '$NEW_PASS'\"\n"
        "# Write creds\n"
        "echo \"AUTH_DB_PASSWORD=$NEW_PASS\" >> /etc/secrets/db_credentials\n"
        "echo \"USER_DB_PASSWORD=$NEW_PASS\" >> /etc/secrets/db_credentials\n"
        'echo "Done. Remember to restart auth-service and user-service!"\n'
    ),
    "/proc/uptime": "3847.22 3201.44\n",
    "/etc/environment": (
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        "LANG=en_US.UTF-8\n"
        "ENVIRONMENT=production\n"
        "CLUSTER=prod-us-east-1\n"
        "APP_VERSION=3.14.1\n"
    ),
}

# ──────────────────────────── Initial service table ────────────────────────────

def _initial_services() -> Dict[str, ServiceHealth]:
    return {
        "postgres": ServiceHealth(
            name="postgres",
            status=ServiceStatus.RUNNING,
            cpu_percent=2.1,
            memory_mb=512.0,
            uptime_seconds=86400.0,
            pid=1001,
            error_message=None,
        ),
        "auth-service": ServiceHealth(
            name="auth-service",
            status=ServiceStatus.CRASHED,
            cpu_percent=0.0,
            memory_mb=0.0,
            uptime_seconds=None,
            pid=None,
            error_message='FATAL: password authentication failed for user "authuser"',
        ),
        "api-gateway": ServiceHealth(
            name="api-gateway",
            status=ServiceStatus.DEGRADED,
            cpu_percent=8.3,
            memory_mb=256.0,
            uptime_seconds=3600.0,
            pid=1045,
            error_message="Upstream auth-service unavailable; partial service only",
        ),
        "user-service": ServiceHealth(
            name="user-service",
            status=ServiceStatus.DEGRADED,
            cpu_percent=3.2,
            memory_mb=192.0,
            uptime_seconds=3600.0,
            pid=1089,
            error_message="DB reconnect failed; running in cache-only mode",
        ),
        "notification-service": ServiceHealth(
            name="notification-service",
            status=ServiceStatus.CRASHED,
            cpu_percent=0.0,
            memory_mb=0.0,
            uptime_seconds=None,
            pid=None,
            error_message="Cannot initialise without auth-service (connection refused)",
        ),
    }


# ──────────────────────────── Task configs ────────────────────────────

TASKS: Dict[str, Dict[str, Any]] = {
    "task1_discovery": {
        "name": "Service Health Discovery",
        "description": (
            "Identify the operational status of every service in the cluster. "
            "Determine which services are CRASHED, DEGRADED, or RUNNING."
        ),
        "difficulty": "easy",
        "max_steps": 15,
        "objectives": [
            "Find that auth-service is CRASHED",
            "Find that notification-service is CRASHED",
            "Find that api-gateway is DEGRADED",
            "Find that user-service is DEGRADED",
            "Confirm postgres is RUNNING",
        ],
    },
    "task2_rca": {
        "name": "Root Cause Analysis",
        "description": (
            "Trace the incident to its root cause: an automated database password "
            "rotation that invalidated credentials used by auth-service and user-service. "
            "Identify the affected config files and where the new password can be found."
        ),
        "difficulty": "medium",
        "max_steps": 25,
        "objectives": [
            "Read the postgres log showing password rotation",
            "Identify OLD_PASSWORD in at least one service config",
            "Locate /etc/secrets/db_credentials containing the new password",
            "Confirm the cron job / script responsible for the rotation",
        ],
    },
    "task3_remediation": {
        "name": "Full Incident Remediation",
        "description": (
            "Fix the entire cluster: update both broken config files with the new "
            "database password and restart all affected services so every service "
            "returns to RUNNING status."
        ),
        "difficulty": "hard",
        "max_steps": 40,
        "objectives": [
            "Update /etc/services/auth-service/config.yml with new password",
            "Update /etc/services/user-service/config.yml with new password",
            "Restart auth-service successfully",
            "Restart user-service successfully",
            "Restart notification-service successfully",
            "Restart api-gateway so it returns to RUNNING",
            "All 5 services reporting RUNNING",
        ],
    },
}


# ──────────────────────────── Reward weights ────────────────────────────

REWARD = {
    # Discovery signals
    "new_service_status_checked": 0.02,   # per unique service via systemctl/curl
    "found_error_log":            0.03,   # per unique error log read
    "found_postgres_rotation":    0.10,   # reading postgres log that shows rotation
    "found_new_password":         0.05,   # reading /etc/secrets/db_credentials
    "found_old_password_in_cfg":  0.03,   # noticing OLD_PASSWORD in a config
    "found_rotation_script":      0.03,   # reading the cron / rotation script
    # Fix signals
    "config_updated_correctly":   0.15,   # per config file correctly patched
    "service_restarted_ok":       0.10,   # per service that comes up healthy
    "all_services_healthy":       0.20,   # bonus when entire cluster is green
    # Penalties
    "step_cost":                 -0.01,   # small per-step cost
    "invalid_command":           -0.02,
    "destructive_command":       -0.10,
}

DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bdd\b",
    r"\bmkfs\b",
    r"\bshred\b",
    r">\s*/dev/sd",
    r"\btruncate\b",
    r"DROP\s+TABLE",
    r"DROP\s+DATABASE",
]


# ──────────────────────────── Environment class ────────────────────────────


class DevOpsEnv:
    """Stateful DevOps Incident Responder environment."""

    def __init__(self, task_id: str = "task3_remediation") -> None:
        if task_id not in TASKS:
            raise ValueError(f"Unknown task_id '{task_id}'. Choose from: {list(TASKS)}")
        self.task_id = task_id
        self._reset_state()

    # ── Public OpenEnv API ──

    def reset(self) -> ResetResult:
        self._reset_state()
        obs = self._build_observation("", "", 0)
        return ResetResult(observation=obs)

    def step(self, action: Action) -> StepResult:
        if self._done:
            obs = self._build_observation(
                "", "Episode is already done. Call reset() to start a new episode.", 1
            )
            return StepResult(observation=obs, reward=0.0, done=True, info={})

        self._step_count += 1
        stdout, stderr, exit_code = self._execute(action.command)
        reward_val, reward_info = self._compute_reward(action.command, stdout, stderr, exit_code)
        self._cumulative_reward += reward_val

        # Check termination
        done = self._check_done()
        self._done = done

        obs = self._build_observation(stdout, stderr, exit_code)
        return StepResult(
            observation=obs,
            reward=reward_val,
            done=done,
            info={**reward_info, "cumulative_reward": self._cumulative_reward},
        )

    def state(self) -> State:
        return State(
            episode_id=self._episode_id,
            step_count=self._step_count,
            task_id=self.task_id,
            task_name=TASKS[self.task_id]["name"],
            done=self._done,
            services={k: v.status.value for k, v in self._services.items()},
            discovered_root_cause=self._discovered_root_cause,
            configs_fixed=list(self._configs_fixed),
            services_restarted=list(self._services_restarted),
            cumulative_reward=self._cumulative_reward,
            max_steps=TASKS[self.task_id]["max_steps"],
        )

    # ── Observation builder ──

    def _build_observation(self, stdout: str, stderr: str, exit_code: int) -> Observation:
        metrics = SystemMetrics(
            services=dict(self._services),
            total_errors_last_minute=self._count_errors(),
            active_connections=self._count_connections(),
            timestamp=time.time(),
        )
        return Observation(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            system_metrics=metrics,
            step_count=self._step_count,
            timestamp=time.time(),
        )

    def _count_errors(self) -> int:
        crashed = sum(
            1 for s in self._services.values() if s.status == ServiceStatus.CRASHED
        )
        degraded = sum(
            1 for s in self._services.values() if s.status == ServiceStatus.DEGRADED
        )
        return crashed * 10 + degraded * 3

    def _count_connections(self) -> int:
        gw = self._services.get("api-gateway")
        if gw and gw.status == ServiceStatus.RUNNING:
            return 142
        if gw and gw.status == ServiceStatus.DEGRADED:
            return 61
        return 0

    # ── Done check ──

    def _check_done(self) -> bool:
        task_cfg = TASKS[self.task_id]
        max_steps = task_cfg["max_steps"]

        if self._step_count >= max_steps:
            return True

        if self.task_id == "task1_discovery":
            required = {"auth-service", "notification-service", "api-gateway",
                        "user-service", "postgres"}
            return required.issubset(self._checked_services)

        if self.task_id == "task2_rca":
            return (
                self._discovered_root_cause
                and self._found_new_password
                and len(self._found_old_in_config) >= 1
            )

        if self.task_id == "task3_remediation":
            return all(
                s.status == ServiceStatus.RUNNING for s in self._services.values()
            )

        return False

    # ──────────────────── Command execution ────────────────────

    def _execute(self, raw: str) -> Tuple[str, str, int]:
        cmd = raw.strip()

        # Safety check first
        for pat in DESTRUCTIVE_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                return (
                    "",
                    f"bash: operation not permitted: {cmd.split()[0]}",
                    126,
                )

        # Empty
        if not cmd:
            return "", "", 0

        parts = cmd.split()
        base = parts[0].lstrip("./")

        dispatch = {
            "systemctl": self._cmd_systemctl,
            "cat":        self._cmd_cat,
            "head":       self._cmd_head,
            "tail":       self._cmd_tail,
            "grep":       self._cmd_grep,
            "ls":         self._cmd_ls,
            "find":       self._cmd_find,
            "curl":       self._cmd_curl,
            "ping":       self._cmd_ping,
            "ps":         self._cmd_ps,
            "journalctl": self._cmd_journalctl,
            "sed":        self._cmd_sed,
            "echo":       self._cmd_echo,
            "printenv":   self._cmd_printenv,
            "env":        self._cmd_printenv,
            "netstat":    self._cmd_netstat,
            "ss":         self._cmd_netstat,
            "df":         self._cmd_df,
            "free":       self._cmd_free,
            "date":       self._cmd_date,
            "uptime":     self._cmd_uptime,
            "hostname":   self._cmd_hostname,
            "whoami":     self._cmd_whoami,
            "uname":      self._cmd_uname,
            "top":        self._cmd_top,
            "htop":       self._cmd_top,
            "which":      self._cmd_which,
            "pwd":        lambda _: ("/root\n", "", 0),
            "id":         lambda _: ("uid=0(root) gid=0(root) groups=0(root)\n", "", 0),
            "history":    lambda _: ("", "history: not available in this session\n", 1),
        }

        handler = dispatch.get(base)
        if handler:
            return handler(cmd)

        # Pipe handling (very limited — grep after cat)
        if "|" in cmd:
            return self._cmd_pipe(cmd)

        return (
            "",
            f"bash: {base}: command not found\n",
            127,
        )

    # ── systemctl ──

    def _cmd_systemctl(self, cmd: str) -> Tuple[str, str, int]:
        parts = cmd.split()
        if len(parts) < 2:
            return "", "Usage: systemctl <action> [service]\n", 1

        action = parts[1]

        if action == "list-units":
            lines = ["UNIT                        LOAD   ACTIVE SUB     DESCRIPTION"]
            for name, svc in self._services.items():
                sub = "running" if svc.status == ServiceStatus.RUNNING else "dead"
                active = "active" if svc.status == ServiceStatus.RUNNING else "failed"
                if svc.status == ServiceStatus.DEGRADED:
                    active, sub = "active", "running"
                lines.append(
                    f"{name+'.service':<32}{' loaded':<8}{active:<8}{sub:<10}{name}"
                )
            lines.append(f"\n{len(self._services)} loaded units listed.")
            self._checked_services.update(self._services.keys())
            return "\n".join(lines) + "\n", "", 0

        if len(parts) < 3:
            return "", f"systemctl: missing service name for '{action}'\n", 1

        svc_arg = parts[2].replace(".service", "")
        if svc_arg not in self._services:
            return "", f"Unit {svc_arg}.service not found.\n", 4

        svc = self._services[svc_arg]
        self._checked_services.add(svc_arg)

        if action == "status":
            return self._systemctl_status_output(svc_arg, svc), "", 0

        if action in ("restart", "start"):
            return self._systemctl_restart(svc_arg)

        if action == "stop":
            svc.status = ServiceStatus.STOPPED
            svc.cpu_percent = 0.0
            svc.memory_mb = 0.0
            svc.uptime_seconds = None
            svc.pid = None
            return f"Stopped {svc_arg}.service.\n", "", 0

        return "", f"systemctl: unknown action '{action}'\n", 1

    def _systemctl_status_output(self, name: str, svc: ServiceHealth) -> str:
        icon = "●" if svc.status == ServiceStatus.RUNNING else "✗"
        active_str = {
            ServiceStatus.RUNNING:  "active (running)",
            ServiceStatus.CRASHED:  "failed (Result: exit-code)",
            ServiceStatus.DEGRADED: "active (running) — degraded",
            ServiceStatus.STOPPED:  "inactive (dead)",
        }[svc.status]
        pid_str = f"Main PID: {svc.pid}" if svc.pid else "Main PID: (none)"
        mem_str = f"{svc.memory_mb:.0f}M" if svc.memory_mb else "0"
        err_hint = f"\n   └─ {svc.error_message}" if svc.error_message else ""
        return (
            f"{icon} {name}.service\n"
            f"   Loaded: loaded (/etc/systemd/system/{name}.service)\n"
            f"   Active: {active_str}{err_hint}\n"
            f"   {pid_str}\n"
            f"   Memory: {mem_str}\n"
            f"   CPU:    {svc.cpu_percent:.1f}%\n"
        )

    def _systemctl_restart(self, svc_name: str) -> Tuple[str, str, int]:
        svc = self._services[svc_name]

        if svc_name == "postgres":
            svc.status = ServiceStatus.RUNNING
            svc.uptime_seconds = 1.0
            svc.pid = 1001
            return "Restarted postgres.service.\n", "", 0

        if svc_name in ("auth-service", "user-service"):
            cfg_path = f"/etc/services/{svc_name}/config.yml"
            cfg_content = self._fs.get(cfg_path, "")
            if OLD_PASSWORD in cfg_content:
                return (
                    "",
                    f"Job for {svc_name}.service failed. "
                    "FATAL: password authentication failed (old credentials still in config).\n"
                    f"See 'journalctl -u {svc_name}' for details.\n",
                    1,
                )
            # Config is fixed — service starts
            svc.status = ServiceStatus.RUNNING
            svc.cpu_percent = 4.5 if svc_name == "auth-service" else 2.8
            svc.memory_mb = 128.0
            svc.uptime_seconds = 0.5
            svc.pid = 2000 + hash(svc_name) % 1000
            svc.error_message = None
            self._services_restarted.add(svc_name)
            self._maybe_fix_dependents()
            return f"Started {svc_name}.service.\n", "", 0

        if svc_name == "notification-service":
            auth_ok = self._services["auth-service"].status == ServiceStatus.RUNNING
            if not auth_ok:
                return (
                    "",
                    "Job for notification-service.service failed. "
                    "FATAL: auth-service unreachable (connection refused :8001).\n",
                    1,
                )
            svc.status = ServiceStatus.RUNNING
            svc.cpu_percent = 1.5
            svc.memory_mb = 96.0
            svc.uptime_seconds = 0.5
            svc.pid = 2500
            svc.error_message = None
            self._services_restarted.add(svc_name)
            return "Started notification-service.service.\n", "", 0

        if svc_name == "api-gateway":
            auth_ok = self._services["auth-service"].status == ServiceStatus.RUNNING
            notif_ok = self._services["notification-service"].status == ServiceStatus.RUNNING
            if auth_ok and notif_ok:
                svc.status = ServiceStatus.RUNNING
                svc.error_message = None
            else:
                svc.status = ServiceStatus.DEGRADED
                missing = [
                    n for n, v in self._services.items()
                    if n != "api-gateway" and v.status != ServiceStatus.RUNNING
                ]
                svc.error_message = f"Upstream(s) still unavailable: {', '.join(missing)}"
            svc.cpu_percent = 9.0
            svc.memory_mb = 260.0
            svc.uptime_seconds = 0.5
            svc.pid = 1045
            self._services_restarted.add(svc_name)
            return f"Restarted api-gateway.service.\n", "", 0

        # Generic fallback
        svc.status = ServiceStatus.RUNNING
        svc.uptime_seconds = 0.5
        svc.pid = 3000
        self._services_restarted.add(svc_name)
        return f"Restarted {svc_name}.service.\n", "", 0

    def _maybe_fix_dependents(self) -> None:
        """Auto-upgrade api-gateway if all deps are now healthy."""
        auth_ok = self._services["auth-service"].status == ServiceStatus.RUNNING
        notif_ok = self._services["notification-service"].status == ServiceStatus.RUNNING
        gw = self._services["api-gateway"]
        if auth_ok and notif_ok and gw.status == ServiceStatus.DEGRADED:
            # Don't auto-upgrade; agent must restart gw explicitly.
            pass

    # ── cat ──

    def _cmd_cat(self, cmd: str) -> Tuple[str, str, int]:
        path = self._extract_path(cmd, "cat")
        if not path:
            return "", "cat: missing file operand\n", 1
        return self._read_file(path)

    # ── head / tail ──

    def _cmd_head(self, cmd: str) -> Tuple[str, str, int]:
        n, path = self._extract_n_path(cmd, default_n=10)
        if not path:
            return "", "head: missing file operand\n", 1
        content, err, code = self._read_file(path)
        if code != 0:
            return content, err, code
        lines = content.splitlines(keepends=True)[:n]
        return "".join(lines), "", 0

    def _cmd_tail(self, cmd: str) -> Tuple[str, str, int]:
        n, path = self._extract_n_path(cmd, default_n=10)
        if not path:
            return "", "tail: missing file operand\n", 1
        content, err, code = self._read_file(path)
        if code != 0:
            return content, err, code
        lines = content.splitlines(keepends=True)[-n:]
        return "".join(lines), "", 0

    # ── grep ──

    def _cmd_grep(self, cmd: str) -> Tuple[str, str, int]:
        # grep [-rniI] pattern path
        parts = cmd.split()
        flags = set()
        args = []
        i = 1
        while i < len(parts):
            if parts[i].startswith("-"):
                flags.update(parts[i][1:].lower())
            else:
                args.append(parts[i])
            i += 1

        if len(args) < 2:
            return "", "grep: need pattern and path\n", 1

        pattern, path = args[0], args[1]
        ci = "i" in flags
        recursive = "r" in flags

        paths_to_search = []
        if recursive:
            for fp in self._fs:
                if fp.startswith(path.rstrip("/*")):
                    paths_to_search.append(fp)
        else:
            paths_to_search = [path]

        results = []
        for fp in paths_to_search:
            content = self._fs.get(fp)
            if content is None:
                continue
            self._track_file_read(fp)
            for lineno, line in enumerate(content.splitlines(), 1):
                hit = (
                    re.search(pattern, line, re.IGNORECASE if ci else 0) is not None
                )
                if hit:
                    prefix = f"{fp}:{lineno}: " if recursive else f"{lineno}: "
                    results.append(prefix + line)

        if not results:
            return "", "", 1
        return "\n".join(results) + "\n", "", 0

    # ── ls ──

    def _cmd_ls(self, cmd: str) -> Tuple[str, str, int]:
        parts = cmd.split()
        show_long = "-l" in parts or "-la" in parts or "-al" in parts
        target = "/"
        for p in parts[1:]:
            if not p.startswith("-"):
                target = p
                break
        target = target.rstrip("/") or "/"

        entries = set()
        for path in self._fs:
            if target == "/":
                # root: collect top-level directory names
                first = path.lstrip("/").split("/")[0]
                if first:
                    entries.add(first)
            elif path.startswith(target + "/"):
                rest = path[len(target) + 1:]
                first = rest.split("/")[0]
                entries.add(first)
            elif path == target:
                entries.add(path.split("/")[-1])

        if not entries:
            # Maybe it's a file
            if target in self._fs:
                return target.split("/")[-1] + "\n", "", 0
            return "", f"ls: cannot access '{target}': No such file or directory\n", 2

        if show_long:
            lines = ["total " + str(len(entries) * 4)]
            for e in sorted(entries):
                full = (target.rstrip("/") + "/" + e).replace("//", "/")
                is_dir = any(
                    p.startswith(full + "/") for p in self._fs if p != full
                )
                dtype = "d" if is_dir else "-"
                lines.append(f"{dtype}rwxr-xr-x 1 root root  4096 Mar 27 08:01 {e}")
            return "\n".join(lines) + "\n", "", 0

        return "  ".join(sorted(entries)) + "\n", "", 0

    # ── find ──

    def _cmd_find(self, cmd: str) -> Tuple[str, str, int]:
        parts = cmd.split()
        if len(parts) < 2:
            return "", "find: missing path\n", 1
        base = parts[1]
        name_pat = None
        for i, p in enumerate(parts):
            if p == "-name" and i + 1 < len(parts):
                name_pat = parts[i + 1].strip("'\"")
        results = []
        for fp in sorted(self._fs):
            if not fp.startswith(base):
                continue
            fname = fp.split("/")[-1]
            if name_pat:
                if re.fullmatch(name_pat.replace("*", ".*").replace("?", "."), fname):
                    results.append(fp)
            else:
                results.append(fp)
        return "\n".join(results) + "\n" if results else "", "", 0 if results else 1

    # ── curl ──

    def _cmd_curl(self, cmd: str) -> Tuple[str, str, int]:
        url_match = re.search(r"https?://([^/\s]+)(/[^\s]*)?", cmd)
        if not url_match:
            return "", "curl: could not parse URL\n", 1
        host = url_match.group(1).split(":")[0]
        path = (url_match.group(2) or "/").split("?")[0]

        svc_map = {
            "auth-service":         "auth-service",
            "auth-service:8001":    "auth-service",
            "user-service":         "user-service",
            "user-service:8002":    "user-service",
            "api-gateway":          "api-gateway",
            "localhost":            "api-gateway",
            "localhost:80":         "api-gateway",
            "notification-service": "notification-service",
            "notification-service:8003": "notification-service",
            "postgres":             "postgres",
        }
        svc_name = svc_map.get(host)

        if svc_name:
            self._checked_services.add(svc_name)
            svc = self._services[svc_name]
            if path in ("/health", "/healthz", "/ping", "/status"):
                if svc.status == ServiceStatus.RUNNING:
                    return f'{{"status":"ok","service":"{svc_name}"}}\n', "", 0
                if svc.status == ServiceStatus.DEGRADED:
                    return (
                        f'{{"status":"degraded","service":"{svc_name}",'
                        f'"error":"{svc.error_message}"}}\n',
                        "",
                        0,
                    )
                return (
                    "",
                    f"curl: (7) Failed to connect to {host}: Connection refused\n",
                    7,
                )
            if svc.status in (ServiceStatus.CRASHED, ServiceStatus.STOPPED):
                return (
                    "",
                    f"curl: (7) Failed to connect to {host}: Connection refused\n",
                    7,
                )
            return f'{{"status":"ok","path":"{path}"}}\n', "", 0

        return "", f"curl: (6) Could not resolve host: {host}\n", 6

    # ── ping ──

    def _cmd_ping(self, cmd: str) -> Tuple[str, str, int]:
        parts = cmd.split()
        host = parts[-1] if len(parts) > 1 else ""
        reachable_hosts = {"postgres", "localhost", "127.0.0.1",
                           "api-gateway", "user-service",
                           "auth-service", "notification-service",
                           "mail.internal"}
        if host in reachable_hosts:
            return (
                f"PING {host}: 56 data bytes\n"
                f"64 bytes from {host}: icmp_seq=0 ttl=64 time=0.412 ms\n"
                f"64 bytes from {host}: icmp_seq=1 ttl=64 time=0.388 ms\n"
                f"--- {host} ping statistics ---\n"
                "2 packets transmitted, 2 received, 0% packet loss\n",
                "",
                0,
            )
        return (
            f"ping: {host}: Name or service not known\n",
            "",
            2,
        )

    # ── ps ──

    def _cmd_ps(self, cmd: str) -> Tuple[str, str, int]:
        lines = [
            "USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND"
        ]
        lines.append("root         1  0.0  0.0  19356  1536 ?        Ss   07:00   0:00 /sbin/init")
        for name, svc in self._services.items():
            if svc.pid and svc.status not in (ServiceStatus.CRASHED, ServiceStatus.STOPPED):
                lines.append(
                    f"root      {svc.pid:5d} {svc.cpu_percent:4.1f}  "
                    f"{svc.memory_mb/1024*10:.1f} "
                    f"{'12345':>6} {int(svc.memory_mb):>4} ?        S    07:58   0:01 {name}"
                )
        return "\n".join(lines) + "\n", "", 0

    # ── journalctl ──

    def _cmd_journalctl(self, cmd: str) -> Tuple[str, str, int]:
        unit_match = re.search(r"-u\s+(\S+)", cmd)
        if not unit_match:
            return "", "journalctl: -u <unit> required\n", 1
        unit = unit_match.group(1).replace(".service", "")
        n_match = re.search(r"-n\s+(\d+)", cmd)
        n = int(n_match.group(1)) if n_match else 50

        log_map = {
            "auth-service":          "/var/log/auth-service/error.log",
            "user-service":          "/var/log/user-service/app.log",
            "api-gateway":           "/var/log/api-gateway/access.log",
            "notification-service":  "/var/log/notification-service/error.log",
            "postgres":              "/var/log/postgres/postgresql.log",
            "postgresql":            "/var/log/postgres/postgresql.log",
        }
        log_path = log_map.get(unit)
        if not log_path:
            return "", f"-- No entries for unit '{unit}' --\n", 0

        content, err, code = self._read_file(log_path)
        if code != 0:
            return content, err, code
        lines = content.splitlines(keepends=True)[-n:]
        return "".join(lines), "", 0

    # ── sed ──

    def _cmd_sed(self, cmd: str) -> Tuple[str, str, int]:
        # Only handle: sed -i 's/old/new/g' <file>
        if "-i" not in cmd:
            return "", "sed: only in-place editing (-i) is supported\n", 1

        sub_match = re.search(r"'s/([^/]+)/([^/]+)/g?'|\"s/([^/]+)/([^/]+)/g?\"", cmd)
        if not sub_match:
            return "", "sed: could not parse substitution expression\n", 1

        old = sub_match.group(1) or sub_match.group(3)
        new = sub_match.group(2) or sub_match.group(4)

        path = cmd.split()[-1].strip("'\"")
        if path not in self._fs:
            return "", f"sed: {path}: No such file or directory\n", 1

        original = self._fs[path]
        updated = original.replace(old, new)
        if updated == original:
            return "", "", 0  # No change — still ok

        self._fs[path] = updated
        self._track_config_update(path, updated)
        return "", "", 0

    # ── echo (with redirection) ──

    def _cmd_echo(self, cmd: str) -> Tuple[str, str, int]:
        # Handle: echo "text" > /path  or  echo "text" >> /path
        append_match = re.search(r">>\s*(\S+)", cmd)
        write_match = re.search(r"(?<!>)>\s*(\S+)", cmd)

        if append_match or write_match:
            path = (append_match or write_match).group(1)
            # Extract the text
            text = re.sub(r"echo\s+", "", cmd, count=1)
            text = re.sub(r"(>>|>)\s*\S+\s*$", "", text).strip().strip("'\"")
            text += "\n"
            if append_match:
                self._fs[path] = self._fs.get(path, "") + text
            else:
                self._fs[path] = text
            self._track_config_update(path, self._fs[path])
            return "", "", 0

        # Plain echo
        text = re.sub(r"^echo\s+", "", cmd).strip().strip("'\"")
        return text + "\n", "", 0

    # ── printenv / env ──

    def _cmd_printenv(self, cmd: str) -> Tuple[str, str, int]:
        env_content = self._fs.get("/etc/environment", "")
        return env_content, "", 0

    # ── netstat ──

    def _cmd_netstat(self, cmd: str) -> Tuple[str, str, int]:
        lines = [
            "Active Internet connections (only servers)",
            "Proto  Local Address           State       PID/Program name",
        ]
        port_map = {
            "postgres":             5432,
            "auth-service":         8001,
            "user-service":         8002,
            "notification-service": 8003,
            "api-gateway":          80,
        }
        for svc_name, port in port_map.items():
            svc = self._services.get(svc_name)
            if svc and svc.status not in (ServiceStatus.CRASHED, ServiceStatus.STOPPED):
                lines.append(
                    f"tcp    0.0.0.0:{port:<20} LISTEN      {svc.pid}/{svc_name}"
                )
        return "\n".join(lines) + "\n", "", 0

    # ── df ──

    def _cmd_df(self, cmd: str) -> Tuple[str, str, int]:
        return (
            "Filesystem      Size  Used Avail Use% Mounted on\n"
            "/dev/sda1        50G   18G   30G  38% /\n"
            "tmpfs           3.9G  1.2M  3.9G   1% /dev/shm\n",
            "",
            0,
        )

    # ── free ──

    def _cmd_free(self, cmd: str) -> Tuple[str, str, int]:
        return (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:        8190432     2341280     3840000      12844     2009152     5580000\n"
            "Swap:       2097148           0     2097148\n",
            "",
            0,
        )

    # ── top ──

    def _cmd_top(self, cmd: str) -> Tuple[str, str, int]:
        lines = [
            "top - 08:05:00 up 1:05, 1 user, load average: 0.32, 0.28, 0.21",
            "Tasks:  12 total,   2 running,  10 sleeping,   0 stopped,   0 zombie",
            "%Cpu(s):  4.2 us,  1.0 sy,  0.0 ni, 94.5 id,  0.3 wa",
            "MiB Mem :   8000.0 total,   3750.0 free,   2290.0 used,   1960.0 buff/cache",
            "",
            "  PID USER   %CPU %MEM  COMMAND",
        ]
        for name, svc in self._services.items():
            if svc.pid:
                lines.append(
                    f"{svc.pid:5d} root   {svc.cpu_percent:4.1f}  "
                    f"{svc.memory_mb/8000*100:.1f}  {name}"
                )
        return "\n".join(lines) + "\n", "", 0

    # ── date / uptime / hostname / whoami / uname / which ──

    def _cmd_date(self, _: str) -> Tuple[str, str, int]:
        return "Fri Mar 27 08:05:00 UTC 2026\n", "", 0

    def _cmd_uptime(self, _: str) -> Tuple[str, str, int]:
        return " 08:05:00 up 1:05,  1 user,  load average: 0.32, 0.28, 0.21\n", "", 0

    def _cmd_hostname(self, _: str) -> Tuple[str, str, int]:
        return "incident-host-01\n", "", 0

    def _cmd_whoami(self, _: str) -> Tuple[str, str, int]:
        return "root\n", "", 0

    def _cmd_uname(self, cmd: str) -> Tuple[str, str, int]:
        return "Linux incident-host-01 5.15.0-91-generic #101-Ubuntu SMP x86_64 GNU/Linux\n", "", 0

    def _cmd_which(self, cmd: str) -> Tuple[str, str, int]:
        parts = cmd.split()
        if len(parts) < 2:
            return "", "which: missing argument\n", 1
        tool = parts[1]
        known = {
            "systemctl": "/usr/bin/systemctl",
            "cat":    "/usr/bin/cat",
            "grep":   "/usr/bin/grep",
            "sed":    "/usr/bin/sed",
            "curl":   "/usr/bin/curl",
            "ping":   "/usr/bin/ping",
            "ps":     "/usr/bin/ps",
            "find":   "/usr/bin/find",
            "journalctl": "/usr/bin/journalctl",
        }
        if tool in known:
            return known[tool] + "\n", "", 0
        return "", f"which: no {tool} in PATH\n", 1

    # ── pipe (limited) ──

    def _cmd_pipe(self, cmd: str) -> Tuple[str, str, int]:
        stages = [s.strip() for s in cmd.split("|")]
        out, err, code = self._execute(stages[0])
        for stage in stages[1:]:
            stage_parts = stage.split()
            stage_base = stage_parts[0]
            if stage_base == "grep":
                flags = set()
                args = []
                for p in stage_parts[1:]:
                    if p.startswith("-"):
                        flags.update(p[1:])
                    else:
                        args.append(p)
                if not args:
                    break
                pattern = args[0]
                ci = "i" in flags
                lines = [
                    ln for ln in out.splitlines()
                    if re.search(pattern, ln, re.IGNORECASE if ci else 0)
                ]
                out = "\n".join(lines) + ("\n" if lines else "")
                code = 0 if lines else 1
            elif stage_base in ("wc",):
                count = len(out.splitlines())
                out = str(count) + "\n"
            elif stage_base == "head":
                n = 10
                for p in stage_parts[1:]:
                    if p.startswith("-") and p[1:].isdigit():
                        n = int(p[1:])
                out = "\n".join(out.splitlines()[:n]) + "\n"
            elif stage_base == "tail":
                n = 10
                for p in stage_parts[1:]:
                    if p.startswith("-") and p[1:].isdigit():
                        n = int(p[1:])
                out = "\n".join(out.splitlines()[-n:]) + "\n"
            elif stage_base == "sort":
                lines = sorted(out.splitlines())
                out = "\n".join(lines) + "\n"
            elif stage_base == "uniq":
                seen = []
                for ln in out.splitlines():
                    if not seen or seen[-1] != ln:
                        seen.append(ln)
                out = "\n".join(seen) + "\n"
        return out, err, code

    # ── file helpers ──

    def _read_file(self, path: str) -> Tuple[str, str, int]:
        path = path.strip().strip("'\"")
        if path not in self._fs:
            return "", f"cat: {path}: No such file or directory\n", 1
        self._track_file_read(path)
        return self._fs[path], "", 0

    def _extract_path(self, cmd: str, skip: str) -> Optional[str]:
        parts = cmd.split()
        for p in parts[1:]:
            if not p.startswith("-"):
                return p
        return None

    def _extract_n_path(self, cmd: str, default_n: int = 10):
        parts = cmd.split()
        n = default_n
        path = None
        i = 1
        while i < len(parts):
            if parts[i] == "-n" and i + 1 < len(parts):
                try:
                    n = int(parts[i + 1])
                except ValueError:
                    pass
                i += 2
                continue
            if re.match(r"-\d+$", parts[i]):
                n = int(parts[i][1:])
            elif not parts[i].startswith("-"):
                path = parts[i]
            i += 1
        return n, path

    # ── tracking helpers ──

    def _track_file_read(self, path: str) -> None:
        self._read_logs.add(path)
        lp = path.lower()
        if "postgres" in lp and "log" in lp:
            self._discovered_root_cause = True
        if "db_credentials" in lp or "secrets" in lp:
            self._found_new_password = True
        if path.endswith("rotate_db_passwords.sh") or "/crontab" in path:
            self._found_rotation_script = True
        # Check if old password is in config files currently stored
        content = self._fs.get(path, "")
        if OLD_PASSWORD in content and "config.yml" in path:
            svc = path.split("/")[3] if len(path.split("/")) > 3 else path
            self._found_old_in_config.add(svc)

    def _track_config_update(self, path: str, new_content: str) -> None:
        if "config.yml" in path and OLD_PASSWORD not in new_content and NEW_PASSWORD in new_content:
            self._configs_fixed.add(path)

    # ──────────────────── Reward computation ────────────────────

    def _compute_reward(
        self, cmd: str, stdout: str, stderr: str, exit_code: int
    ) -> Tuple[float, Dict[str, Any]]:
        components: Dict[str, float] = {}
        r = 0.0

        # Step cost
        components["step_cost"] = REWARD["step_cost"]
        r += REWARD["step_cost"]

        # Destructive
        for pat in DESTRUCTIVE_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                components["destructive_command"] = REWARD["destructive_command"]
                r += REWARD["destructive_command"]
                return r, {"components": components}

        # Invalid command
        if exit_code == 127:
            components["invalid_command"] = REWARD["invalid_command"]
            r += REWARD["invalid_command"]
            return r, {"components": components}

        # New service status discovered
        for svc_name in list(self._checked_services):
            key = f"svc_checked_{svc_name}"
            if key not in components:
                components[key] = REWARD["new_service_status_checked"]
                r += REWARD["new_service_status_checked"]

        # New log file read
        for log_path in list(self._read_logs):
            key = f"log_read_{log_path}"
            if key not in components:
                if any(kw in log_path for kw in ["error.log", "app.log", "access.log"]):
                    components[key] = REWARD["found_error_log"]
                    r += REWARD["found_error_log"]

        # Root cause discovered
        if self._discovered_root_cause and "root_cause" not in self._reward_memo:
            self._reward_memo.add("root_cause")
            components["found_postgres_rotation"] = REWARD["found_postgres_rotation"]
            r += REWARD["found_postgres_rotation"]

        # New password found
        if self._found_new_password and "new_password" not in self._reward_memo:
            self._reward_memo.add("new_password")
            components["found_new_password"] = REWARD["found_new_password"]
            r += REWARD["found_new_password"]

        # Old password noticed in config
        for svc_name in list(self._found_old_in_config):
            key = f"old_pw_{svc_name}"
            if key not in self._reward_memo:
                self._reward_memo.add(key)
                components[key] = REWARD["found_old_password_in_cfg"]
                r += REWARD["found_old_password_in_cfg"]

        # Rotation script read
        if self._found_rotation_script and "rotation_script" not in self._reward_memo:
            self._reward_memo.add("rotation_script")
            components["found_rotation_script"] = REWARD["found_rotation_script"]
            r += REWARD["found_rotation_script"]

        # Config fixed
        for cfg in list(self._configs_fixed):
            key = f"cfg_fixed_{cfg}"
            if key not in self._reward_memo:
                self._reward_memo.add(key)
                components[key] = REWARD["config_updated_correctly"]
                r += REWARD["config_updated_correctly"]

        # Service restarted successfully
        for svc_name in list(self._services_restarted):
            key = f"restarted_{svc_name}"
            if key not in self._reward_memo:
                self._reward_memo.add(key)
                components[key] = REWARD["service_restarted_ok"]
                r += REWARD["service_restarted_ok"]

        # All services healthy bonus
        if all(s.status == ServiceStatus.RUNNING for s in self._services.values()):
            if "all_healthy" not in self._reward_memo:
                self._reward_memo.add("all_healthy")
                components["all_services_healthy"] = REWARD["all_services_healthy"]
                r += REWARD["all_services_healthy"]

        return round(r, 4), {"components": components}

    def _reset_state(self) -> None:
        self._episode_id = str(uuid.uuid4())
        self._step_count = 0
        self._done = False
        self._cumulative_reward = 0.0
        self._services: Dict[str, ServiceHealth] = _initial_services()
        self._fs: Dict[str, str] = copy.deepcopy(INITIAL_FS)
        self._start_time = time.time()
        self._checked_services: set = set()
        self._read_logs: set = set()
        self._discovered_root_cause = False
        self._found_new_password = False
        self._found_old_in_config: set = set()
        self._found_rotation_script = False
        self._configs_fixed: set = set()
        self._services_restarted: set = set()
        self._reward_memo: set = set()
