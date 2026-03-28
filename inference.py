"""
inference.py — entry-point script for the DevOps Incident Responder OpenEnv environment.

Runs the heuristic baseline agent against all tasks and reports scores.
No API key required.

Usage:
    python inference.py
    python inference.py --task task3_remediation
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from environment import DevOpsEnv, TASKS
from models import Action
from tasks import run_grader
from scripts import BASELINE_SCRIPTS


def run_inference(task_id: str = "task3_remediation"):
    """Run heuristic agent on a single task, return GraderResult."""
    env = DevOpsEnv(task_id=task_id)
    env.reset()
    commands = BASELINE_SCRIPTS.get(task_id, [])
    for cmd in commands:
        result = env.step(Action(command=cmd))
        if result.done:
            break
    return run_grader(env)


def main() -> None:
    parser = argparse.ArgumentParser(description="DevOps Incident Responder — inference runner")
    parser.add_argument("--task", default=None, help="Run a single task (default: all)")
    args = parser.parse_args()

    task_ids = [args.task] if args.task else list(TASKS.keys())

    print("\nDevOps Incident Responder — Inference")
    print("=" * 40)
    scores = []
    for task_id in task_ids:
        if task_id not in TASKS:
            print(f"  Unknown task: {task_id}")
            continue
        if task_id not in BASELINE_SCRIPTS:
            print(f"  {task_id}: no baseline script available")
            continue
        grade = run_inference(task_id)
        status = "PASS" if grade.passed else "FAIL"
        print(f"  {task_id}: {grade.score:.4f}  [{status}]  {grade.feedback}")
        scores.append(grade.score)

    if scores:
        print(f"\n  Average score: {sum(scores) / len(scores):.4f}")
    print()


if __name__ == "__main__":
    main()
