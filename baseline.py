"""
Baseline inference script for the DevOps Incident Responder OpenEnv environment.

Runs two agents against all 3 tasks and prints reproducible scores:
  1. Heuristic agent   — deterministic command sequence (no API key required)
  2. OpenAI LLM agent  — GPT-4o / GPT-3.5 driven by system + loop prompt
                         (requires OPENAI_API_KEY env variable)

Usage
─────
    # Heuristic baseline only (no API key needed)
    python baseline.py --heuristic

    # LLM baseline (requires OPENAI_API_KEY)
    python baseline.py --llm

    # Both
    python baseline.py

    # Single task
    python baseline.py --task task3_remediation --llm

Environment variables
─────────────────────
    OPENAI_API_KEY   — OpenAI API key
    OPENAI_BASE_URL  — optional; override base URL (e.g. for Azure or local proxy)
    OPENAI_MODEL     — model to use (default: gpt-4o-mini)
    BASE_URL         — if set, hits a remote server instead of in-process env
                       e.g. BASE_URL=https://your-hf-space.hf.space
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# ──────────────────────────── CLI ────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DevOps Incident Responder — baseline runner")
    p.add_argument("--heuristic", action="store_true", default=False,
                   help="Run the deterministic heuristic baseline")
    p.add_argument("--llm", action="store_true", default=False,
                   help="Run the LLM-based baseline (requires OPENAI_API_KEY)")
    p.add_argument("--task", default=None,
                   help="Run a single task (default: all three)")
    p.add_argument("--verbose", "-v", action="store_true", default=False,
                   help="Print step-by-step command output")
    args = p.parse_args()
    # If neither flag, run both
    if not args.heuristic and not args.llm:
        args.heuristic = True
        args.llm = True
    return args


# ──────────────────────────── In-process env helpers ────────────────────────────

def _make_env(task_id: str):
    """Import and instantiate environment in-process."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from environment import DevOpsEnv
    return DevOpsEnv(task_id=task_id)


def _run_heuristic(task_id: str, verbose: bool = False) -> Dict[str, Any]:
    """Run the pre-scripted heuristic command sequence."""
    from environment import DevOpsEnv
    from models import Action
    from tasks import run_grader
    from scripts import BASELINE_SCRIPTS as _BASELINE_SCRIPTS

    env = DevOpsEnv(task_id=task_id)
    env.reset()
    commands = _BASELINE_SCRIPTS[task_id]

    total_reward = 0.0
    steps = 0
    for cmd in commands:
        result = env.step(Action(command=cmd))
        total_reward += result.reward
        steps += 1
        if verbose:
            print(f"  [{steps:02d}] $ {cmd}")
            if result.observation.stdout.strip():
                for line in result.observation.stdout.strip().splitlines()[:5]:
                    print(f"        {line}")
        if result.done:
            break

    grade = run_grader(env)
    return {
        "agent":    "heuristic",
        "task_id":  task_id,
        "score":    grade.score,
        "passed":   grade.passed,
        "steps":    steps,
        "reward":   round(total_reward, 4),
        "feedback": grade.feedback,
        "breakdown": grade.breakdown,
    }


# ──────────────────────────── LLM agent ────────────────────────────

SYSTEM_PROMPT = """You are an expert DevOps site-reliability engineer performing incident response.
You are connected to a SIMULATED cloud environment via a bash terminal.

Your goal depends on the active task:
  task1_discovery   — Identify the status of every service (easy)
  task2_rca         — Find the root cause of the service failures (medium)
  task3_remediation — Fix the entire cluster so all services are RUNNING (hard)

Rules:
  - Issue ONE bash command per turn, nothing else.
  - Do NOT explain your reasoning — output ONLY the command.
  - Available commands: systemctl, cat, head, tail, grep, ls, find,
    curl, ping, ps, journalctl, sed, echo (with >/>>, for file edits),
    printenv, netstat, df, free, date, uptime, hostname, whoami, uname, top.
  - Use sed -i 's/old/new/g' <file> to edit config files.
  - When you believe the task is complete, output: DONE
"""

