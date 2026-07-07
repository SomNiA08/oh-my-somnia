"""DIAGNOSE phase: root-cause analysis of a failed generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agents import READ_ONLY_TOOLS, run_agent
from .config import Config
from .evaluator import Fitness
from .planner import Plan

DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "category": {
            "type": "string",
            "enum": ["planning", "execution", "knowledge",
                     "environment", "fitness", "other"],
        },
        "evidence": {"type": "string"},
        "lesson": {"type": "string"},
    },
    "required": ["root_cause", "category", "lesson"],
    "additionalProperties": False,
}


@dataclass
class Diagnosis:
    root_cause: str
    category: str
    evidence: str
    lesson: str
    cost_usd: float = 0.0


async def diagnose(task: str, plan: Plan, fitness: Fitness, transcript_tail: str,
                   *, cwd: Path, cfg: Config) -> Diagnosis:
    prompt = (
        "You are the DIAGNOSER phase of oh-my-somnia, a self-improving agent "
        "harness. A generation just FAILED. Find the true root cause — not "
        "the surface symptom — and extract a lesson the harness can reuse on "
        "future tasks. You may inspect the workspace read-only.\n\n"
        f"Task:\n{task}\n\n"
        f"{plan.render()}\n\n"
        f"Fitness result: {fitness.render()}\n"
        f"Fitness command output (tail):\n```\n{fitness.command_output[-2000:]}\n```\n"
        f"Judge reasoning: {fitness.judge_reasoning[:1500]}\n\n"
        f"Executor transcript (tail):\n```\n{transcript_tail[-6000:]}\n```\n\n"
        "Categories: planning (bad plan), execution (agent mistakes), "
        "knowledge (missing know-how), environment (tooling/setup), "
        "fitness (the check itself is wrong), other.\n"
        "`lesson` must be a generalizable, imperative heuristic that would "
        "have prevented this failure — not a description of this one bug."
    )
    run = await run_agent(
        prompt,
        cwd=cwd,
        allowed_tools=READ_ONLY_TOOLS,
        permission_mode="dontAsk",
        schema=DIAGNOSIS_SCHEMA,
        max_turns=20,
        model=cfg.model,
        max_budget_usd=cfg.max_budget_usd,
    )
    data = run.structured or {}
    return Diagnosis(
        root_cause=data.get("root_cause", "unknown"),
        category=data.get("category", "other"),
        evidence=data.get("evidence", ""),
        lesson=data.get("lesson", ""),
        cost_usd=run.cost_usd,
    )
