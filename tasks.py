"""
Task definitions and graders for the DevOps Incident Responder environment.

Tasks:
    task1_discovery   — easy   — identify all failing/degraded services
    task2_rca         — medium — root-cause analysis of the password rotation
    task3_remediation — hard   — full remediation (fix configs + restart services)
    task4_disk_full   — medium — diagnose and fix disk-full incident
    task5_ssl_expired — hard   — diagnose and fix expired SSL certificate
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from models import GraderResult, TaskInfo

if TYPE_CHECKING:
    from environment import DevOpsEnv

# ─────────────────────────── Task catalogue ───────────────────────────

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": (
                "A bash command to execute. "
                "Supported: systemctl, cat, head, tail, grep, ls, find, "
                "curl, ping, ps, journalctl, sed, echo (with redirection), "
                "printenv, env, netstat, df, free, date, uptime, "
                "hostname, whoami, uname, top, which."
            ),
        }
    },
    "required": ["command"],
}


TASK_CATALOGUE: Dict[str, TaskInfo] = {
    "task1_discovery": TaskInfo(
        task_id="task1_discovery",
        name="Service Health Discovery",
        description=(
            "The production cluster is experiencing an incident. Your job is to "
            "discover the health status of every service in the cluster. "
            "Identify which services are CRASHED, which are DEGRADED, and which "
            "are RUNNING. The episode ends when you have checked all 5 services "
            "or you exhaust your 15-step budget."
        ),
        difficulty="easy",
        max_steps=15,
        objectives=[
            "Confirm postgres is RUNNING",
            "Find that auth-service is CRASHED",
            "Find that api-gateway is DEGRADED",
            "Find that user-service is DEGRADED",
            "Find that notification-service is CRASHED",
        ],
        action_schema=ACTION_SCHEMA,
    ),
    "task2_rca": TaskInfo(
        task_id="task2_rca",
        name="Root Cause Analysis",
        description=(
            "Services are failing. Dig into the logs and configs to determine "
            "the root cause. You must: read the postgres log (which reveals the "
            "password rotation), find the stale credential in at least one "
            "service config, and locate the new credentials file. "
            "Episode ends when all three are satisfied or 25 steps are used."
        ),
        difficulty="medium",
        max_steps=25,
        objectives=[
            "Read /var/log/postgres/postgresql.log and observe password rotation",
            "Find OLD_PASSWORD in /etc/services/auth-service/config.yml "
            "or /etc/services/user-service/config.yml",
            "Locate /etc/secrets/db_credentials containing the new password",
            "Identify the cron job or rotation script as the trigger",
        ],
        action_schema=ACTION_SCHEMA,
    ),
    "task3_remediation": TaskInfo(
        task_id="task3_remediation",
        name="Full Incident Remediation",
        description=(
            "Fix the entire cluster. Both auth-service and user-service have "
            "the old database password hardcoded in their config files. "
            "The new password is in /etc/secrets/db_credentials. "
            "Update both configs, then restart auth-service, user-service, "
            "notification-service (depends on auth), and api-gateway. "
            "Episode ends when all 5 services are RUNNING or 40 steps are used."
        ),
        difficulty="hard",
        max_steps=40,
        objectives=[
            "Update /etc/services/auth-service/config.yml with new DB password",
            "Update /etc/services/user-service/config.yml with new DB password",
            "Restart auth-service successfully (requires correct config)",
            "Restart user-service successfully (requires correct config)",
            "Restart notification-service (requires auth-service RUNNING)",
            "Restart api-gateway (requires all upstreams RUNNING)",
            "All 5 services in RUNNING state",
        ],
        action_schema=ACTION_SCHEMA,
    ),
}


# ─────────────────────────── Grader functions ───────────────────────────


def grade_task1(env: "DevOpsEnv") -> GraderResult:
    """
    Score: fraction of the 5 services that the agent checked during the episode.

    Criteria (each worth 0.20):
        1. postgres status checked
        2. auth-service status checked
        3. api-gateway status checked
        4. user-service status checked
        5. notification-service status checked
    """
    all_svcs = {"postgres", "auth-service", "api-gateway",
                "user-service", "notification-service"}
    checked = env._checked_services.intersection(all_svcs)

    breakdown: Dict[str, float] = {}
    for svc in all_svcs:
        breakdown[f"checked_{svc}"] = 0.20 if svc in checked else 0.0

    score = round(len(checked) / len(all_svcs), 4)
    passed = score >= 0.7

    feedback_parts = []
    missing = all_svcs - checked
    if missing:
        feedback_parts.append(f"Services NOT checked: {', '.join(sorted(missing))}.")
    if len(checked) == len(all_svcs):
        feedback_parts.append("All services successfully identified.")
    elif len(checked) >= 3:
        feedback_parts.append("Most services identified; check the remaining ones.")
    else:
        feedback_parts.append(
            "Use 'systemctl list-units --type=service' or 'systemctl status <svc>' "
            "to discover service statuses."
        )

    return GraderResult(
        task_id="task1_discovery",
        score=score,
        breakdown=breakdown,
        passed=passed,
        feedback=" ".join(feedback_parts) or "No services checked.",
    )


def grade_task2(env: "DevOpsEnv") -> GraderResult:
    """
    Score based on four RCA milestones (each worth 0.25):
        1. Postgres log read (discovered password rotation)
        2. Old password found in at least one service config
        3. New credentials file located
        4. Rotation script or cron identified
    """
    m1 = env._discovered_root_cause           # postgres log
    m2 = len(env._found_old_in_config) >= 1   # stale config spotted
    m3 = env._found_new_password              # secrets file read
    m4 = env._found_rotation_script           # cron/script read

    breakdown = {
        "postgres_log_read":       0.25 if m1 else 0.0,
        "old_password_in_config":  0.25 if m2 else 0.0,
        "new_credentials_found":   0.25 if m3 else 0.0,
        "rotation_script_found":   0.25 if m4 else 0.0,
    }
    score = round(sum(breakdown.values()), 4)
    passed = score >= 0.7

    hints = []
    if not m1:
        hints.append("Read /var/log/postgres/postgresql.log to see the rotation event.")
    if not m2:
        hints.append(
            "cat /etc/services/auth-service/config.yml to spot the stale password."
        )
    if not m3:
        hints.append("Check /etc/secrets/ for the new DB credentials.")
    if not m4:
        hints.append("Review /etc/crontab or /opt/scripts/ to find the rotation job.")

    feedback = (
        "RCA complete — all milestones hit."
        if score == 1.0
        else "Hints: " + " | ".join(hints)
    )

    return GraderResult(
        task_id="task2_rca",
        score=score,
        breakdown=breakdown,
        passed=passed,
        feedback=feedback,
    )


def grade_task3(env: "DevOpsEnv") -> GraderResult:
    """
    Score based on seven remediation steps (each worth 1/7 ≈ 0.143):
        1. auth-service config updated
        2. user-service config updated
        3. auth-service restarted
        4. user-service restarted
        5. notification-service restarted
        6. api-gateway restarted
        7. All 5 services RUNNING (bonus criteria)
    """
    from environment import OLD_PASSWORD

    auth_cfg   = "/etc/services/auth-service/config.yml"
    user_cfg   = "/etc/services/user-service/config.yml"

    c1 = auth_cfg in env._configs_fixed
    c2 = user_cfg in env._configs_fixed
    c3 = "auth-service" in env._services_restarted
    c4 = "user-service" in env._services_restarted
    c5 = "notification-service" in env._services_restarted
    c6 = "api-gateway" in env._services_restarted

    from models import ServiceStatus
    all_running = all(
        s.status == ServiceStatus.RUNNING for s in env._services.values()
    )
    c7 = all_running

    criteria = [c1, c2, c3, c4, c5, c6, c7]
    names = [
        "auth_config_updated",
        "user_config_updated",
        "auth_service_restarted",
        "user_service_restarted",
        "notification_restarted",
        "api_gateway_restarted",
        "all_services_running",
    ]
    weight = round(1.0 / len(criteria), 6)
    breakdown = {n: weight if v else 0.0 for n, v in zip(names, criteria)}
    score = round(sum(1 for v in criteria if v) / len(criteria), 4)
    passed = score >= 0.7

    hints = []
    if not c1:
        hints.append(
            "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
            "/etc/services/auth-service/config.yml"
        )
    if not c2:
        hints.append(
            "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
            "/etc/services/user-service/config.yml"
        )
    if c1 and not c3:
        hints.append("systemctl restart auth-service")
    if c2 and not c4:
        hints.append("systemctl restart user-service")
    if c3 and not c5:
        hints.append("systemctl restart notification-service")
    if c3 and c5 and not c6:
        hints.append("systemctl restart api-gateway")

    feedback = (
        "Full remediation complete — cluster is healthy!"
        if score == 1.0
        else ("Next steps: " + " → ".join(hints) if hints else "Keep going!")
    )

    return GraderResult(
        task_id="task3_remediation",
        score=score,
        breakdown=breakdown,
        passed=passed,
        feedback=feedback,
    )


# ─────────────────────────── Task 4: Disk Full ───────────────────────────


def grade_task4(env: "DevOpsEnv") -> GraderResult:
    """
    Score based on 5 criteria (each 0.20):
        1. Identified disk full root cause (read df or error logs)
        2. Found large log files (du / find / ls on /var/log)
        3. Freed disk space (truncate or echo > on a log file)
        4. Restarted at least 2 crashed services
        5. All services running
    """
    from models import ServiceStatus

    c1 = env._discovered_root_cause  # read df or disk-related logs
    c2 = getattr(env, '_found_large_logs', False)
    c3 = getattr(env, '_freed_disk_space', False)
    c4 = len(env._services_restarted) >= 2
    c5 = all(s.status == ServiceStatus.RUNNING for s in env._services.values())

    criteria = [c1, c2, c3, c4, c5]
    names = ["identified_disk_full", "found_large_logs",
             "freed_disk_space", "restarted_services", "all_running"]
    breakdown = {n: 0.20 if v else 0.0 for n, v in zip(names, criteria)}
    score = round(sum(breakdown.values()), 4)
    passed = score >= 0.7

    hints = []
    if not c1: hints.append("Run: df -h  or  cat /var/log/api-gateway/error.log")
    if not c2: hints.append("Run: du -sh /var/log/*  or  ls -lh /var/log/api-gateway/")
    if not c3: hints.append("Run: truncate -s 0 /var/log/api-gateway/access.log")
    if not c4: hints.append("Run: systemctl restart api-gateway auth-service")
    feedback = "Disk full incident resolved!" if score == 1.0 else ("Hints: " + " | ".join(hints) if hints else "Keep going!")

    return GraderResult(task_id="task4_disk_full", score=score,
                        breakdown=breakdown, passed=passed, feedback=feedback)


# ─────────────────────────── Task 5: SSL Expired ───────────────────────────


def grade_task5(env: "DevOpsEnv") -> GraderResult:
    """
    Score based on 5 criteria (each 0.20):
        1. Found expired cert (cat /etc/ssl/certs/api-gateway.crt)
        2. Found staging cert (cat /etc/ssl/staging/api-gateway.crt)
        3. Deployed new cert (cp staging → certs)
        4. Restarted api-gateway
        5. All services running
    """
    from models import ServiceStatus

    c1 = getattr(env, '_found_expired_cert', False)
    c2 = getattr(env, '_found_new_cert', False)
    c3 = getattr(env, '_deployed_new_cert', False)
    c4 = "api-gateway" in env._services_restarted
    c5 = all(s.status == ServiceStatus.RUNNING for s in env._services.values())

    criteria = [c1, c2, c3, c4, c5]
    names = ["found_expired_cert", "found_staging_cert",
             "deployed_cert", "restarted_gateway", "all_running"]
    breakdown = {n: 0.20 if v else 0.0 for n, v in zip(names, criteria)}
    score = round(sum(breakdown.values()), 4)
    passed = score >= 0.7

    hints = []
    if not c1: hints.append("cat /etc/ssl/certs/api-gateway.crt")
    if not c2: hints.append("ls /etc/ssl/staging/ && cat /etc/ssl/staging/api-gateway.crt")
    if not c3: hints.append("cp /etc/ssl/staging/api-gateway.crt /etc/ssl/certs/api-gateway.crt")
    if not c4: hints.append("systemctl restart api-gateway")
    feedback = "SSL cert deployed — cluster healthy!" if score == 1.0 else ("Hints: " + " | ".join(hints) if hints else "Keep going!")

    return GraderResult(task_id="task5_ssl_expired", score=score,
                        breakdown=breakdown, passed=passed, feedback=feedback)


# ─────────────────────────── Task catalogue additions ───────────────────────────

TASK_CATALOGUE["task4_disk_full"] = TaskInfo(
    task_id="task4_disk_full",
    name="Disk Full Incident",
    description=(
        "The /var/log partition is at 100% capacity. Services are crashing because "
        "they cannot write to their log files. Find the large log files, free up "
        "disk space by truncating them, then restart affected services."
    ),
    difficulty="medium",
    max_steps=25,
    objectives=[
        "Run df -h to confirm disk is full",
        "Find large log files with du or ls",
        "Truncate or clear the large log files",
        "Restart crashed services",
        "All services back to RUNNING",
    ],
    action_schema=ACTION_SCHEMA,
)

TASK_CATALOGUE["task5_ssl_expired"] = TaskInfo(
    task_id="task5_ssl_expired",
    name="SSL Certificate Expired",
    description=(
        "The SSL certificate for api-gateway expired today, taking down all HTTPS "
        "traffic. certbot auto-renewed the cert but only copied it to staging. "
        "You must find the new cert, deploy it to production, then restart api-gateway."
    ),
    difficulty="hard",
    max_steps=30,
    objectives=[
        "Read expired cert at /etc/ssl/certs/api-gateway.crt",
        "Find renewed cert at /etc/ssl/staging/api-gateway.crt",
        "Copy new cert: cp /etc/ssl/staging/api-gateway.crt /etc/ssl/certs/api-gateway.crt",
        "Restart api-gateway",
        "All services RUNNING",
    ],
    action_schema=ACTION_SCHEMA,
)


# ─────────────────────────── Dispatcher ───────────────────────────


def run_grader(env: "DevOpsEnv") -> GraderResult:
    """Grade the current episode based on the active task."""
    graders = {
        "task1_discovery":   grade_task1,
        "task2_rca":         grade_task2,
        "task3_remediation": grade_task3,
        "task4_disk_full":   grade_task4,
        "task5_ssl_expired": grade_task5,
    }
    grader_fn = graders.get(env.task_id)
    if grader_fn is None:
        return GraderResult(
            task_id=env.task_id,
            score=0.0,
            breakdown={},
            passed=False,
            feedback=f"No grader found for task_id='{env.task_id}'",
        )
    return grader_fn(env)
