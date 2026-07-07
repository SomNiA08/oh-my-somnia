"""Configuration loading for oh-my-somnia.

Precedence (later wins): built-in defaults < ~/.oh-my-somnia/config.toml
< <project>/.somnia/config.toml < CLI flags.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


def somnia_home() -> Path:
    """Global state home (genome, history, sandboxes).

    Prefers OH_MY_SOMNIA_HOME; the pre-rename OH_MY_DARWIN_HOME is still
    honored. On first use this silently migrates a pre-rename ~/.oh-my-darwin
    directory, so learned genome/history survive the project rename."""
    for env in ("OH_MY_SOMNIA_HOME", "OH_MY_DARWIN_HOME"):
        value = os.environ.get(env)
        if value:
            return Path(value)
    home = Path.home() / ".oh-my-somnia"
    legacy = Path.home() / ".oh-my-darwin"
    if not home.exists() and legacy.is_dir():
        try:
            legacy.rename(home)
        except OSError:
            return legacy  # e.g. in use — keep working from the old location
    return home


def project_dir(project_root: Path) -> Path:
    """Per-project state dir: .somnia/ (new name), falling back to a
    pre-rename .darwin/ when that's what the project already has."""
    new = project_root / ".somnia"
    legacy = project_root / ".darwin"
    if new.exists() or not legacy.exists():
        return new
    return legacy


def genome_dir() -> Path:
    return somnia_home() / "genome"


def history_path() -> Path:
    return somnia_home() / "history.jsonl"


def sandbox_root() -> Path:
    return somnia_home() / "sandboxes"


# NOTE: matching is by bare directory/file NAME at any depth. Deliberately
# excludes overly generic names like "env" (a common source-dir name);
# ".venv"/"venv" cover Python virtualenvs. Use `unignore` in config.toml to
# drop any of these defaults for a project where the name is real source.
DEFAULT_IGNORES = [
    ".git",
    ".hg",
    ".svn",
    ".somnia",
    ".darwin",  # pre-rename project state dir
    "node_modules",
    ".venv",
    "venv",
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
    judge_max_turns: int = 40      # read-only inspection caps — real repos
    diagnoser_max_turns: int = 40  # need more than toy projects
    max_budget_usd: float = 0.0  # 0 = unlimited, applies per agent call
    permission_mode: str = "acceptEdits"  # executor permission mode

    # Sandbox
    # "auto" uses a git worktree when the project is a git repo (fast, no
    # full copy), otherwise a directory copy. Force with "worktree" / "copy".
    sandbox: str = "auto"
    in_place: bool = False     # run in the real project instead of a sandbox
    keep_sandboxes: bool = False
    extra_ignores: list[str] = field(default_factory=list)
    unignore: list[str] = field(default_factory=list)  # names to drop from defaults

    # Where mutated genes are written: "global" (~/.oh-my-somnia/genome)
    # or "project" (<project>/.somnia/genome)
    scope: str = "global"

    @property
    def ignores(self) -> set[str]:
        return (set(DEFAULT_IGNORES) - set(self.unignore)) | set(self.extra_ignores)


class ConfigError(Exception):
    """Raised for invalid config values — fail loudly at load time instead of
    silently misbehaving deep inside a run."""


_CHOICE_FIELDS = {
    "permission_mode": {"default", "acceptEdits", "plan", "dontAsk",
                        "bypassPermissions"},
    "scope": {"global", "project"},
    "sandbox": {"auto", "copy", "worktree"},
}


def _apply(cfg: Config, data: dict, source: str = "config") -> None:
    valid = {f.name for f in fields(Config)}
    for key, value in data.items():
        k = key.replace("-", "_")
        if k not in valid or value is None:
            continue
        current = getattr(cfg, k)
        if isinstance(current, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"{source}: '{key}' expects true/false, "
                                  f"got {value!r}")
        elif isinstance(current, int):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigError(f"{source}: '{key}' expects an integer, "
                                  f"got {value!r}")
        elif isinstance(current, float):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"{source}: '{key}' expects a number, "
                                  f"got {value!r}")
            value = float(value)
        elif isinstance(current, list):
            if not isinstance(value, list) or not all(
                    isinstance(x, str) for x in value):
                raise ConfigError(f"{source}: '{key}' expects a list of "
                                  f"strings like [\"a\", \"b\"], got {value!r}")
        else:  # str
            if not isinstance(value, str):
                raise ConfigError(f"{source}: '{key}' expects a string, "
                                  f"got {value!r}")
        setattr(cfg, k, value)


def load_config(project_root: Path, overrides: dict | None = None) -> Config:
    cfg = Config()
    for path in (somnia_home() / "config.toml",
                 project_dir(project_root) / "config.toml"):
        if path.is_file():
            with open(path, "rb") as f:
                _apply(cfg, tomllib.load(f), source=str(path))
    if overrides:
        _apply(cfg, {k: v for k, v in overrides.items() if v is not None},
               source="command line")
    for key, allowed in _CHOICE_FIELDS.items():
        if getattr(cfg, key) not in allowed:
            raise ConfigError(f"'{key}' must be one of {sorted(allowed)}, "
                              f"got {getattr(cfg, key)!r}")
    return cfg


CONFIG_TEMPLATE = """\
# oh-my-somnia project config (.somnia/config.toml)

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
# changes and are much faster on large repositories. They also work when you
# run somnia from a monorepo subdirectory (scoped to that subdirectory); when
# that subdirectory is only a small slice of a big enclosing repo, "auto"
# copies just the subdirectory instead of checking out the whole tree.
sandbox = "auto"

# Where learned heuristics are stored: "global" shares across all projects,
# "project" keeps them in .somnia/genome/.
scope = "global"

# Extra directory names to exclude from sandbox copies.
# extra_ignores = ["data", "models"]

# Default-ignored names to re-include (e.g. a real source dir named "build").
# unignore = ["build"]
"""
