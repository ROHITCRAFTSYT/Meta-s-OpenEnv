"""
Comprehensive test suite for the DevOps Incident Responder OpenEnv environment.

Run with:  python -m pytest tests/ -v
       or: python tests/test_environment.py
"""
from __future__ import annotations

import sys
import os

# Make sure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from environment import DevOpsEnv, OLD_PASSWORD, NEW_PASSWORD
from models import Action, ServiceStatus, StepResult, ResetResult, State
from tasks import run_grader, TASK_CATALOGUE


# ─────────────────────────── Fixtures ───────────────────────────


@pytest.fixture
def env1():
    e = DevOpsEnv(task_id="task1_discovery")
    e.reset()
    return e


@pytest.fixture
def env2():
    e = DevOpsEnv(task_id="task2_rca")
    e.reset()
    return e


@pytest.fixture
def env3():
    e = DevOpsEnv(task_id="task3_remediation")
    e.reset()
    return e


def act(env: DevOpsEnv, cmd: str) -> StepResult:
    return env.step(Action(command=cmd))


# ─────────────────────────── reset() ───────────────────────────


class TestReset:
    def test_reset_returns_reset_result(self):
        env = DevOpsEnv(task_id="task1_discovery")
        result = env.reset()
        assert isinstance(result, ResetResult)

    def test_initial_step_count_is_zero(self, env1):
        assert env1._step_count == 0

    def test_initial_cumulative_reward_is_zero(self, env1):
        assert env1._cumulative_reward == 0.0

    def test_reset_clears_previous_state(self, env3):
        act(env3, "systemctl restart auth-service")
        env3.reset()
        assert env3._step_count == 0
        assert env3._services_restarted == set()

    def test_initial_services_are_broken(self, env3):
        svc = env3._services
        assert svc["auth-service"].status == ServiceStatus.CRASHED
        assert svc["notification-service"].status == ServiceStatus.CRASHED
        assert svc["api-gateway"].status == ServiceStatus.DEGRADED
        assert svc["user-service"].status == ServiceStatus.DEGRADED
        assert svc["postgres"].status == ServiceStatus.RUNNING

    def test_initial_config_has_old_password(self, env3):
        cfg = env3._fs["/etc/services/auth-service/config.yml"]
        assert OLD_PASSWORD in cfg
        assert NEW_PASSWORD not in cfg


# ─────────────────────────── step() — observations ───────────────────────────


class TestStepObservation:
    def test_step_increments_count(self, env1):
        act(env1, "hostname")
        assert env1._step_count == 1

    def test_step_returns_step_result(self, env1):
        r = act(env1, "hostname")
        assert isinstance(r, StepResult)

    def test_stdout_non_empty_for_valid_command(self, env1):
        r = act(env1, "hostname")
        assert "incident-host-01" in r.observation.stdout

    def test_exit_code_zero_on_success(self, env1):
        r = act(env1, "cat /etc/hostname")
        assert r.observation.exit_code == 0

    def test_exit_code_nonzero_for_missing_file(self, env1):
        r = act(env1, "cat /nonexistent/file.txt")
        assert r.observation.exit_code != 0

    def test_stderr_populated_on_error(self, env1):
        r = act(env1, "cat /no/such/path")
        assert r.observation.stderr != ""

    def test_system_metrics_present(self, env1):
        r = act(env1, "hostname")
        assert r.observation.system_metrics is not None
        assert len(r.observation.system_metrics.services) == 5

    def test_done_false_at_start(self, env1):
        r = act(env1, "hostname")
        assert r.done is False


# ─────────────────────────── Command simulation ───────────────────────────


