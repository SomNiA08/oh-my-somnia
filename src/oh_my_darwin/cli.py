"""oh-my-darwin CLI.

    darwin init                     # write .darwin/config.toml template
    darwin run "task"               # run the evolutionary loop on a task
    darwin status                   # recent runs + genome summary
    darwin genome list|show|promote|rm
    darwin evolve                   # offline evolution from run history
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, history
from .config import CONFIG_TEMPLATE, Config, load_config, sandbox_root
from .diagnoser import diagnose
from .evaluator import Fitness, evaluate
from .evolve import evolve as evolve_genome
from .executor import execute
from .genome import Genome, ensure_seed_genome
from .mutator import GenePatch, mutate
from .planner import Plan, make_plan
from .sandbox import Sandbox
from .selector import better


def _say(msg: str = "") -> None:
    print(msg, flush=True)


@dataclass
class Attempt:
    generation: int
    plan: Plan
    fitness: Fitness
    sandbox: Sandbox | None
    patch: GenePatch | None = None
    cost_usd: float = 0.0
    transcript_tail: str = ""


@dataclass
class RunReport:
    attempts: list[Attempt] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    diagnoses: list[str] = field(default_factory=list)

    @property
    def best(self) -> Attempt | None:
        best = None
        for a in self.attempts:
            if best is None or better(a.fitness, best.fitness):
                best = a
        return best

    @property
    def total_cost(self) -> float:
        return sum(a.cost_usd for a in self.attempts)


# --------------------------------------------------------------------------
# darwin run
# --------------------------------------------------------------------------

async def _run_generation(task: str, gen: int, *, project: Path, cfg: Config,
                          genome: Genome, patch: GenePatch | None,
                          run_id: str) -> Attempt:
    genome_section = genome.render(extra=patch.gene if patch else None)

    if cfg.in_place:
        workdir, sandbox = project, None
    else:
        _say(f"  [gen {gen}] creating sandbox...")
        sandbox = Sandbox.create(project, sandbox_root() / run_id, f"gen-{gen}",
                                 cfg.ignores, mode=cfg.sandbox)
        _say(f"  [gen {gen}] sandbox ready ({sandbox.kind}): {sandbox.path}")
        workdir = sandbox.path

    _say(f"  [gen {gen}] planning...")
    plan, plan_run = await make_plan(
        task, cwd=workdir, genome_section=genome_section, cfg=cfg,
        fitness_command=cfg.fitness_command,
    )
    _say(f"  [gen {gen}] plan: {plan.summary}")

    _say(f"  [gen {gen}] executing ({len(plan.steps)} steps)...")
    exec_run = await execute(
        task, plan, cwd=workdir, genome_section=genome_section, cfg=cfg,
        fitness_command=cfg.fitness_command,
    )
    if exec_run.is_error:
        _say(f"  [gen {gen}] executor ended with {exec_run.subtype}"
             + (f": {'; '.join(exec_run.errors)[:200]}" if exec_run.errors else ""))

    _say(f"  [gen {gen}] evaluating...")
    fitness = await evaluate(task, plan, cwd=workdir, cfg=cfg)
    _say(f"  [gen {gen}] fitness: {fitness.render()}")

    return Attempt(
        generation=gen, plan=plan, fitness=fitness, sandbox=sandbox,
        patch=patch,
        cost_usd=plan_run.cost_usd + exec_run.cost_usd + fitness.cost_usd,
        transcript_tail=exec_run.transcript_tail,
    )


async def cmd_run(args: argparse.Namespace) -> int:
    project = Path.cwd()
    cfg = load_config(project, overrides={
        "fitness_command": args.fitness,
        "generations": args.generations,
        "model": args.model,
        "sandbox": args.sandbox,
        "in_place": True if args.in_place else None,
        "keep_sandboxes": True if args.keep_sandboxes else None,
    })
    ensure_seed_genome()
    genome = Genome.load(project, scope=cfg.scope)
    task: str = args.task
    run_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    _say(f"oh-my-darwin v{__version__}")
    _say(f"task      : {task}")
    _say(f"project   : {project}")
    _say(f"fitness   : {cfg.fitness_command or '(AI judge only)'}")
    _say(f"genome    : {len(genome.genes)} genes | generations: {cfg.generations} "
         f"| mode: {'in-place' if cfg.in_place else f'sandboxed ({cfg.sandbox})'}")
    _say("")

    report = RunReport()
    pending_patch: GenePatch | None = None

    for gen in range(cfg.generations):
        _say(f"── generation {gen} " + ("─" * 40))
        attempt = await _run_generation(
            task, gen, project=project, cfg=cfg, genome=genome,
            patch=pending_patch, run_id=run_id,
        )

        # SELECT: did this generation's mutation earn its place in the genome?
        if pending_patch is not None and report.attempts:
            if better(attempt.fitness, report.attempts[-1].fitness):
                genome.write(pending_patch.gene)
                report.promoted.append(pending_patch.gene.id)
                _say(f"  [select] gene '{pending_patch.gene.id}' improved fitness "
                     f"-> kept in genome")
            else:
                report.discarded.append(pending_patch.gene.id)
                _say(f"  [select] gene '{pending_patch.gene.id}' did not improve "
                     f"fitness -> discarded")
        pending_patch = None
        report.attempts.append(attempt)

        if attempt.fitness.passed:
            _say(f"  [gen {gen}] PASSED")
            break

        if gen == cfg.generations - 1:
            _say(f"  [gen {gen}] failed — no generations left")
            break

        _say(f"  [gen {gen}] failed — diagnosing...")
        diagnosis = await diagnose(
            task, attempt.plan, attempt.fitness, attempt.transcript_tail,
            cwd=attempt.sandbox.path if attempt.sandbox else project, cfg=cfg,
        )
        report.diagnoses.append(f"[{diagnosis.category}] {diagnosis.root_cause}")
        _say(f"  [diagnose] ({diagnosis.category}) {diagnosis.root_cause[:160]}")

        pending_patch = await mutate(task, diagnosis, genome, cfg=cfg)
        if pending_patch:
            _say(f"  [mutate] trial gene '{pending_patch.gene.id}': "
                 f"{pending_patch.gene.title}")
        else:
            _say("  [mutate] no viable mutation proposed — retrying with "
                 "current genome")

    # Merge the winner back into the real project.
    best = report.best
    merged: list[str] = []
    skipped: list[str] = []
    if best and best.sandbox and (best.fitness.passed or args.merge_best):
        applied, skipped = best.sandbox.merge_into_project()
        merged = [f"{c.kind[0]} {c.relpath}" for c in applied]
        _say("")
        _say(f"merged generation {best.generation} into project "
             f"({len(applied)} files):")
        for line in merged[:30]:
            _say(f"  {line}")
        if len(merged) > 30:
            _say(f"  ... and {len(merged) - 30} more")
        for rel in skipped:
            _say(f"  ! skipped {rel} (changed in project since snapshot)")
    elif best and best.sandbox and not best.fitness.passed:
        _say("")
        _say("no generation passed — nothing merged. Best attempt kept at:")
        _say(f"  {best.sandbox.path}")
        _say("  (use --merge-best to merge the best attempt anyway)")

    # Candidate genes prove themselves through passing runs.
    auto_promoted = genome.record_trial(passed=bool(best and best.fitness.passed))
    for gid in auto_promoted:
        _say(f"[genome] candidate '{gid}' proved itself -> promoted to active")

    # Persist history.
    history.record({
        "type": "run",
        "project": str(project),
        "task": task,
        "passed": bool(best and best.fitness.passed),
        "best_generation": best.generation if best else None,
        "best_score": round(best.fitness.score, 3) if best else 0,
        "generations": len(report.attempts),
        "diagnoses": report.diagnoses,
        "patches_promoted": report.promoted,
        "patches_discarded": report.discarded,
        "cost_usd": round(report.total_cost, 4),
    })

    # Cleanup sandboxes (keep the best failing one for inspection).
    if not cfg.keep_sandboxes:
        for a in report.attempts:
            if a.sandbox is None:
                continue
            keep_for_inspection = (
                best is a and not best.fitness.passed and not args.merge_best
            )
            if not keep_for_inspection:
                a.sandbox.destroy()
        try:  # drop the run folder once it's empty
            (sandbox_root() / run_id).rmdir()
        except OSError:
            pass

    _say("")
    passed = bool(best and best.fitness.passed)
    _say(f"result    : {'PASS' if passed else 'FAIL'} "
         f"(best score {best.fitness.score:.2f})" if best else "result    : no attempts")
    if report.promoted:
        _say(f"evolved   : +{', +'.join(report.promoted)}")
    if report.discarded:
        _say(f"discarded : {', '.join(report.discarded)}")
    _say(f"cost      : ${report.total_cost:.4f}")
    return 0 if passed else 1


# --------------------------------------------------------------------------
# other commands
# --------------------------------------------------------------------------

def cmd_init(_args: argparse.Namespace) -> int:
    ensure_seed_genome()
    darwin_dir = Path.cwd() / ".darwin"
    darwin_dir.mkdir(exist_ok=True)
    config_path = darwin_dir / "config.toml"
    if config_path.exists():
        _say(f"already exists: {config_path}")
    else:
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        _say(f"created: {config_path}")
        _say("Set `fitness_command` to your test/build command for objective "
             "fitness measurement.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    ensure_seed_genome()
    genome = Genome.load(Path.cwd())
    entries = history.read(limit=10)
    all_entries = history.read()
    runs = [e for e in all_entries if e.get("type") == "run"]
    wins = sum(1 for e in runs if e.get("passed"))

    _say(f"oh-my-darwin v{__version__}")
    _say(f"runs      : {len(runs)} total, {wins} passed "
         f"({(wins / len(runs) * 100):.0f}% win rate)" if runs else "runs      : none yet")
    _say(f"genome    : {len(genome.genes)} genes")
    for g in genome.summary():
        stats = f" uses={g.uses} wins={g.wins}" if g.status == "candidate" else ""
        _say(f"  [{g.status:9}] {g.id} — {g.title} ({g.origin}){stats}")
    if entries:
        _say("")
        _say("recent runs:")
        for e in entries:
            if e.get("type") != "run":
                continue
            mark = "PASS" if e.get("passed") else "FAIL"
            evolved = ""
            if e.get("patches_promoted"):
                evolved = f" | evolved: {', '.join(e['patches_promoted'])}"
            _say(f"  {e.get('timestamp', '?')} [{mark}] "
                 f"{str(e.get('task', ''))[:60]} "
                 f"(gens={e.get('generations')}, ${e.get('cost_usd', 0)}){evolved}")
    return 0


def cmd_genome(args: argparse.Namespace) -> int:
    ensure_seed_genome()
    genome = Genome.load(Path.cwd())
    action = args.action
    if action == "list":
        for g in genome.summary():
            _say(f"[{g.status:9}] {g.id} — {g.title} "
                 f"(origin={g.origin}, born={g.born}, uses={g.uses}, wins={g.wins})")
        if not genome.genes:
            _say("(genome is empty)")
    elif action == "show":
        gene = genome.genes.get(args.gene_id or "")
        if not gene:
            _say(f"no such gene: {args.gene_id}")
            return 1
        _say(gene.render_file())
    elif action == "promote":
        if genome.promote(args.gene_id or ""):
            _say(f"promoted: {args.gene_id}")
        else:
            _say(f"no such gene: {args.gene_id}")
            return 1
    elif action == "rm":
        if genome.remove(args.gene_id or ""):
            _say(f"removed: {args.gene_id}")
        else:
            _say(f"no such gene: {args.gene_id}")
            return 1
    return 0


async def cmd_evolve(_args: argparse.Namespace) -> int:
    ensure_seed_genome()
    project = Path.cwd()
    cfg = load_config(project)
    genome = Genome.load(project, scope=cfg.scope)
    _say("analyzing run history for evolvable patterns...")
    created = await evolve_genome(genome, cfg)
    if not created:
        _say("no new candidate genes proposed (history empty or already covered).")
        return 0
    for gid in created:
        _say(f"new candidate gene: {gid}")
    _say("Candidates join future runs marked 'provisional' and auto-promote "
         "after proving themselves in passing runs.")
    history.record({"type": "evolve", "project": str(project),
                    "candidates": created})
    return 0


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="darwin",
        description="oh-my-darwin: a self-improving agent harness. "
                    "Plan -> execute -> diagnose -> mutate -> trial -> "
                    "keep only what works better.",
    )
    p.add_argument("--version", action="version", version=f"oh-my-darwin {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the evolutionary loop on a task")
    run.add_argument("task", help="the task to accomplish")
    run.add_argument("--fitness", help="shell command whose exit code decides pass/fail")
    run.add_argument("--generations", type=int, help="max evolution generations")
    run.add_argument("--model", help="model for all agent phases")
    run.add_argument("--sandbox", choices=["auto", "copy", "worktree"],
                     help="sandbox backend (default: auto — worktree for git "
                          "repos, copy otherwise)")
    run.add_argument("--in-place", action="store_true",
                     help="run directly in the project (no sandbox)")
    run.add_argument("--keep-sandboxes", action="store_true",
                     help="keep all sandbox copies after the run")
    run.add_argument("--merge-best", action="store_true",
                     help="merge the best attempt even if it failed")

    sub.add_parser("init", help="write .darwin/config.toml template")
    sub.add_parser("status", help="recent runs and genome summary")
    sub.add_parser("evolve", help="propose candidate genes from run history")

    genome = sub.add_parser("genome", help="inspect and manage the genome")
    genome.add_argument("action", choices=["list", "show", "promote", "rm"])
    genome.add_argument("gene_id", nargs="?", help="gene id (for show/promote/rm)")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return asyncio.run(cmd_run(args))
        if args.command == "init":
            return cmd_init(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "evolve":
            return asyncio.run(cmd_evolve(args))
        if args.command == "genome":
            return cmd_genome(args)
    except KeyboardInterrupt:
        _say("\ninterrupted.")
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
