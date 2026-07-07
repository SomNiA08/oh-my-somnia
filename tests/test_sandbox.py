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

    def test_not_eligible_non_git(self, tmp_path, monkeypatch):
        # The test host's home dir may itself be a git repo (temp lives under
        # it); cap git's upward search so `plain` is genuinely repo-less.
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
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
