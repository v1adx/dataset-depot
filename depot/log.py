"""Rendering a list of decisions as text.

A run's log and a dry run's plan are the same thing rendered twice: the runner
and ``plan`` return the same objects in the same order, so the two read
identically and a plan can be trusted before a run that writes outward.

Indentation is by layer within the subgraph, so the shape of the dependencies
is visible without drawing a tree — everything at one indent could have run in
parallel.
"""
from __future__ import annotations

from .decide import Decision


def _elapsed(seconds: float) -> str:
    if seconds >= 1:
        return f"{seconds:.2f}s"
    return f"{seconds * 1000:.0f}ms"


def _depth(decisions: list[Decision]) -> dict[str, int]:
    """How deep each dataset sits within this subgraph."""
    inside = {d.dataset.key for d in decisions}
    depth: dict[str, int] = {}
    for decision in decisions:  # topological, so refs are already measured
        dts = decision.dataset
        depth[dts.key] = max(
            (depth[r.key] + 1 for r in dts.refs if r.key in depth and r.key in inside),
            default=0,
        )
    return depth


def render(decisions: list[Decision], target: str | None = None) -> str:
    """One line per dataset, in the order they were decided.

    A plan and a finished run are told apart by the clock: nothing has an
    elapsed time until it has run.
    """
    if not decisions:
        return "nothing to do"

    ran = [d for d in decisions if d.works]
    executed = any(d.elapsed for d in decisions)
    depth = _depth(decisions)

    width = max(len(d.dataset.key) + 2 * depth[d.dataset.key] for d in decisions)
    lines = []
    for d in decisions:
        indent = "  " * depth[d.dataset.key]
        if executed:
            status = _elapsed(d.elapsed) if d.works else "·"
        elif not d.works:
            status = "skip"
        else:
            # Only a ref woke it, and a ref that runs may still produce the
            # same content and never move. Nothing short of running can tell.
            status = "run" if d.certain else "maybe"
        lines.append(f"  {indent}{d.dataset.key:<{width - 2 * depth[d.dataset.key]}}  {status:>7}  {d.reason}")

    if executed:
        header = (f"{target or decisions[-1].dataset.key} · {len(ran)} of {len(decisions)} "
                  f"ran in {_elapsed(sum(d.elapsed for d in decisions))}")
    else:
        certain = [d for d in ran if d.certain]
        maybe = len(ran) - len(certain)
        header = (f"{target or decisions[-1].dataset.key} · {len(certain)} of {len(decisions)} will run"
                  + (f", {maybe} may follow" if maybe else ""))

    extras = [name for d in decisions for name in d.extras_ran]
    footer = [f"  extras: {', '.join(extras)}"] if extras else []

    return "\n".join([header, *lines, *footer])