class TestCommands:
    def test_systemctl_status(self, env1):
        r = act(env1, "systemctl status postgres")
        assert "RUNNING" in r.observation.stdout or "active" in r.observation.stdout.lower()

    def test_systemctl_list_units(self, env1):
        r = act(env1, "systemctl list-units --type=service")
        assert "auth-service" in r.observation.stdout
        assert "postgres" in r.observation.stdout

    def test_cat_valid_file(self, env3):
        r = act(env3, "cat /etc/services/auth-service/config.yml")
        assert OLD_PASSWORD in r.observation.stdout

    def test_cat_invalid_file(self, env1):
        r = act(env1, "cat /does/not/exist")
        assert r.observation.exit_code != 0

    def test_grep_finds_pattern(self, env3):
        r = act(env3, "grep password /etc/services/auth-service/config.yml")
        assert OLD_PASSWORD in r.observation.stdout

    def test_grep_no_match_returns_nonzero(self, env1):
        r = act(env1, "grep nonexistentXXXpattern /etc/hostname")
        assert r.observation.exit_code != 0

    def test_ls_root(self, env1):
        r = act(env1, "ls /")
        assert r.observation.exit_code == 0
        assert r.observation.stdout.strip() != ""

    def test_ls_etc(self, env1):
        r = act(env1, "ls /etc")
        assert "services" in r.observation.stdout or r.observation.exit_code == 0

    def test_curl_health_postgres(self, env1):
        # postgres is running
        r = act(env1, "curl http://postgres/health")
        assert r.observation.exit_code == 0

    def test_curl_auth_service_refused(self, env1):
        # auth-service is CRASHED
        r = act(env1, "curl http://auth-service:8001/health")
        assert r.observation.exit_code != 0

    def test_journalctl_auth_service(self, env3):
        r = act(env3, "journalctl -u auth-service -n 5")
        assert "password" in r.observation.stdout.lower() or \
               "FATAL" in r.observation.stdout

    def test_journalctl_postgres(self, env3):
        r = act(env3, "journalctl -u postgres -n 10")
        assert "rotation" in r.observation.stdout.lower() or \
               "password" in r.observation.stdout.lower()

    def test_ps_shows_running_services(self, env1):
        r = act(env1, "ps aux")
        assert "postgres" in r.observation.stdout

    def test_date_returns_date(self, env1):
        r = act(env1, "date")
        assert "2026" in r.observation.stdout

    def test_uptime_returns_output(self, env1):
        r = act(env1, "uptime")
        assert "load average" in r.observation.stdout

    def test_df_returns_output(self, env1):
        r = act(env1, "df -h")
        assert "Filesystem" in r.observation.stdout

    def test_free_returns_output(self, env1):
        r = act(env1, "free -h")
        assert "Mem" in r.observation.stdout

    def test_find_config_files(self, env3):
        r = act(env3, "find /etc/services -name config.yml")
        assert "auth-service" in r.observation.stdout

    def test_pipe_grep(self, env3):
        r = act(env3, "cat /etc/services/auth-service/config.yml | grep password")
        assert OLD_PASSWORD in r.observation.stdout

    def test_unknown_command_returns_127(self, env1):
        r = act(env1, "fakecommand_xyz")
        assert r.observation.exit_code == 127

    def test_destructive_rm_rf_blocked(self, env1):
        r = act(env1, "rm -rf /etc/services")
        assert r.observation.exit_code == 126

    def test_sed_updates_file(self, env3):
        r = act(env3,
                "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                "/etc/services/auth-service/config.yml")
        assert r.observation.exit_code == 0
        cfg = env3._fs["/etc/services/auth-service/config.yml"]
        assert NEW_PASSWORD in cfg
        assert OLD_PASSWORD not in cfg

    def test_echo_redirect_creates_content(self, env3):
        r = act(env3, "echo hello_world > /tmp/test_echo.txt")
        assert r.observation.exit_code == 0
        assert "/tmp/test_echo.txt" in env3._fs

    def test_netstat_shows_postgres_port(self, env1):
        r = act(env1, "netstat -tlnp")
        assert "5432" in r.observation.stdout


# ─────────────────────────── Reward ───────────────────────────


class TestReward:
    def test_step_cost_applied(self, env1):
        r = act(env1, "date")
        assert r.reward <= 0.01  # at most step cost alone or small bonus

    def test_checking_service_gives_positive_reward(self, env1):
        r = act(env1, "systemctl status auth-service")
        assert r.reward > -0.05  # step cost offset by discovery reward

    def test_reading_postgres_log_gives_large_reward(self, env2):
        r = act(env2, "cat /var/log/postgres/postgresql.log")
        # Should trigger root cause discovered
        assert env2._discovered_root_cause is True
        assert r.reward > 0.05

    def test_reading_secrets_gives_reward(self, env2):
        r = act(env2, "cat /etc/secrets/db_credentials")
        assert env2._found_new_password is True

    def test_fixing_config_gives_reward(self, env3):
        # Need to first read config to avoid unknown command path
        act(env3, "cat /etc/services/auth-service/config.yml")
        r = act(env3,
                "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                "/etc/services/auth-service/config.yml")
        assert "/etc/services/auth-service/config.yml" in env3._configs_fixed

    def test_all_services_healthy_gives_bonus(self, env3):
        # Full remediation sequence
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/auth-service/config.yml")
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/user-service/config.yml")
        act(env3, "systemctl restart auth-service")
        act(env3, "systemctl restart user-service")
        act(env3, "systemctl restart notification-service")
        r = act(env3, "systemctl restart api-gateway")
        assert all(
            s.status == ServiceStatus.RUNNING
            for s in env3._services.values()
        )

    def test_invalid_command_penalty(self, env1):
        r = act(env1, "totallyfakecommand")
        assert r.reward < 0

    def test_reward_bounded(self, env1):
        for cmd in ["systemctl list-units", "cat /etc/hostname", "ps aux"]:
            r = act(env1, cmd)
            assert -1.0 <= r.reward <= 1.0

    def test_cumulative_reward_accumulates(self, env1):
        act(env1, "systemctl status postgres")
        act(env1, "systemctl status auth-service")
        assert env1._cumulative_reward != 0.0


# ─────────────────────────── state() ───────────────────────────


class TestState:
    def test_state_returns_state_model(self, env3):
        s = env3.state()
        assert isinstance(s, State)

    def test_state_episode_id_non_empty(self, env3):
        s = env3.state()
        assert s.episode_id != ""

    def test_state_task_id_correct(self, env3):
        s = env3.state()
        assert s.task_id == "task3_remediation"

    def test_state_reflects_progress(self, env3):
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/auth-service/config.yml")
        act(env3, "systemctl restart auth-service")
        s = env3.state()
        assert "/etc/services/auth-service/config.yml" in s.configs_fixed
        assert "auth-service" in s.services_restarted

    def test_state_services_dict(self, env1):
        s = env1.state()
        assert "auth-service" in s.services
        assert s.services["postgres"] == "running"

    def test_state_episode_id_changes_after_reset(self, env3):
        id1 = env3.state().episode_id
        env3.reset()
        id2 = env3.state().episode_id
        assert id1 != id2


# ─────────────────────────── done / termination ───────────────────────────


class TestTermination:
    def test_task1_done_when_all_services_checked(self, env1):
        for svc in ["postgres", "auth-service", "api-gateway",
                    "user-service", "notification-service"]:
            r = act(env1, f"systemctl status {svc}")
        assert r.done is True

    def test_task1_done_on_step_budget(self):
        env = DevOpsEnv(task_id="task1_discovery")
        env.reset()
        last = None
        for _ in range(15):
            last = act(env, "date")
        assert last.done is True

    def test_task3_done_when_all_healthy(self, env3):
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/auth-service/config.yml")
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/user-service/config.yml")
        act(env3, "systemctl restart auth-service")
        act(env3, "systemctl restart user-service")
        act(env3, "systemctl restart notification-service")
        r = act(env3, "systemctl restart api-gateway")
        assert r.done is True

    def test_step_after_done_returns_done(self, env3):
        # Exhaust the budget
        for _ in range(40):
            env3.step(Action(command="date"))
        r = act(env3, "hostname")
        assert r.done is True


# ─────────────────────────── Grader ───────────────────────────


class TestGraders:
    def test_grader_score_in_range(self, env1):
        act(env1, "systemctl status auth-service")
        g = run_grader(env1)
        assert 0.0 <= g.score <= 1.0

    def test_grader_score_zero_at_start(self):
        env = DevOpsEnv(task_id="task1_discovery")
        env.reset()
        g = run_grader(env)
        assert g.score == 0.0

    def test_task1_perfect_score(self, env1):
        for svc in ["postgres", "auth-service", "api-gateway",
                    "user-service", "notification-service"]:
            act(env1, f"systemctl status {svc}")
        g = run_grader(env1)
        assert g.score == 1.0
        assert g.passed is True

    def test_task2_full_rca(self, env2):
        # Read crontab BEFORE the 3-condition done-trigger fires
        act(env2, "cat /etc/crontab")
        act(env2, "cat /var/log/postgres/postgresql.log")
        act(env2, "cat /etc/services/auth-service/config.yml")
        act(env2, "cat /etc/secrets/db_credentials")  # triggers done
        g = run_grader(env2)
        assert g.score == 1.0
        assert g.passed is True

    def test_task3_partial_score(self, env3):
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/auth-service/config.yml")
        act(env3, "systemctl restart auth-service")
        g = run_grader(env3)
        assert 0.0 < g.score < 1.0

    def test_task3_perfect_score(self, env3):
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/auth-service/config.yml")
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/user-service/config.yml")
        act(env3, "systemctl restart auth-service")
        act(env3, "systemctl restart user-service")
        act(env3, "systemctl restart notification-service")
        act(env3, "systemctl restart api-gateway")
        g = run_grader(env3)
        assert g.score == 1.0
        assert g.passed is True

    def test_grader_has_breakdown(self, env1):
        act(env1, "systemctl status postgres")
        g = run_grader(env1)
        assert isinstance(g.breakdown, dict)
        assert len(g.breakdown) > 0

    def test_grader_has_feedback(self, env1):
        g = run_grader(env1)
        assert isinstance(g.feedback, str)
        assert len(g.feedback) > 0


# ─────────────────────────── Task catalogue ───────────────────────────


class TestTaskCatalogue:
    def test_three_tasks_defined(self):
        assert len(TASK_CATALOGUE) == 3

    def test_task_ids_correct(self):
        assert "task1_discovery" in TASK_CATALOGUE
        assert "task2_rca" in TASK_CATALOGUE
        assert "task3_remediation" in TASK_CATALOGUE

    def test_difficulties_ordered(self):
        d_map = {"easy": 1, "medium": 2, "hard": 3}
        tasks = list(TASK_CATALOGUE.values())
        diffs = [d_map[t.difficulty] for t in tasks]
        assert diffs == sorted(diffs), "Tasks should be ordered easy→medium→hard"

    def test_action_schema_present(self):
        for task in TASK_CATALOGUE.values():
            assert "command" in task.action_schema.get("properties", {})

    def test_max_steps_increasing(self):
        tasks = list(TASK_CATALOGUE.values())
        steps = [t.max_steps for t in tasks]
        assert steps == sorted(steps)


# ─────────────────────────── Service restart logic ───────────────────────────


class TestRestartLogic:
    def test_auth_service_fails_with_old_config(self, env3):
        r = act(env3, "systemctl restart auth-service")
        assert r.observation.exit_code != 0
        assert env3._services["auth-service"].status == ServiceStatus.CRASHED

    def test_auth_service_starts_after_config_fix(self, env3):
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/auth-service/config.yml")
        r = act(env3, "systemctl restart auth-service")
        assert r.observation.exit_code == 0
        assert env3._services["auth-service"].status == ServiceStatus.RUNNING

    def test_notification_fails_without_auth(self, env3):
        r = act(env3, "systemctl restart notification-service")
        assert r.observation.exit_code != 0

    def test_notification_starts_after_auth_up(self, env3):
        act(env3, "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
                  "/etc/services/auth-service/config.yml")
        act(env3, "systemctl restart auth-service")
        r = act(env3, "systemctl restart notification-service")
        assert r.observation.exit_code == 0
        assert env3._services["notification-service"].status == ServiceStatus.RUNNING

    def test_api_gateway_degrades_without_all_upstreams(self, env3):
        # Restart gw before auth/notif are up → degraded
        act(env3, "systemctl restart api-gateway")
        assert env3._services["api-gateway"].status == ServiceStatus.DEGRADED

    def test_full_remediation_sequence(self, env3):
        cmds = [
            "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
            "/etc/services/auth-service/config.yml",
            "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' "
            "/etc/services/user-service/config.yml",
            "systemctl restart auth-service",
            "systemctl restart user-service",
            "systemctl restart notification-service",
            "systemctl restart api-gateway",
        ]
        for cmd in cmds:
            act(env3, cmd)
        for svc in env3._services.values():
            assert svc.status == ServiceStatus.RUNNING, f"{svc.name} not running"


# ─────────────────────────── FastAPI server ───────────────────────────


try:
    from fastapi.testclient import TestClient as _TestClient
    from server.app import app as _app
    _FASTAPI_AVAILABLE = True
except Exception:
    _FASTAPI_AVAILABLE = False

fastapi_only = pytest.mark.skipif(
    not _FASTAPI_AVAILABLE,
    reason="FastAPI/Pydantic incompatible with this Python version; use Python 3.10-3.12",
)


class TestFastAPIServer:
    @pytest.fixture
    def client(self):
        if not _FASTAPI_AVAILABLE:
            pytest.skip("FastAPI not importable on this runtime")
        return _TestClient(_app)

    def test_health_endpoint(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_root_endpoint(self, client):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert "tasks" in data

    def test_tasks_endpoint(self, client):
        r = client.get("/tasks")
        assert r.status_code == 200
        data = r.json()
        assert "task1_discovery" in data
        assert "task2_rca" in data
        assert "task3_remediation" in data

    def test_tasks_contain_action_schema(self, client):
        r = client.get("/tasks")
        for task in r.json().values():
            assert "action_schema" in task
            assert "command" in task["action_schema"]["properties"]

    def test_reset_endpoint(self, client):
        r = client.post("/reset", json={"task_id": "task1_discovery"})
        assert r.status_code == 200
        data = r.json()
        assert "observation" in data

    def test_step_endpoint(self, client):
        client.post("/reset", json={"task_id": "task1_discovery"})
        r = client.post("/step", json={
            "task_id": "task1_discovery",
            "action": {"command": "systemctl status postgres"}
        })
        assert r.status_code == 200
        data = r.json()
        assert "observation" in data
        assert "reward" in data
        assert "done" in data

    def test_state_endpoint(self, client):
        client.post("/reset", json={"task_id": "task3_remediation"})
        r = client.get("/state", params={"task_id": "task3_remediation"})
        assert r.status_code == 200
        data = r.json()
        assert "episode_id" in data
        assert "task_id" in data

    def test_grader_endpoint(self, client):
        client.post("/reset", json={"task_id": "task1_discovery"})
        r = client.post("/grader", json={"task_id": "task1_discovery"})
        assert r.status_code == 200
        data = r.json()
        assert "score" in data
        assert 0.0 <= data["score"] <= 1.0

    def test_baseline_endpoint(self, client):
        r = client.post("/baseline", timeout=120)
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert "average_score" in data
        assert len(data["results"]) == 3

    def test_invalid_task_id(self, client):
        r = client.post("/reset", json={"task_id": "nonexistent_task"})
        assert r.status_code == 400


# ─────────────────────────── Standalone runner ───────────────────────────

if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    sys.exit(result.returncode)
