# Worktree Monorepo-Subdir Sandbox Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the fast `git worktree` sandbox backend work when `somnia` is run from inside a monorepo subdirectory, scoped correctly to that subdirectory.

**Architecture:** Split `Sandbox`'s conflated "git checkout dir" from its "agent work dir" by adding a `subpath`. The worktree still checks out the whole repo (`checkout_root`), but the agent works in, and merges back only, `checkout_root / subpath`. When `subpath == ""` (every copy sandbox, every repo-root worktree) all formulas collapse to today's behavior — the change is purely additive.

**Tech Stack:** Python 3.11+ (stdlib only: `pathlib`, `subprocess`, `os`, `shutil`, `hashlib`, `dataclasses`), the `git` CLI, `pytest`.

## Global Constraints

- Python 3.11+, standard library only — **no new dependencies**.
- Every module starts with `from __future__ import annotations` (existing style).
- Must run on Windows: use `pathlib`, POSIX-normalize subpaths with `.as_posix()`, keep the existing `-c core.quotepath=false` git invocation and UTF-8 handling in `_git`.
- **Backward compatibility is mandatory:** for `subpath == ""` the behavior of `copy` sandboxes and repo-root `worktree` sandboxes must be byte-for-byte unchanged.
- This repo's fitness command is `python -m pytest -q`; the full suite must stay green after every task.
- Git-backed tests must skip cleanly when `git` is not on PATH.

---

## File Structure

- **Modify** `src/oh_my_somnia/sandbox.py` — all core logic (eligibility, Sandbox fields, worktree creation, overlay, teardown, out-of-subdir detection).
- **Modify** `src/oh_my_somnia/cli.py` — print the out-of-subdir warning at merge time.
- **Modify** `README.md`, `src/oh_my_somnia/config.py` (`CONFIG_TEMPLATE`) — doc wording.
- **Create** `tests/test_sandbox.py` — git-backed sandbox tests.

---

## Task 1: `worktree_eligible` returns a subpath

**Files:**
- Modify: `src/oh_my_somnia/sandbox.py:61-80` (`worktree_eligible`), and its two callers at `:130` and `:162`.
- Create: `tests/test_sandbox.py`

**Interfaces:**
- Produces: `worktree_eligible(project_root: Path) -> tuple[bool, str, str]` — `(eligible, reason, subpath)`. `subpath` is the POSIX-separated relative path from the repo toplevel to `project_root`, `""` when they are the same directory.
- Consumes: existing `_git(repo, *args) -> tuple[int, str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sandbox.py`:

```python
"""Git-backed tests for the sandbox worktree/subdir logic."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from oh_my_somnia.sandbox import Sandbox, worktree_eligible

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git CLI required")


def _git(root: Path, *args: str) -> None:
    subprocess.run([GIT, "-C", str(root), *args],
                   check=True, capture_output=True, text=True)


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    return root


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit(root: Path, msg: str = "c") -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)


class TestWorktreeEligible:

    def test_eligible_at_repo_root(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.txt", "hi")
        _commit(repo)
        assert worktree_eligible(repo) == (True, "", "")

    def test_eligible_in_subdir(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "pkg/app/a.txt", "hi")
        _commit(repo)
        assert worktree_eligible(repo / "pkg" / "app") == (True, "", "pkg/app")

    def test_not_eligible_non_git(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        eligible, reason, subpath = worktree_eligible(plain)
        assert eligible is False
        assert subpath == ""
        assert "not a git" in reason

    def test_not_eligible_no_commits(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        eligible, reason, subpath = worktree_eligible(repo)
        assert eligible is False
        assert "no commits" in reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sandbox.py::TestWorktreeEligible -q`
Expected: FAIL — `worktree_eligible` returns a 2-tuple, so the `== (True, "", "")` assertions fail (and the 3-value unpacks raise `ValueError: not enough values to unpack`).

- [ ] **Step 3: Change `worktree_eligible` to return a subpath**

Replace the whole function at `src/oh_my_somnia/sandbox.py:61-80` with:

```python
def worktree_eligible(project_root: Path) -> tuple[bool, str, str]:
    """Decide whether a worktree sandbox can be built for ``project_root``.

    Returns ``(eligible, reason, subpath)``. ``subpath`` is the POSIX relative
    path from the repository toplevel to ``project_root`` — ``""`` when the
    project IS the toplevel, or e.g. ``"packages/frontend"`` for a monorepo
    subdirectory. The worktree always checks out the whole repo; ``subpath``
    tells the sandbox which directory inside it the agent actually works in."""
    code, out = _git(project_root, "rev-parse", "--show-toplevel")
    if code != 0:
        return False, "not a git repository", ""
    toplevel = out.strip()
    try:
        # samefile handles Windows 8.3 short paths, case, and slash direction
        same = os.path.samefile(toplevel, project_root)
    except OSError:
        same = Path(toplevel).resolve() == Path(project_root).resolve()
    if same:
        subpath = ""
    else:
        try:
            rel = Path(project_root).resolve().relative_to(Path(toplevel).resolve())
        except ValueError:
            return False, f"project is outside the repo at {toplevel}", ""
        subpath = rel.as_posix()
    code, _ = _git(project_root, "rev-parse", "--verify", "HEAD")
    if code != 0:
        return False, "repository has no commits yet", ""
    return True, "", subpath
```

- [ ] **Step 4: Update the two callers to unpack the 3-tuple**

In `create` at `src/oh_my_somnia/sandbox.py:130`, change:

```python
            eligible, reason = worktree_eligible(project_root)
```
to:
```python
            eligible, reason, _subpath = worktree_eligible(project_root)
```

In `_create_worktree` at `src/oh_my_somnia/sandbox.py:162`, change:

```python
        eligible, reason = worktree_eligible(project_root)
```
to:
```python
        eligible, reason, _subpath = worktree_eligible(project_root)
```