USER_TEMPLATE = """
=== System metrics ===
{metrics}

=== Last command output ===
$ {last_cmd}
exit_code: {exit_code}
stdout:
{stdout}
stderr:
{stderr}

=== Step {step}/{max_steps} | Cumulative reward: {reward:.4f} ===
Task: {task_name} ({difficulty})
Objective: {objective}

Issue your next command (or DONE if complete):
"""


def _metrics_summary(metrics: Any) -> str:
    lines = []
    for name, svc in metrics.services.items():
        status = svc.status if isinstance(svc.status, str) else svc.status.value
        err = f" | {svc.error_message}" if svc.error_message else ""
        lines.append(f"  {name:<28} {status.upper()}{err}")
    return "\n".join(lines)


def _run_llm(task_id: str, verbose: bool = False) -> Dict[str, Any]:
    """Run an OpenAI-powered agent against the environment."""
    try:
        from openai import OpenAI
    except ImportError:
        print("  openai package not installed. Run: pip install openai")
        return {"agent": "llm", "task_id": task_id, "score": 0.0,
                "passed": False, "steps": 0, "reward": 0.0,
                "feedback": "openai not installed", "breakdown": {}}

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("  OPENAI_API_KEY not set — skipping LLM baseline.")
        return {"agent": "llm", "task_id": task_id, "score": 0.0,
                "passed": False, "steps": 0, "reward": 0.0,
                "feedback": "OPENAI_API_KEY not set", "breakdown": {}}

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.environ.get("OPENAI_BASE_URL")
    client_kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    from environment import DevOpsEnv, TASKS
    from models import Action
    from tasks import run_grader

    env = DevOpsEnv(task_id=task_id)
    reset_result = env.reset()

    task_cfg = TASKS[task_id]
    max_steps = task_cfg["max_steps"]
    objective = task_cfg["description"]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Seed the first user message
    obs = reset_result.observation
    user_msg = USER_TEMPLATE.format(
        metrics=_metrics_summary(obs.system_metrics),
        last_cmd="(episode start — no command yet)",
        exit_code=0,
        stdout="",
        stderr="",
        step=0,
        max_steps=max_steps,
        task_name=task_cfg["name"],
        difficulty=task_cfg["difficulty"],
        objective=objective,
        reward=0.0,
    )
    messages.append({"role": "user", "content": user_msg})

    total_reward = 0.0
    steps = 0
    last_cmd = ""

    while steps < max_steps:
        # Call the LLM
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=100,
                temperature=0.0,
            )
        except Exception as exc:
            print(f"  LLM API error: {exc}")
            break

        assistant_content = response.choices[0].message.content.strip()
        messages.append({"role": "assistant", "content": assistant_content})

        if assistant_content.upper() == "DONE" or assistant_content == "":
            break

        cmd = assistant_content
        last_cmd = cmd

        if verbose:
            print(f"  [{steps+1:02d}] $ {cmd}")

        result = env.step(Action(command=cmd))
        total_reward += result.reward
        steps += 1

        if verbose and result.observation.stdout.strip():
            for line in result.observation.stdout.strip().splitlines()[:4]:
                print(f"        {line}")

        if result.done:
            break

        # Build next user message
        obs = result.observation
        next_msg = USER_TEMPLATE.format(
            metrics=_metrics_summary(obs.system_metrics),
            last_cmd=cmd,
            exit_code=obs.exit_code,
            stdout=obs.stdout[:800] if obs.stdout else "(no output)",
            stderr=obs.stderr[:400] if obs.stderr else "",
            step=steps,
            max_steps=max_steps,
            task_name=task_cfg["name"],
            difficulty=task_cfg["difficulty"],
            objective=objective,
            reward=total_reward,
        )
        messages.append({"role": "user", "content": next_msg})

    grade = run_grader(env)
    return {
        "agent":    f"llm ({model})",
        "task_id":  task_id,
        "score":    grade.score,
        "passed":   grade.passed,
        "steps":    steps,
        "reward":   round(total_reward, 4),
        "feedback": grade.feedback,
        "breakdown": grade.breakdown,
    }


# ──────────────────────────── Remote mode ────────────────────────────

