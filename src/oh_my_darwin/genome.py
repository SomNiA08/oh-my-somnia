"""The genome: evolved heuristics stored as one markdown file per gene.

A gene looks like:

    ---
    id: verify-before-done
    title: Verify before claiming completion
    status: active          # active | candidate
    origin: seed            # seed | mutation | evolve
    born: 2026-07-07
    uses: 4
    wins: 3
    ---
    Before declaring a task complete, run the project's tests/build...

Active genes are injected into every phase's system prompt. Candidate genes
(from `darwin evolve`) are injected too, marked provisional; they get promoted
after proving themselves in passing runs, or via `darwin genome promote`.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config as cfg_mod

PROMOTE_AFTER_WINS = 2


@dataclass
class Gene:
    id: str
    title: str
    content: str
    status: str = "active"
    origin: str = "seed"
    born: str = ""
    uses: int = 0
    wins: int = 0
    path: Path | None = None

    def render_file(self) -> str:
        return (
            "---\n"
            f"id: {self.id}\n"
            f"title: {self.title}\n"
            f"status: {self.status}\n"
            f"origin: {self.origin}\n"
            f"born: {self.born}\n"
            f"uses: {self.uses}\n"
            f"wins: {self.wins}\n"
            "---\n"
            f"{self.content.strip()}\n"
        )


def _parse_gene(path: Path) -> Gene | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.DOTALL)
    if not m:
        return None
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    gene = Gene(
        id=meta.get("id", path.stem),
        title=meta.get("title", path.stem),
        content=m.group(2).strip(),
        status=meta.get("status", "active"),
        origin=meta.get("origin", "unknown"),
        born=meta.get("born", ""),
        path=path,
    )
    for int_field in ("uses", "wins"):
        try:
            setattr(gene, int_field, int(meta.get(int_field, "0")))
        except ValueError:
            pass
    return gene


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "gene"


class Genome:
    def __init__(self, dirs: list[Path]):
        # First dir is the write target for new genes.
        self.dirs = dirs
        self.genes: dict[str, Gene] = {}
        self.reload()

    @classmethod
    def load(cls, project_root: Path, scope: str = "global") -> "Genome":
        global_dir = cfg_mod.genome_dir()
        project_dir = project_root / ".darwin" / "genome"
        dirs = [project_dir, global_dir] if scope == "project" else [global_dir, project_dir]
        return cls(dirs)

    def reload(self) -> None:
        self.genes = {}
        # Later dirs must not clobber earlier ones? Project-level should win,
        # so load global first and project last regardless of write order.
        for d in sorted(self.dirs, key=lambda p: 0 if p == cfg_mod.genome_dir() else 1):
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.md")):
                gene = _parse_gene(path)
                if gene:
                    self.genes[gene.id] = gene

    def render(self, extra: Gene | None = None) -> str:
        """Render the genome as a system-prompt section."""
        genes = [g for g in self.genes.values() if g.status in ("active", "candidate")]
        if extra:
            genes = [g for g in genes if g.id != extra.id] + [extra]
        if not genes:
            return ""
        lines = [
            "## Evolved heuristics (genome)",
            "These heuristics were learned from previous runs of this harness.",
            "Apply each one whenever it is relevant to the current work.",
            "",
        ]
        for g in genes:
            tag = " (provisional — being trialed)" if g.status == "candidate" else ""
            lines.append(f"### {g.title}{tag}")
            lines.append(g.content.strip())
            lines.append("")
        return "\n".join(lines)

    def summary(self) -> list[Gene]:
        return sorted(self.genes.values(), key=lambda g: (g.status, g.id))

    # -- mutation lifecycle ------------------------------------------------

    def write(self, gene: Gene) -> Path:
        target_dir = self.dirs[0]
        target_dir.mkdir(parents=True, exist_ok=True)
        if gene.path is None:
            gene.path = target_dir / f"{gene.id}.md"
        if not gene.born:
            gene.born = _dt.date.today().isoformat()
        gene.path.write_text(gene.render_file(), encoding="utf-8")
        self.genes[gene.id] = gene
        return gene.path

    def make_gene(self, gene_id: str, title: str, content: str,
                  origin: str, status: str) -> Gene:
        gene_id = slugify(gene_id)
        existing = self.genes.get(gene_id)
        gene = Gene(
            id=gene_id, title=title, content=content,
            origin=origin, status=status,
            born=_dt.date.today().isoformat(),
        )
        if existing is not None:
            gene.uses, gene.wins = existing.uses, existing.wins
            gene.path = existing.path
        return gene

    def promote(self, gene_id: str) -> bool:
        gene = self.genes.get(gene_id)
        if not gene:
            return False
        gene.status = "active"
        self.write(gene)
        return True

    def remove(self, gene_id: str) -> bool:
        gene = self.genes.get(gene_id)
        if not gene or not gene.path:
            return False
        gene.path.unlink(missing_ok=True)
        del self.genes[gene_id]
        return True

    def record_trial(self, passed: bool) -> list[str]:
        """Bump candidate stats after a run; auto-promote proven candidates."""
        promoted: list[str] = []
        for gene in self.genes.values():
            if gene.status != "candidate":
                continue
            gene.uses += 1
            if passed:
                gene.wins += 1
            if gene.wins >= PROMOTE_AFTER_WINS:
                gene.status = "active"
                promoted.append(gene.id)
            self.write(gene)
        return promoted


def ensure_seed_genome() -> None:
    """Copy packaged seed genes into the global genome dir on first use."""
    target = cfg_mod.genome_dir()
    if target.is_dir() and any(target.glob("*.md")):
        return
    target.mkdir(parents=True, exist_ok=True)
    seed_dir = Path(__file__).parent / "seed"
    if seed_dir.is_dir():
        for src in seed_dir.glob("*.md"):
            (target / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