(The `_subpath` values are wired through properly in Task 2; unpacking-and-ignoring here just keeps the module importable and green.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox.py::TestWorktreeEligible -q`
Expected: PASS (4 passed). Also run `python -m pytest -q` — the whole suite stays green.

- [ ] **Step 6: Commit**

```bash
git add src/oh_my_somnia/sandbox.py tests/test_sandbox.py
git commit -m "feat(sandbox): worktree_eligible returns repo subpath"
```

---

## Task 2: Sandbox tracks checkout-root vs work-root

**Files:**
- Modify: `src/oh_my_somnia/sandbox.py` — `Sandbox` dataclass (`:113-119`), `_create_copy` (`:142-157`), `_create_worktree` (`:159-178`), `destroy` (`:255-259`).
- Modify: `tests/test_sandbox.py` — add `TestWorktreeSubdir`.

**Interfaces:**
- Consumes: `worktree_eligible(...) -> (bool, str, str)` from Task 1; existing `_git`, `_remove_worktree`, `_overlay_uncommitted`, `_take_snapshot`.
- Produces: `Sandbox` with new attributes `checkout_root: Path` (the git worktree dir; equals `path` for copy / repo-root worktree), `repo_toplevel: Path | None`, `subpath: str`, `overlay_baseline: set[str]`. Invariant: `path == checkout_root / subpath`. `destroy()` and `merge_into_project()` unchanged in signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sandbox.py`:

```python
class TestWorktreeSubdir:

    def _repo_with_subdir(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "pkg/app/a.txt", "committed")
        _write(repo, "other/b.txt", "sibling")
        _commit(repo)
        return repo

    def test_subdir_worktree_path_is_subpath(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="worktree")
        try:
            assert sb.kind == "worktree"
            assert sb.subpath == "pkg/app"
            assert sb.path == sb.checkout_root / "pkg" / "app"
            assert (sb.path / "a.txt").read_text(encoding="utf-8") == "committed"
        finally:
            sb.destroy()

    def test_subdir_worktree_merge_maps_to_subdir(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="worktree")
        try:
            (sb.path / "a.txt").write_text("changed by agent", encoding="utf-8")
            applied, skipped = sb.merge_into_project()
        finally:
            sb.destroy()
        assert skipped == []
        assert (repo / "pkg" / "app" / "a.txt").read_text(encoding="utf-8") \
            == "changed by agent"
        # A sibling package in the real repo is never touched.
        assert (repo / "other" / "b.txt").read_text(encoding="utf-8") == "sibling"

    def test_destroy_removes_worktree(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="worktree")
        checkout = sb.checkout_root
        sb.destroy()
        assert not checkout.exists()
        listing = subprocess.run(
            [GIT, "-C", str(repo), "worktree", "list"],
            capture_output=True, text=True).stdout
        assert str(checkout) not in listing

    def test_repo_root_worktree_regression(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.txt", "root")
        _commit(repo)
        sb = Sandbox.create(repo, tmp_path / "sb", "gen-0",
                            ignores=set(), mode="worktree")
        try:
            assert sb.subpath == ""
            assert sb.path == sb.checkout_root
            assert (sb.path / "a.txt").read_text(encoding="utf-8") == "root"
        finally:
            sb.destroy()

    def test_copy_from_subdir_regression(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="copy")
        try:
            assert sb.kind == "copy"
            assert sb.subpath == ""
            assert sb.path == sb.checkout_root
            assert (sb.path / "a.txt").read_text(encoding="utf-8") == "committed"
        finally:
            sb.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sandbox.py::TestWorktreeSubdir -q`
Expected: FAIL — `Sandbox` has no `checkout_root`/`subpath` attributes, and a subdir worktree currently makes `path` point at the checkout root (not `checkout_root/subpath`), so the assertions and `merge_into_project` mapping fail.

- [ ] **Step 3: Add the new dataclass fields + `__post_init__`**

Replace `src/oh_my_somnia/sandbox.py:113-119`:

```python
@dataclass
class Sandbox:
    project_root: Path
    path: Path
    ignores: set[str]
    kind: str = "copy"  # "copy" | "worktree"
    snapshot: dict[str, str] = field(default_factory=dict)
```
with:
```python
@dataclass
class Sandbox:
    project_root: Path      # merge-back target (the dir somnia was run in)
    path: Path              # agent work dir == checkout_root / subpath
    ignores: set[str]
    kind: str = "copy"      # "copy" | "worktree"
    snapshot: dict[str, str] = field(default_factory=dict)
    # For a monorepo-subdir worktree these three differ from the trivial case;
    # for copy sandboxes and repo-root worktrees they collapse (subpath == "").
    checkout_root: Path | None = None  # git worktree dir (whole repo)
    repo_toplevel: Path | None = None  # real repo root; None for copy
    subpath: str = ""                  # POSIX rel path, repo root -> project
    overlay_baseline: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.checkout_root is None:
            self.checkout_root = self.path
```

- [ ] **Step 4: Point `_create_worktree` at checkout-root + subpath**

Replace `src/oh_my_somnia/sandbox.py:159-178` (the whole `_create_worktree`) with:

```python
    @classmethod
    def _create_worktree(cls, project_root: Path, base: Path, name: str,
                         ignores: set[str]) -> "Sandbox":
        eligible, reason, subpath = worktree_eligible(project_root)
        if not eligible:
            raise RuntimeError(f"worktree sandbox unavailable: {reason}")
        _, top_out = _git(project_root, "rev-parse", "--show-toplevel")
        repo_toplevel = Path(top_out.strip())
        checkout_root = base / name
        if checkout_root.exists():
            cls._remove_worktree(project_root, checkout_root)
        checkout_root.parent.mkdir(parents=True, exist_ok=True)
        code, out = _git(project_root, "worktree", "add", "--detach",
                         str(checkout_root), "HEAD")
        if code != 0:
            raise RuntimeError(f"git worktree add failed: {out.strip()[:400]}")

        work = checkout_root / subpath if subpath else checkout_root
        sb = cls(project_root=project_root, path=work, ignores=ignores,
                 kind="worktree", checkout_root=checkout_root,
                 repo_toplevel=repo_toplevel, subpath=subpath)
        sb._overlay_uncommitted()
        sb._take_snapshot()
        return sb
```

(`_create_copy` needs no change: it passes `path=target`, and `__post_init__` sets `checkout_root = target`, `subpath` stays `""`.)

- [ ] **Step 5: Make `destroy` remove the checkout root**

Replace `src/oh_my_somnia/sandbox.py:255-259` (`destroy`):

```python
    def destroy(self) -> None:
        if self.kind == "worktree":
            self._remove_worktree(self.project_root, self.path)
        else:
            shutil.rmtree(self.path, ignore_errors=True)
```
with:
```python
    def destroy(self) -> None:
        if self.kind == "worktree":
            self._remove_worktree(self.project_root, self.checkout_root)
        else:
            shutil.rmtree(self.path, ignore_errors=True)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox.py -q`
Expected: PASS. `TestWorktreeSubdir` (5 tests) and `TestWorktreeEligible` (4 tests) all pass.

Note: these tests commit everything before creating the sandbox (clean tree), so `_overlay_uncommitted` is a no-op here — its subdir-correctness is covered in Task 3.

- [ ] **Step 7: Commit**

```bash
git add src/oh_my_somnia/sandbox.py tests/test_sandbox.py
git commit -m "feat(sandbox): track checkout-root vs subdir work-root"
```

---

## Task 3: Overlay the whole repo's uncommitted state

**Files:**
- Modify: `src/oh_my_somnia/sandbox.py:180-197` (`_overlay_uncommitted`).
- Modify: `tests/test_sandbox.py` — add `TestOverlay`.

**Interfaces:**
- Consumes: `self.repo_toplevel`, `self.checkout_root` (from Task 2); existing `_dirty_entries`.
- Produces: `_overlay_uncommitted` mirrors `repo_toplevel` → `checkout_root` across the whole repo, so the agent sees a faithful tree (including sibling packages), while snapshot/merge stay subdir-scoped.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sandbox.py`:

```python
class TestOverlay:

    def _repo_with_subdir(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "pkg/app/a.txt", "committed")
        _write(repo, "other/b.txt", "sibling")
        _commit(repo)
        return repo

    def test_uncommitted_subdir_edit_visible_at_path(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        # Uncommitted edit inside the subdir we will run in.
        _write(repo, "pkg/app/a.txt", "dirty")
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="worktree")
        try:
            assert (sb.path / "a.txt").read_text(encoding="utf-8") == "dirty"
        finally:
            sb.destroy()

    def test_uncommitted_sibling_edit_visible_in_checkout_only(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        # Uncommitted edit in a sibling package, outside the subdir.
        _write(repo, "other/b.txt", "dirty sibling")
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="worktree")
        try:
            # Faithfully mirrored under the checkout root...
            assert (sb.checkout_root / "other" / "b.txt").read_text(
                encoding="utf-8") == "dirty sibling"
            # ...but not under the agent's subdir work-root.
            assert not (sb.path / "other").exists()
        finally:
            sb.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sandbox.py::TestOverlay -q`
Expected: FAIL — the old overlay uses `self.project_root` (the subdir) as the copy source with repo-relative paths from `git status`, so `pkg/app/a.txt` is looked for under `.../pkg/app/pkg/app/a.txt` and the uncommitted edit never lands at `sb.path/a.txt`.

- [ ] **Step 3: Rewrite `_overlay_uncommitted` to use repo-root ↔ checkout-root**

Replace `src/oh_my_somnia/sandbox.py:180-197` (`_overlay_uncommitted`) with:

```python
    def _overlay_uncommitted(self) -> None:
        """Mirror the whole repo's uncommitted state into the worktree
        checkout, so the agent sees the real current tree (HEAD + working
        changes), not just HEAD. Paths from `git status` are relative to the
        repo toplevel, so we overlay from `repo_toplevel` onto `checkout_root`;
        snapshot/merge remain scoped to the subdir via `self.path`."""
        source = self.repo_toplevel or self.project_root
        dest = self.checkout_root or self.path

        def blocked(rel: str) -> bool:
            return any(part in self.ignores for part in Path(rel).parts)

        for status, rel, orig in _dirty_entries(source):
            if orig and not blocked(orig):  # rename: drop the old path
                (dest / orig).unlink(missing_ok=True)
            if blocked(rel):
                continue
            src = source / rel
            dst = dest / rel
            if "D" in status or not src.exists():
                dst.unlink(missing_ok=True)
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox.py -q`
Expected: PASS — `TestOverlay` (2) plus all earlier tests. For a repo-root worktree, `source == project_root` and `dest == path`, so behavior is identical to before.

- [ ] **Step 5: Commit**

```bash
git add src/oh_my_somnia/sandbox.py tests/test_sandbox.py
git commit -m "feat(sandbox): overlay whole-repo uncommitted state into worktree"
```

---

## Task 4: Warn about dropped out-of-subdir edits

**Files:**
- Modify: `src/oh_my_somnia/sandbox.py` — capture `overlay_baseline` in `_create_worktree`; add `_porcelain_set` and `out_of_subdir_changes` methods.
- Modify: `src/oh_my_somnia/cli.py:239-240` (inside the merge block of `cmd_run`).
- Modify: `tests/test_sandbox.py` — add `TestOutOfSubdir`.

**Interfaces:**
- Consumes: `self.checkout_root`, `self.subpath`, `self.kind`, `self.overlay_baseline`; existing `_dirty_entries`.
- Produces: `Sandbox.out_of_subdir_changes() -> list[str]` — repo-relative paths (sorted) the agent changed **outside** `subpath` that will NOT be merged. Always `[]` for copy sandboxes and repo-root worktrees.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sandbox.py`:

```python
class TestOutOfSubdir:

    def _repo_with_subdir(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "pkg/app/a.txt", "committed")
        _write(repo, "other/b.txt", "sibling")
        _commit(repo)
        return repo

    def test_flags_edit_outside_subdir(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="worktree")
        try:
            # Agent reaches outside its subdir inside the worktree checkout.
            (sb.checkout_root / "other" / "b.txt").write_text(
                "touched by agent", encoding="utf-8")
            out = sb.out_of_subdir_changes()
        finally:
            sb.destroy()
        assert "other/b.txt" in out

    def test_empty_when_only_inside_subdir(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="worktree")
        try:
            (sb.path / "a.txt").write_text("only inside", encoding="utf-8")
            assert sb.out_of_subdir_changes() == []
        finally:
            sb.destroy()

    def test_empty_for_copy_sandbox(self, tmp_path):
        repo = self._repo_with_subdir(tmp_path)
        sb = Sandbox.create(repo / "pkg" / "app", tmp_path / "sb", "gen-0",
                            ignores=set(), mode="copy")
        try:
            (sb.path / "a.txt").write_text("x", encoding="utf-8")
            assert sb.out_of_subdir_changes() == []
        finally:
            sb.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sandbox.py::TestOutOfSubdir -q`
Expected: FAIL — `AttributeError: 'Sandbox' object has no attribute 'out_of_subdir_changes'`.

- [ ] **Step 3: Capture the overlay baseline in `_create_worktree`**

In `_create_worktree` (Task 2 version), insert the baseline capture between `_overlay_uncommitted()` and `_take_snapshot()`:

```python
        sb._overlay_uncommitted()
        sb.overlay_baseline = sb._porcelain_set()
        sb._take_snapshot()
        return sb
```

- [ ] **Step 4: Add `_porcelain_set` and `out_of_subdir_changes`**

Add these two methods to `Sandbox`, immediately after `_overlay_uncommitted` (before `_take_snapshot`):

```python
    def _porcelain_set(self) -> set[str]:
        """Repo-relative paths currently reported dirty in the checkout."""
        base = self.checkout_root or self.path
        return {rel for _status, rel, _orig in _dirty_entries(base)}

    def out_of_subdir_changes(self) -> list[str]:
        """Repo-relative files the agent changed OUTSIDE `subpath` that merge
        will not carry back. Best-effort (diffs against the post-overlay
        baseline); empty for copy sandboxes and repo-root worktrees."""
        if self.kind != "worktree" or not self.subpath:
            return []
        prefix = self.subpath + "/"
        fresh = self._porcelain_set() - self.overlay_baseline
        out = []
        for rel in sorted(fresh):
            if rel.startswith(prefix):
                continue  # inside the subdir — merged normally
            if any(part in self.ignores for part in Path(rel).parts):
                continue
            out.append(rel)
        return out
```

- [ ] **Step 5: Run the sandbox tests to verify they pass**

Run: `python -m pytest tests/test_sandbox.py::TestOutOfSubdir -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Print the warning at merge time in the CLI**

In `src/oh_my_somnia/cli.py`, find the merge block in `cmd_run` (the `for rel in skipped:` loop at `:239-240`):

```python
            for rel in skipped:
                _say(f"  ! skipped {rel} (changed in project since snapshot)")
```
Add, immediately after that loop (still inside the `try`):

```python
            for rel in best.sandbox.out_of_subdir_changes():
                _say(f"  ! ignored {rel} (outside {best.sandbox.subpath} — "
                     f"not merged back)")
```

- [ ] **Step 7: Run the full suite to verify nothing regressed**

Run: `python -m pytest -q`
Expected: PASS — full suite green (existing tests + all `tests/test_sandbox.py`).

- [ ] **Step 8: Commit**

```bash
git add src/oh_my_somnia/sandbox.py src/oh_my_somnia/cli.py tests/test_sandbox.py
git commit -m "feat(sandbox): warn about dropped out-of-subdir edits"
```

---

## Task 5: Documentation

**Files:**
- Modify: `src/oh_my_somnia/sandbox.py:1-12` (module docstring).
- Modify: `README.md` — the sandbox bullet in the "동작 원리" section.
- Modify: `src/oh_my_somnia/config.py` — the `sandbox` comment inside `CONFIG_TEMPLATE` (`:208-211`).

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the module docstring**

In `src/oh_my_somnia/sandbox.py`, replace the `"worktree"` bullet in the top docstring (`:6-7`):

```
- "worktree": `git worktree add` of HEAD plus an overlay of the project's
              uncommitted changes — much faster on large repositories.
```
with:
```
- "worktree": `git worktree add` of HEAD plus an overlay of the project's
              uncommitted changes — much faster on large repositories. Works
              from a monorepo subdirectory too: the whole repo is checked out
              but the agent works in, and only merges back, that subdirectory.
```

- [ ] **Step 2: Update the README sandbox bullet**

In `README.md`, find the `**샌드박스**` bullet (it currently says worktree is chosen when "git 저장소면"). Replace its parenthetical about worktree with wording that notes subdir support, e.g. append to that bullet:

```
  모노레포 하위 디렉터리에서 실행해도 worktree가 동작한다 — 저장소 전체를
  체크아웃하되 에이전트는 그 하위 디렉터리에서만 작업하고, 변경분도 그
  하위 디렉터리에만 머지된다.
```

- [ ] **Step 3: Update the config template comment**

In `src/oh_my_somnia/config.py`, replace the `CONFIG_TEMPLATE` sandbox comment (`:208-211`):

```python
# Sandbox backend: "auto" (git worktree when possible, else copy),
# "worktree", or "copy". Worktrees start from HEAD plus your uncommitted
# changes and are much faster on large repositories.
```
with:
```python
# Sandbox backend: "auto" (git worktree when possible, else copy),
# "worktree", or "copy". Worktrees start from HEAD plus your uncommitted
# changes and are much faster on large repositories. They also work when you
# run somnia from a monorepo subdirectory (scoped to that subdirectory).
```

- [ ] **Step 4: Verify the suite still passes**

Run: `python -m pytest -q`
Expected: PASS (docs-only changes; suite unchanged and green).

- [ ] **Step 5: Commit**

```bash
git add src/oh_my_somnia/sandbox.py README.md src/oh_my_somnia/config.py
git commit -m "docs: note worktree monorepo-subdir support"
```

---

## Self-Review

**Spec coverage:**
- Unit 1 (`worktree_eligible` subpath) → Task 1. ✓
- Unit 2 (Sandbox fields, snapshot/changes/merge unchanged) → Task 2. ✓
- Unit 3 (`_create_copy` unchanged via post_init) → Task 2 Step 4 note. ✓
- Unit 4 (`_create_worktree` checkout_root/subpath) → Task 2. ✓
- Unit 5 (`_overlay_uncommitted` whole-repo) → Task 3. ✓
- Unit 6 (destroy/`_remove_worktree` checkout_root) → Task 2 Step 5. ✓
- Unit 7 (out-of-subdir warning + baseline + cli) → Task 4. ✓
- Unit 8 (docs) → Task 5. ✓
- Spec test items 1-10 → mapped across Tasks 1-4 (`TestWorktreeEligible`, `TestWorktreeSubdir`, `TestOverlay`, `TestOutOfSubdir`, plus copy/repo-root regressions). ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code and exact commands with expected output. ✓

**Type consistency:** `worktree_eligible -> (bool, str, str)` used consistently (Tasks 1, 2). `checkout_root`, `repo_toplevel`, `subpath`, `overlay_baseline` defined in Task 2 and referenced with identical names in Tasks 3-4. `out_of_subdir_changes()` / `_porcelain_set()` defined and called by identical names (Task 4, cli). Invariant `path == checkout_root / subpath` upheld in `_create_worktree` and asserted in tests. ✓
