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
        if self.notes:
            parts.append(f"({'; '.join(self.notes)})")
        return " ".join(parts)


def run_fitness_command(command: str, cwd: Path, timeout: int) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            # Explicit UTF-8: on Korean-locale Windows the default is cp949,
            # which mangles the UTF-8 output most dev tools emit — and that
            # garbled text would feed the judge/diagnoser prompts.
            command, shell=True, cwd=str(cwd), timeout=timeout,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"fitness command timed out after {timeout}s"
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode == 0, output[-OUTPUT_TAIL:]


async def judge(task: str, plan: Plan, *, cwd: Path, cfg: Config,
                command_result: tuple[bool, str] | None
                ) -> tuple[bool | None, int | None, str, float]:
    """Returns (passed, score, reasoning, cost). passed/score are None when
    the judge agent itself failed (max turns, budget, API error) — that is an
    infrastructure failure, NOT a 0/100 verdict, and must not be counted as
    a fitness failure."""
    cmd_note = ""
    if command_result is not None:
        status = "PASSED" if command_result[0] else "FAILED"
        cmd_note = (
            f"\nThe objective fitness command {status}. Its output (tail):\n"
            f"```\n{command_result[1][-1500:]}\n```\n"
        )
    prompt = (
        "You are the JUDGE phase of oh-my-somnia. Inspect this workspace "
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
    data = run.structured
    if not isinstance(data, dict) or "score" not in data:
        reason = run.subtype or "no structured output"
        return None, None, f"judge unavailable ({reason})", run.cost_usd
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
    j_score: int | None = None
    use_judge = cfg.judge or command_result is None
    if use_judge:
        j_passed, j_score, j_reason, j_cost = await judge(
            task, plan, cwd=cwd, cfg=cfg, command_result=command_result
        )
        fitness.judge_score = j_score
        fitness.judge_reasoning = j_reason
        fitness.cost_usd += j_cost
        if j_score is None:
            # Judge infrastructure failure: fall back to the command signal
            # alone instead of poisoning selection with a fake 0/100.
            fitness.notes.append(f"judge unavailable: {j_reason}")

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
    elif j_score is None:
        # No command AND no judge verdict: the generation cannot be evaluated.
        # Report it as unevaluated rather than a genuine failure.
        fitness.passed = False
        fitness.score = 0.0
        fitness.notes.append("no fitness signal at all — treat with suspicion")
    else:
        fitness.passed = bool(j_passed)
        fitness.score = (fitness.judge_score or 0) / 100
    return fitness