def _run_remote(base_url: str, task_id: str, commands: List[str],
                verbose: bool = False) -> Dict[str, Any]:
    """Hit a deployed HuggingFace Space via HTTP."""
    try:
        import requests
    except ImportError:
        print("  requests not installed. Run: pip install requests")
        return {}

    base = base_url.rstrip("/")
    requests.post(f"{base}/reset", json={"task_id": task_id}, timeout=30)

    total_reward = 0.0
    steps = 0
    for cmd in commands:
        resp = requests.post(
            f"{base}/step",
            json={"task_id": task_id, "action": {"command": cmd}},
            timeout=30,
        )
        data = resp.json()
        total_reward += data.get("reward", 0.0)
        steps += 1
        if verbose:
            print(f"  [{steps:02d}] $ {cmd}")
        if data.get("done"):
            break

    grade_resp = requests.post(f"{base}/grader", json={"task_id": task_id}, timeout=30)
    grade = grade_resp.json()
    return {
        "agent":    "heuristic (remote)",
        "task_id":  task_id,
        "score":    grade.get("score", 0.0),
        "passed":   grade.get("passed", False),
        "steps":    steps,
        "reward":   round(total_reward, 4),
        "feedback": grade.get("feedback", ""),
        "breakdown": grade.get("breakdown", {}),
    }


# ──────────────────────────── Printer ────────────────────────────

def _print_result(r: Dict[str, Any]) -> None:
    bar = "█" * int(r["score"] * 20) + "░" * (20 - int(r["score"] * 20))
    status = "✅ PASS" if r["passed"] else "❌ FAIL"
    print(f"\n  Task  : {r['task_id']}")
    print(f"  Agent : {r['agent']}")
    print(f"  Score : {r['score']:.4f}  [{bar}]  {status}")
    print(f"  Steps : {r['steps']}  |  Reward: {r['reward']:.4f}")
    print(f"  Info  : {r['feedback']}")
    if r.get("breakdown"):
        print("  Breakdown:")
        for k, v in r["breakdown"].items():
            print(f"    {k:<40} {v:.4f}")


# ──────────────────────────── Main ────────────────────────────

def main() -> None:
    args = _parse_args()
    from environment import TASKS
    from scripts import BASELINE_SCRIPTS as _BASELINE_SCRIPTS

    task_ids = [args.task] if args.task else list(TASKS.keys())
    base_url = os.environ.get("BASE_URL", "")

    all_results: List[Dict[str, Any]] = []

    print("\n" + "=" * 60)
    print("  DevOps Incident Responder — Baseline Evaluation")
    print("=" * 60)

    for task_id in task_ids:
        print(f"\n-- Task: {task_id} --")

        if args.heuristic:
            print("  [heuristic] running...")
            if base_url:
                r = _run_remote(base_url, task_id, _BASELINE_SCRIPTS[task_id], args.verbose)
            else:
                r = _run_heuristic(task_id, args.verbose)
            _print_result(r)
            all_results.append(r)

        if args.llm:
            print("  [llm] running...")
            r = _run_llm(task_id, args.verbose)
            _print_result(r)
            all_results.append(r)

    # Summary
    if all_results:
        heuristic_scores = [r["score"] for r in all_results if "heuristic" in r["agent"]]
        llm_scores = [r["score"] for r in all_results if "llm" in r["agent"]]

        print("\n" + "=" * 60)
        print("  SUMMARY")
        print("=" * 60)
        if heuristic_scores:
            avg_h = sum(heuristic_scores) / len(heuristic_scores)
            print(f"  Heuristic avg score : {avg_h:.4f}  "
                  f"({sum(1 for r in all_results if 'heuristic' in r['agent'] and r['passed'])}/"
                  f"{len(heuristic_scores)} passed)")
        if llm_scores:
            avg_l = sum(llm_scores) / len(llm_scores)
            print(f"  LLM avg score       : {avg_l:.4f}  "
                  f"({sum(1 for r in all_results if 'llm' in r['agent'] and r['passed'])}/"
                  f"{len(llm_scores)} passed)")

    # Save JSON results
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline_results.json")
    with open(out_path, "w") as fh:
        json.dump(all_results, fh, indent=2)
    print(f"\n  Results saved → {out_path}")
    print()


if __name__ == "__main__":
    main()
