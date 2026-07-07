"""Thin wrapper over the Claude Agent SDK.

Every phase of the harness (plan, execute, judge, diagnose, mutate, evolve)
goes through run_agent(), which returns the final text, optional structured
output, and a transcript usable for later diagnosis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

try:  # tool blocks are useful for the transcript but not load-bearing
    from claude_agent_sdk import ToolUseBlock
except ImportError:  # pragma: no cover
    ToolUseBlock = None

READ_ONLY_TOOLS = ["Read", "Glob", "Grep"]
EXECUTOR_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep", "Bash",
    "WebSearch", "WebFetch", "TodoWrite", "NotebookEdit",
]

TRANSCRIPT_LIMIT = 12_000  # chars kept for diagnosis context


@dataclass
class AgentRun:
    text: str = ""
    structured: Any = None
    transcript: str = ""
    cost_usd: float = 0.0
    num_turns: int = 0
    is_error: bool = False
    subtype: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def transcript_tail(self) -> str:
        return self.transcript[-TRANSCRIPT_LIMIT:]


async def run_agent(
    prompt: str,
    *,
    system: str | None = None,
    cwd: Path | str | None = None,
    allowed_tools: list[str] | None = None,
    permission_mode: str = "dontAsk",
    schema: dict | None = None,
    max_turns: int | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
) -> AgentRun:
    options = ClaudeAgentOptions(
        system_prompt=system,
        cwd=str(cwd) if cwd else None,
        allowed_tools=allowed_tools or [],
        permission_mode=permission_mode,
        max_turns=max_turns,
        model=model or None,
        max_budget_usd=max_budget_usd or None,
        # Reproducibility: don't let user/project Claude Code settings leak in.
        setting_sources=[],
        output_format={"type": "json_schema", "schema": schema} if schema else None,
    )

    run = AgentRun()
    parts: list[str] = []

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
                    elif ToolUseBlock is not None and isinstance(block, ToolUseBlock):
                        try:
                            arg = json.dumps(block.input, ensure_ascii=False)[:200]
                        except Exception:
                            arg = "?"
                        parts.append(f"[tool:{block.name}] {arg}")
            elif isinstance(message, ResultMessage):
                run.text = message.result or ""
                run.structured = message.structured_output
                run.cost_usd = message.total_cost_usd or 0.0
                run.num_turns = message.num_turns
                run.is_error = message.is_error
                run.subtype = message.subtype
                run.errors = list(message.errors or [])
    except Exception as exc:
        # When the CLI emits an error result (error_max_turns,
        # error_during_execution, ...) it exits non-zero and the SDK
        # re-raises that at end of stream — usually AFTER the ResultMessage
        # already arrived. Degrade to an is_error AgentRun so each phase can
        # handle its own failure (e.g. the judge falls back to the command
        # signal) instead of aborting the whole evolutionary run.
        run.is_error = True
        if not run.subtype:
            run.subtype = "sdk_error"
        run.errors.append(str(exc)[:500])

    run.transcript = "\n".join(parts)
    if not run.text:
        # Fall back to the last text block if the CLI didn't set `result`.
        texts = [p for p in parts if not p.startswith("[tool:")]
        run.text = texts[-1] if texts else ""
    return run
