"""
DevOps Incident Responder — OpenEnv Environment
================================================

A production-grade OpenEnv environment simulating a DevOps incident:
an automated database password rotation broke a microservice cluster.

Quick start
-----------
    from environment import DevOpsEnv
    from models import Action

    env = DevOpsEnv(task_id="task3_remediation")
    result = env.reset()

    step = env.step(Action(command="systemctl list-units --type=service"))
    print(step.observation.stdout)
    print(step.reward)
"""
from environment import DevOpsEnv
from models import Action, Observation, State, StepResult, ResetResult

__all__ = ["DevOpsEnv", "Action", "Observation", "State", "StepResult", "ResetResult"]
__version__ = "1.0.0"
