# Design: worktree sandbox support for monorepo subdirectories

- **Date:** 2026-07-07
- **Status:** proposed (awaiting user review)
- **Scope:** `src/oh_my_somnia/sandbox.py`, minor docs, new tests

## Problem

`somnia run` inside a subdirectory of a git repo (a monorepo package such as
`monorepo/packages/frontend`) cannot use the fast `worktree` sandbox backend.
`worktree_eligible()` (`sandbox.py:61`) deliberately rejects any project whose
root is not the repo *toplevel*:

> A mere subdirectory of some enclosing repo would check out the whole
> enclosing repo — wrong tree, wrong merge paths.

Consequence: in a monorepo, `--sandbox auto` silently falls back to a full
directory **copy** (slow on large trees), and `--sandbox worktree` errors out.

We want the worktree backend to work from a subdirectory, scoped correctly to
that subdirectory.

## Root cause

`Sandbox` conflates two roots that are identical only at the repo top level:

- the **checkout root** — the working tree `git worktree add` materializes
  (always the *whole* repo), used for `git worktree add/remove` and for
  overlaying uncommitted changes; and
- the **work root** (`sandbox.path`) — what the agent's `cwd`, the fitness
  command, and all snapshot / change-tracking / merge-back logic operate on.

At the repo root these are the same directory, so the code uses one field for
both. In a subdirectory they diverge by the **subpath** (`packages/frontend`).

## Approach

Teach `Sandbox` the two-root distinction and carry a `subpath`. When `subpath`
is empty — every `copy` sandbox, and every repo-root `worktree` — all formulas
collapse to today's behavior. The change is therefore **purely additive**: no
existing code path changes result.

### Invariants

| concept | field | copy / repo-root worktree | monorepo-subdir worktree |
|---|---|---|---|
| git checkout dir | `checkout_root` | `base/name` | `base/name` (whole repo) |
| agent work dir | `path` | `base/name` | `base/name/<subpath>` |
| real repo root | `repo_toplevel` | `project_root` | enclosing repo toplevel |
| merge-back target | `project_root` | project root | the subdir launched in |
| subpath | `subpath` | `""` | `"packages/frontend"` (POSIX) |

`path == checkout_root / subpath` and `project_root == repo_toplevel / subpath`
hold in all cases.

## Changes by unit

### 1. `worktree_eligible(project_root) -> (bool, str, str)`

Add a third return value, `subpath`.

- Run `git rev-parse --show-toplevel`. Not a repo → `(False, "not a git
  repository", "")`.
- Compute `subpath` = POSIX relative path from toplevel to `project_root`
  (resolve both first, to normalize Windows 8.3 / case / slash direction; keep
  the existing `os.path.samefile` fast-path for the equal case → `subpath=""`).
- If `project_root` is not inside the toplevel (should not happen once
  `rev-parse` succeeded) → `(False, "project is outside the repo at
  <toplevel>", "")`.
- Require `HEAD` to exist (`git rev-parse --verify HEAD`); else `(False,
  "repository has no commits yet", "")`.
- Otherwise `(True, "", subpath)`.

The previous "project is a subdirectory of the repo" rejection is **removed** —
that is exactly the case we now support.

All callers updated for the 3-tuple (`create`, `_create_worktree`).

### 2. `Sandbox` dataclass

Add fields (all default to the empty/`path` case so `copy` construction is
unaffected):

```python
checkout_root: Path      # git worktree dir; == path when subpath == ""
repo_toplevel: Path | None = None  # real repo root; None for copy
subpath: str = ""        # POSIX rel path from repo root to project
```

`snapshot`, `changes()`, `merge_into_project()` are **unchanged** — they key off
`self.path` and `self.project_root`, which now point at the subdir on both
sides, so subdir→subdir mapping falls out for free.

### 3. `_create_copy`

Set `checkout_root = target`, `subpath = ""`, `repo_toplevel = None`. No
behavior change.

### 4. `_create_worktree`

- `checkout_root = base / name`; `git worktree add --detach <checkout_root>
  HEAD` (unchanged — always the whole repo).
- `path = checkout_root / subpath`.
- Store `checkout_root`, `repo_toplevel` (the rev-parse toplevel), `subpath`.
- `_overlay_uncommitted()` then `_take_snapshot()` (snapshot walks `self.path`,
  i.e. the subdir only — unchanged).

