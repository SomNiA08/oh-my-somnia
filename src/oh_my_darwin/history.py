"""Run history: one JSON line per run in ~/.oh-my-darwin/history.jsonl."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from .config import history_path


def record(entry: dict[str, Any]) -> None:
    entry = {"timestamp": _dt.datetime.now().isoformat(timespec="seconds"), **entry}
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read(limit: int | None = None, project: str | None = None) -> list[dict[str, Any]]:
    path = history_path()
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if project and entry.get("project") != project:
                continue
            entries.append(entry)
    if limit is None:
        return entries
    return entries[-limit:] if limit > 0 else []
