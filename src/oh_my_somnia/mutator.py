"""MUTATE phase: turn a diagnosis into a genome patch (a new/updated gene).

The patch is applied provisionally to the next generation only; the SELECT
step keeps it permanently only if that generation's fitness improved.
"""

from __future__ import annotations

from dataclasses import dataclass

from .agents import run_agent
from .config import Config
from .diagnoser import Diagnosis
from .genome import Gene, Genome

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["create", "update"]},
        "gene_id": {"type": "string"},
        "title": {"type": "string"},
        "content": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["action", "gene_id", "title", "content"],
    "additionalProperties": False,
}


@dataclass
class GenePatch:
    action: str
    gene: Gene
    rationale: str
    cost_usd: float = 0.0


async def mutate(task: str, diagnosis: Diagnosis, genome: Genome,
                 *, cfg: Config) -> GenePatch | None:
    existing = "\n".join(
        f"- {g.id}: {g.title}" for g in genome.summary()
    ) or "(genome is empty)"
    prompt = (
        "You are the MUTATOR phase of oh-my-somnia, a self-improving agent "
        "harness. A generation failed and was diagnosed. Propose exactly ONE "
        "patch to the harness's genome (its library of reusable heuristics) "
        "that would prevent this class of failure in the future.\n\n"
        f"Failed task:\n{task}\n\n"
        f"Diagnosis:\n"
        f"- root cause: {diagnosis.root_cause}\n"
        f"- category: {diagnosis.category}\n"
        f"- evidence: {diagnosis.evidence}\n"
        f"- lesson: {diagnosis.lesson}\n\n"
        f"Existing genes (do not duplicate; update one if it nearly covers this):\n"
        f"{existing}\n\n"
        "Rules for `content` (the heuristic text injected into future agent "
        "prompts):\n"
        "- imperative, generalizable across projects and tasks — never mention "
        "this specific task, file, or project\n"
        "- at most 8 lines\n"
        "- must be actionable during planning or execution\n"
        "`gene_id` is kebab-case. Use action=update with an existing gene_id "
        "to refine it, otherwise action=create with a new id."
    )
    run = await run_agent(
        prompt,
        schema=PATCH_SCHEMA,
        permission_mode="dontAsk",
        max_turns=8,
        model=cfg.model,
        max_budget_usd=cfg.max_budget_usd,
    )
    data = run.structured
    if not data or not data.get("content"):
        return None
    gene = genome.make_gene(
        gene_id=data["gene_id"],
        title=data.get("title", data["gene_id"]),
        content=data["content"],
        origin="mutation",
        status="active",  # written only if it wins selection
    )
    return GenePatch(
        action=data.get("action", "create"),
        gene=gene,
        rationale=data.get("rationale", ""),
        cost_usd=run.cost_usd,
    )