### 5. `_overlay_uncommitted`

Mirror the **whole** repo's uncommitted state so the agent sees a faithful tree
(including sibling packages it may need to read), while snapshot/merge stay
subdir-scoped:

- source = `repo_toplevel`, dest = `checkout_root` (today's `project_root` /
  `path`; identical when `subpath == ""`).
- `_dirty_entries(repo_toplevel)` — `git status` paths are already
  repo-toplevel-relative, so no prefix arithmetic is needed.
- `blocked()` (ignores) check unchanged.

### 6. `destroy` / `_remove_worktree`

Operate on `checkout_root`, not `path` (git removes a worktree by its checkout
dir; a subdir path would fail). Identical to today when `subpath == ""`.

### 7. Out-of-subdir change warning (best-effort)

Because the worktree physically contains the whole repo, the agent *can* edit
files outside the subdir. Those edits are intentionally **not** merged (merge is
subdir-scoped) — but the loss must not be silent.

- Right after overlay, capture `baseline = set()` of porcelain entries from
  `git -C checkout_root status --porcelain -z` (fast; no content hashing).
- New method `out_of_subdir_changes() -> list[str]`: recompute the porcelain
  set, return entries **not** under `subpath`, **not** ignored, and **not** in
  `baseline`. Empty for copy sandboxes and repo-root worktrees.
- `cmd_run` prints, at merge time, a warning line per dropped file (capped like
  the merged-file list), e.g.
  `! ignored <path> (outside <subpath> — not merged back)`.

This is best-effort by design (it will not notice an agent reverting an overlaid
change); it exists to surface, not to guarantee.

### 8. Docs

- `sandbox.py` module docstring: note subdir worktrees.
- `worktree_eligible` docstring: replace the "must be toplevel" rationale with
  the subpath behavior.
- README sandbox bullet + `CONFIG_TEMPLATE` comment: mention worktree works from
  a monorepo subdirectory.

## Testing

This repo's fitness command is `python -m pytest -q`; the change must ship with
tests. Add git-backed tests (new `tests/test_sandbox.py`), each skipped if `git`
is unavailable, using a helper that `git init`s a repo, configures a throwaway
user, writes files, and commits.

1. `worktree_eligible` at repo root → `(True, "", "")`.
2. `worktree_eligible` in a subdir → `(True, "", "pkg/app")`. **(new behavior)**
3. `worktree_eligible` on a non-git dir → `(False, <reason>, "")`.
4. `worktree_eligible` on a repo with no commits → `(False, <reason>, "")`.
5. `Sandbox.create(mode="worktree")` from a subdir → `kind == "worktree"`,
   `path == checkout_root / subpath`, `path` exists and contains the subdir's
   committed files.
6. Overlay: an uncommitted edit in the real subdir is visible at `sandbox.path`;
   an uncommitted edit in a *sibling* package is visible under `checkout_root`
   but not under `path`.
7. Merge: edit a file at `sandbox.path` → `merge_into_project()` writes it back
   into the real subdir; a sibling-package file in the real repo is untouched.
8. `out_of_subdir_changes()` reports a file the agent created outside `subpath`;
   returns empty when only in-subdir files changed.
9. `destroy()` leaves `git worktree list` with no dangling entry and removes the
   checkout dir.
10. Regression: copy backend from a subdir still works; repo-root worktree still
    works (subpath `""`, all fields collapse).

## Non-goals

- Sparse-checkout (checking out only the subdir) — a full-repo worktree is
  simpler and correct; revisit only if disk/time on huge monorepos proves it.
- Whole-repo merge scope — explicitly rejected in favor of subdir scoping +
  warning (see decision below).
- New config keys or CLI flags — behavior extends the existing `worktree`/`auto`
  modes transparently.

## Open decision (made in the user's absence; override welcome)

Change scope for edits outside the launched subdir: **scope to the subdirectory,
with a best-effort warning** (unit 7). Chosen over strict-silent scoping (loses
edits invisibly) and whole-repo scoping (merge blast radius reaches unrelated
monorepo files, contradicting running in a subdir). Flip to whole-repo scope, or
drop the warning, on request.
