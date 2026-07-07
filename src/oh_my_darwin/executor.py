"""EXECUTE phase: an agent carries out the plan inside the sandbox."""

from __future__ import annotations

from pathlib import Path

from .agents import EXECUTOR_TOOLS, AgentRun, run_agent
from .config import Config
from .planner import Plan


def _system(genome_section: str) -> str:
    return (
        "You are the EXECUTOR phase of oh-my-darwin, a self-improving agent "
        "harness. Carry out the given plan in this workspace. Work "
        "autonomously — nobody can answer questions mid-task. Verify your own "
        "work before finishing: run the verification command or exercise the "
        "code you changed and read the real output.\n\n" + genome_section
    )


async def execute(task: str, plan: Plan, *, cwd: Path, genome_section: str,
                  cfg: Config, fitness_command: str) -> AgentRun:
    fitness_note = (
        f"Verification command (must exit 0 when you are done): `{fitness_command}`\n"
        if fitness_command else ""
    )
    prompt = (
        f"Task:\n{task}\n\n"
        f"{plan.render()}\n\n"
        f"{fitness_note}"
        "Execute the plan now. Adapt if a step turns out to be wrong, but keep "
        "the success criteria as the goal. When finished, summarize what you "
        "changed and how you verified it."
    )
    return await run_agent(
        prompt,
        system=_system(genome_section),
        cwd=cwd,
        allowed_tools=EXECUTOR_TOOLS,
        permission_mode=cfg.permission_mode,
        max_turns=cfg.max_turns,
        model=cfg.executor_model or cfg.model,
        max_budget_usd=cfg.max_budget_usd,
    )
