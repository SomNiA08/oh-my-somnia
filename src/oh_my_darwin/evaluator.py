"""EVALUATE phase: fitness = objective command result + optional AI judge."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .agents import READ_ONLY_TOOLS, run_agent
from .config import Config
from .planner import Plan

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "score": {"type": "integer"},
        "reasoning": {"type": "string"},
    },
    "required": ["passed", "score", "reasoning"],
    "additionalProperties": False,
}

OUTPUT_TAIL = 4000


@dataclass
class Fitness:
    passed: bool
    score: float  # 0.0 .. 1.0
    command_passed: bool | None = None
    command_output: str = ""
    judge_score: int | None = None
    judge_reasoning: str = ""
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts = [f"passed={self.passed} score={self.score:.2f}"]
        if self.command_passed is not None:
            parts.append(f"command={'PASS' if self.command_passed else 'FAIL'}")
        if self.judge_score is not None:
            parts.append(f"judge={self.judge_score}/100")
        return " ".join(parts)


def run_fitness_command(command: str, cwd: Path, timeout: int) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd), timeout=timeout,
            capture_output=True, text=True, errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"fitness command timed out after {timeout}s"
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode == 0, output[-OUTPUT_TAIL:]


async def judge(task: str, plan: Plan, *, cwd: Path, cfg: Config,
                command_result: tuple[bool, str] | None) -> tuple[bool, int, str, float]:
    cmd_note = ""
    if command_result is not None:
        status = "PASSED" if command_result[0] else "FAILED"
        cmd_note = (
            f"\nThe objective fitness command {status}. Its output (tail):\n"
            f"```\n{command_result[1][-1500:]}\n```\n"
        )
    prompt = (
        "You are the JUDGE phase of oh-my-darwin. Inspect this workspace "
        "read-only and score how well the task was accomplished.\n\n"
        f"Task:\n{task}\n\n"
        "Success criteria:\n"
        + "\n".join(f"- {c}" for c in plan.success_criteria)
        + cmd_note
        + "\nScore 0-100 (100 = fully satisfies every criterion with good "
        "quality). `passed` means all criteria are genuinely met."
    )
    run = await run_agent(
        prompt,
        cwd=cwd,
        allowed_tools=READ_ONLY_TOOLS,
        permission_mode="dontAsk",
        schema=JUDGE_SCHEMA,
        max_turns=20,
        model=cfg.model,
        max_budget_usd=cfg.max_budget_usd,
    )
    data = run.structured or {}
    return (
        bool(data.get("passed", False)),
        int(data.get("score", 0)),
        str(data.get("reasoning", ""))[:2000],
        run.cost_usd,
    )


async def evaluate(task: str, plan: Plan, *, cwd: Path, cfg: Config) -> Fitness:
    command_result: tuple[bool, str] | None = None
    if cfg.fitness_command:
        command_result = run_fitness_command(
            cfg.fitness_command, cwd, cfg.fitness_timeout
        )

    fitness = Fitness(passed=False, score=0.0)
    if command_result is not None:
        fitness.command_passed, fitness.command_output = command_result

    j_passed: bool | None = None
    use_judge = cfg.judge or command_result is None
    if use_judge:
        j_passed, j_score, j_reason, j_cost = await judge(
            task, plan, cwd=cwd, cfg=cfg, command_result=command_result
        )
        fitness.judge_score = j_score
        fitness.judge_reasoning = j_reason
        fitness.cost_usd += j_cost

    if command_result is not None:
        # The objective command is the gate; the judge refines the score.
        fitness.passed = command_result[0] and (
            fitness.judge_score is None or fitness.judge_score >= 50
        )
        cmd_part = 0.7 if command_result[0] else 0.0
        judge_part = (fitness.judge_score / 100 * 0.3) if fitness.judge_score is not None else (
            0.3 if command_result[0] else 0.0
        )
        fitness.score = cmd_part + judge_part
    else:
        fitness.passed = bool(j_passed)
        fitness.score = (fitness.judge_score or 0) / 100
    return fitness
