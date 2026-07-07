"""PLAN phase: a read-only agent explores the project and produces a plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .agents import READ_ONLY_TOOLS, AgentRun, run_agent
from .config import Config

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "success_criteria": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "steps", "success_criteria"],
    "additionalProperties": False,
}


@dataclass
class Plan:
    summary: str
    steps: list[str]
    success_criteria: list[str]
    risks: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def render(self) -> str:
        lines = [f"Plan: {self.summary}", "", "Steps:"]
        lines += [f"{i}. {s}" for i, s in enumerate(self.steps, 1)]
        lines += ["", "Success criteria:"]
        lines += [f"- {c}" for c in self.success_criteria]
        if self.risks:
            lines += ["", "Known risks:"] + [f"- {r}" for r in self.risks]
        return "\n".join(lines)


def _system(genome_section: str) -> str:
    return (
        "You are the PLANNER phase of oh-my-somnia, a self-improving agent "
        "harness. Explore the project read-only, then produce a concrete, "
        "verifiable execution plan for the given task. Keep steps actionable "
        "and success criteria objectively checkable.\n\n" + genome_section
    )


async def make_plan(task: str, *, cwd: Path, genome_section: str,
                    cfg: Config, fitness_command: str) -> tuple[Plan, AgentRun]:
    fitness_note = (
        f'The result will be verified by running: `{fitness_command}` '
        "(exit code 0 = pass). Plan with that in mind.\n"
        if fitness_command else
        "The result will be scored by an AI judge against the success criteria.\n"
    )
    prompt = (
        f"Task:\n{task}\n\n{fitness_note}"
        "Explore the project as needed, then output the plan."
    )
    run = await run_agent(
        prompt,
        system=_system(genome_section),
        cwd=cwd,
        allowed_tools=READ_ONLY_TOOLS,
        permission_mode="dontAsk",
        schema=PLAN_SCHEMA,
        max_turns=cfg.planner_max_turns,
        model=cfg.model,
        max_budget_usd=cfg.max_budget_usd,
    )
    data = run.structured or {}
    plan = Plan(
        summary=data.get("summary", task),
        steps=data.get("steps") or ["Complete the task directly."],
        success_criteria=data.get("success_criteria") or ["The task is done as described."],
        risks=data.get("risks") or [],
        cost_usd=run.cost_usd,
    )
    return plan, run
