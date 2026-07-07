"""Sandboxes: isolated copies of the project where each generation runs.

Two backends share the same change-tracking and merge-back logic:

- "copy":     shutil.copytree of the project (works anywhere).
- "worktree": `git worktree add` of HEAD plus an overlay of the project's
              uncommitted changes — much faster on large repositories.

A sandbox snapshots its tree (content hashes), lets an agent mutate it
freely, and can report/merge exactly what changed. Only the winning
generation's changes are merged back into the real project.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_HASH_BYTES = 50 * 1024 * 1024  # skip hashing files bigger than this


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        if path.stat().st_size > MAX_HASH_BYTES:
            return f"big:{path.stat().st_size}"
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return "unreadable"
    return h.hexdigest()


def _walk(root: Path, ignores: set[str]):
    for path in root.rglob("*"):
        if any(part in ignores for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            # -c core.quotepath=false: emit non-ASCII paths raw (UTF-8),
            # not octal-escaped; git always encodes paths as UTF-8 on Windows.
            ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def worktree_eligible(project_root: Path) -> tuple[bool, str]:
    """A worktree sandbox is only valid when the project root IS the repo
    toplevel. A mere subdirectory of some enclosing repo (e.g. a project under
    a git-managed home directory) would check out the whole enclosing repo —
    wrong tree, wrong merge paths."""
    code, out = _git(project_root, "rev-parse", "--show-toplevel")
    if code != 0:
        return False, "not a git repository"
    toplevel = out.strip()
    try:
        # samefile handles Windows 8.3 short paths, case, and slash direction
        same = os.path.samefile(toplevel, project_root)
    except OSError:
        same = Path(toplevel).resolve() == Path(project_root).resolve()
    if not same:
        return False, f"project is a subdirectory of the repo at {toplevel}"
    code, _ = _git(project_root, "rev-parse", "--verify", "HEAD")
    if code != 0:
        return False, "repository has no commits yet"
    return True, ""


def _dirty_entries(project_root: Path) -> list[tuple[str, str, str | None]]:
    """Parse `git status --porcelain -z` into (status, path, orig_path)."""
    code, out = _git(project_root, "status", "--porcelain",
                     "--untracked-files=all", "-z")
    if code != 0:
        return []
    entries: list[tuple[str, str, str | None]] = []
    tokens = out.split("\0")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if len(token) < 4:
            i += 1
            continue
        status, path = token[:2], token[3:]
        orig = None
        if status[0] in "RC":  # rename/copy carries the original path next
            i += 1
            orig = tokens[i] if i < len(tokens) else None
        entries.append((status, path, orig))
        i += 1
    return entries


@dataclass
class Change:
    relpath: str
    kind: str  # "added" | "modified" | "deleted"


@dataclass
class Sandbox:
    project_root: Path
    path: Path
    ignores: set[str]
    kind: str = "copy"  # "copy" | "worktree"
    snapshot: dict[str, str] = field(default_factory=dict)

    # -- creation ----------------------------------------------------------

    @classmethod
    def create(cls, project_root: Path, base: Path, name: str,
               ignores: set[str], mode: str = "auto") -> "Sandbox":
        """Create a sandbox. mode: "auto" | "copy" | "worktree"."""
        if mode not in ("auto", "copy", "worktree"):
            raise ValueError(f"unknown sandbox mode: {mode}")
        if mode != "copy":
            eligible, reason = worktree_eligible(project_root)
            if eligible:
                try:
                    return cls._create_worktree(project_root, base, name, ignores)
                except RuntimeError:
                    if mode == "worktree":
                        raise
                    # auto: fall back to a plain copy
            elif mode == "worktree":
                raise RuntimeError(f"worktree sandbox unavailable: {reason}")
        return cls._create_copy(project_root, base, name, ignores)

    @classmethod
    def _create_copy(cls, project_root: Path, base: Path, name: str,
                     ignores: set[str]) -> "Sandbox":
        target = base / name
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            project_root, target,
            ignore=shutil.ignore_patterns(*ignores),
            symlinks=True,
        )
        sb = cls(project_root=project_root, path=target, ignores=ignores,
                 kind="copy")
        sb._take_snapshot()
        return sb

    @classmethod
    def _create_worktree(cls, project_root: Path, base: Path, name: str,
                         ignores: set[str]) -> "Sandbox":
        eligible, reason = worktree_eligible(project_root)
        if not eligible:
            raise RuntimeError(f"worktree sandbox unavailable: {reason}")
        target = base / name
        if target.exists():
            cls._remove_worktree(project_root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        code, out = _git(project_root, "worktree", "add", "--detach",
                         str(target), "HEAD")
        if code != 0:
            raise RuntimeError(f"git worktree add failed: {out.strip()[:400]}")

        sb = cls(project_root=project_root, path=target, ignores=ignores,
                 kind="worktree")
        sb._overlay_uncommitted()
        sb._take_snapshot()
        return sb

    def _overlay_uncommitted(self) -> None:
        """Mirror the project's uncommitted state into the worktree, so the
        agent sees the real current tree, not just HEAD."""
        def blocked(rel: str) -> bool:
            return any(part in self.ignores for part in Path(rel).parts)

        for status, rel, orig in _dirty_entries(self.project_root):
            if orig and not blocked(orig):  # rename: drop the old path
                (self.path / orig).unlink(missing_ok=True)
            if blocked(rel):
                continue
            src = self.project_root / rel
            dst = self.path / rel
            if "D" in status or not src.exists():
                dst.unlink(missing_ok=True)
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    def _take_snapshot(self) -> None:
        self.snapshot = {
            str(p.relative_to(self.path)): _hash_file(p)
            for p in _walk(self.path, self.ignores)
        }

    # -- change tracking / merge-back ---------------------------------------

    def changes(self) -> list[Change]:
        current = {
            str(p.relative_to(self.path)): _hash_file(p)
            for p in _walk(self.path, self.ignores)
        }
        out: list[Change] = []
        for rel, digest in current.items():
            old = self.snapshot.get(rel)
            if old is None:
                out.append(Change(rel, "added"))
            elif old != digest:
                out.append(Change(rel, "modified"))
        for rel in self.snapshot:
            if rel not in current:
                out.append(Change(rel, "deleted"))
        return sorted(out, key=lambda c: c.relpath)

    def merge_into_project(self) -> tuple[list[Change], list[str]]:
        """Apply this sandbox's changes to the real project.

        Skips (and reports) any file the user modified in the project since
        the snapshot was taken, so we never clobber concurrent human edits.
        """
        applied: list[Change] = []
        skipped: list[str] = []
        for change in self.changes():
            src = self.path / change.relpath
            dst = self.project_root / change.relpath
            if dst.exists():
                snapshot_digest = self.snapshot.get(change.relpath)
                if snapshot_digest is None:
                    # File appeared in the project concurrently — don't clobber.
                    if _hash_file(dst) != _hash_file(src):
                        skipped.append(change.relpath)
                        continue
                elif _hash_file(dst) != snapshot_digest:
                    skipped.append(change.relpath)
                    continue
            if change.kind == "deleted":
                dst.unlink(missing_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            applied.append(change)
        return applied, skipped

    # -- teardown ------------------------------------------------------------

    def destroy(self) -> None:
        if self.kind == "worktree":
            self._remove_worktree(self.project_root, self.path)
        else:
            shutil.rmtree(self.path, ignore_errors=True)

    @staticmethod
    def _remove_worktree(project_root: Path, path: Path) -> None:
        code, _ = _git(project_root, "worktree", "remove", "--force", str(path))
        if code != 0:  # e.g. locked or already gone — clean up manually
            shutil.rmtree(path, ignore_errors=True)
            _git(project_root, "worktree", "prune")
