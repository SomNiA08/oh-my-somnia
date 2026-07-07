"""Configuration loading for oh-my-darwin.

Precedence (later wins): built-in defaults < ~/.oh-my-darwin/config.toml
< <project>/.darwin/config.toml < CLI flags.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


def darwin_home() -> Path:
    return Path(os.environ.get("OH_MY_DARWIN_HOME", Path.home() / ".oh-my-darwin"))


def genome_dir() -> Path:
    return darwin_home() / "genome"


def history_path() -> Path:
    return darwin_home() / "history.jsonl"


def sandbox_root() -> Path:
    return darwin_home() / "sandboxes"


DEFAULT_IGNORES = [
    ".git",
    ".hg",
    ".svn",
    ".darwin",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    ".gradle",
    ".idea",
    ".vscode",
]


@dataclass
class Config:
    # How fitness is measured. Shell command whose exit code decides pass/fail
    # (e.g. "python -m pytest -q"). Empty string means "AI judge only".
    fitness_command: str = ""
    fitness_timeout: int = 600
    # Whether an AI judge also scores the outcome (always on when no command).
    judge: bool = True

    # Evolution loop
    generations: int = 3

    # Agent settings
    model: str = ""            # "" = inherit Claude Code default
    executor_model: str = ""   # override just for the executor phase
    max_turns: int = 60        # executor turn cap
    planner_max_turns: int = 15
    max_budget_usd: float = 0.0  # 0 = unlimited, applies per agent call
    permission_mode: str = "acceptEdits"  # executor permission mode

    # Sandbox
    # "auto" uses a git worktree when the project is a git repo (fast, no
    # full copy), otherwise a directory copy. Force with "worktree" / "copy".
    sandbox: str = "auto"
    in_place: bool = False     # run in the real project instead of a sandbox
    keep_sandboxes: bool = False
    extra_ignores: list[str] = field(default_factory=list)

    # Where mutated genes are written: "global" (~/.oh-my-darwin/genome)
    # or "project" (<project>/.darwin/genome)
    scope: str = "global"

    @property
    def ignores(self) -> set[str]:
        return set(DEFAULT_IGNORES) | set(self.extra_ignores)


def _apply(cfg: Config, data: dict) -> None:
    valid = {f.name for f in fields(Config)}
    for key, value in data.items():
        k = key.replace("-", "_")
        if k in valid and value is not None:
            setattr(cfg, k, value)


def load_config(project_root: Path, overrides: dict | None = None) -> Config:
    cfg = Config()
    for path in (darwin_home() / "config.toml", project_root / ".darwin" / "config.toml"):
        if path.is_file():
            with open(path, "rb") as f:
                _apply(cfg, tomllib.load(f))
    if overrides:
        _apply(cfg, {k: v for k, v in overrides.items() if v is not None})
    return cfg


CONFIG_TEMPLATE = """\
# oh-my-darwin project config (.darwin/config.toml)

# Shell command that decides pass/fail via exit code. Runs with cwd at the
# (sandboxed) project root. Leave empty to use the AI judge alone.
# Make sure it resolves outside the project tree too (e.g. "python -m pytest -q",
# not ".venv/bin/pytest") because sandboxes exclude virtualenvs by default.
fitness_command = ""

# Also have an AI judge score the outcome (0-100) alongside the command.
judge = true

# Max evolution generations per run (1 = no self-improvement retries).
generations = 3

# Executor agent limits.
max_turns = 60
# max_budget_usd = 2.0        # per agent call; omit for unlimited
# model = "claude-sonnet-4-6" # omit to inherit your Claude Code default
# permission_mode = "acceptEdits"  # or "bypassPermissions" (sandbox only!)

# Sandbox backend: "auto" (git worktree when possible, else copy),
# "worktree", or "copy". Worktrees start from HEAD plus your uncommitted
# changes and are much faster on large repositories.
sandbox = "auto"

# Where learned heuristics are stored: "global" shares across all projects,
# "project" keeps them in .darwin/genome/.
scope = "global"

# Extra directory names to exclude from sandbox copies.
# extra_ignores = ["data", "models"]
"""
