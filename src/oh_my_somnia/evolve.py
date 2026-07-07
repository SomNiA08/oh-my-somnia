"""`somnia evolve`: offline evolution from accumulated run history.

Reads recent history (failures with their diagnoses, discarded patches,
successes) and proposes candidate genes. Candidates are injected into future
runs marked "provisional"; they auto-promote after proving themselves in
passing runs, or can be promoted/removed manually via `somnia genome`.
"""

from __future__ import annotations

import json

from .agents import run_agent
from .config import Config
from .genome import Genome
from . import history

EVOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "genes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "gene_id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["gene_id", "title", "content"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["genes"],
    "additionalProperties": False,
}


async def evolve(genome: Genome, cfg: Config, limit: int = 30) -> list[str]:
    entries = history.read(limit=limit)
    if not entries:
        return []

    condensed = []
    for e in entries:
        condensed.append({
            "task": str(e.get("task", ""))[:200],
            "passed": e.get("passed"),
            "generations": e.get("generations"),
            "diagnoses": [str(d)[:300] for d in e.get("diagnoses", [])][:3],
            "patches_promoted": e.get("patches_promoted", []),
            "patches_discarded": e.get("patches_discarded", []),
        })
    existing = "\n".join(f"- {g.id}: {g.title}" for g in genome.summary()) or "(empty)"

    prompt = (
        "You are the EVOLVE phase of oh-my-somnia, a self-improving agent "
        "harness. Below is the harness's recent run history. Find recurring "
        "failure patterns or winning behaviors that the current genome does "
        "not yet capture, and propose 0-3 new candidate genes.\n\n"
        f"Existing genes (never duplicate these):\n{existing}\n\n"
        f"Run history (most recent last):\n"
        f"```json\n{json.dumps(condensed, ensure_ascii=False, indent=1)[:12000]}\n```\n\n"
        "Rules for each gene's `content`:\n"
        "- imperative, generalizable across projects — no task-specific details\n"
        "- at most 8 lines\n"
        "- actionable during planning or execution\n"
        "Propose an empty list if the history shows nothing new worth encoding."
    )
    run = await run_agent(
        prompt,
        schema=EVOLVE_SCHEMA,
        permission_mode="dontAsk",
        max_turns=8,
        model=cfg.model,
        max_budget_usd=cfg.max_budget_usd,
    )
    data = run.structured or {}
    created: list[str] = []
    for item in data.get("genes", [])[:3]:
        if not item.get("content"):
            continue
        gene = genome.make_gene(
            gene_id=item["gene_id"],
            title=item.get("title", item["gene_id"]),
            content=item["content"],
            origin="evolve",
            status="candidate",
        )
        genome.write(gene)
        created.append(gene.id)
    return created
