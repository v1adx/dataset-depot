"""One entry point over the core: every command is a thin renderer.

Nothing here decides anything. plan, run, reset and the registry are the API;
these commands format what they return, and each one takes --json so an agent
reads the same answer a person does.

Finding the datasets means importing them, which costs about a third of a
second for forty modules. A project that wants the answer without paying it
declares a DatasetIndex somewhere and reads that instead — it is the same
table, cached, with a probe that notices when a module is edited.
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from . import cache, config, graph, registry
from .dataset import Dataset
from .log import render
from .runner import plan as plan_run
from .runner import run as run_pipeline

TEMPLATE = '''"""
One line saying what this dataset is.

Data flow:
  1. Where the data comes from.
  2. What is done to it.

Anything a reader would otherwise have to reverse-engineer: why the source is
consulted this way, what the odd column means, what this deliberately omits.
"""

from pathlib import Path

import pandas as pd

from depot import Dataset

## Refs
# from datasets.<type>.<name> import dts as <alias>


# Go to the source. The only phase that fetches.
def extract(d: Dataset) -> None:
    d.dataframe = pd.DataFrame()


# Compute from the refs. Read them with ref.dataframe — the runner has already
# brought them up to date, and ref.load() would re-enter the runner.
def transform(d: Dataset) -> None:
    ...


# Raise if the result is wrong. Runs before anything is written.
def validate(d: Dataset) -> None:
    ...


# Send it outward. Runs only when the version actually moved.
def publish(d: Dataset) -> None:
    ...


# Build a file and return its path. Never called by the pipeline.
def report(d: Dataset) -> Path | None:
    ...
    return file


dts = Dataset(
    # name and type come from the file path; pass them only to override
    threshold=3600,     # do not consult the source more often than this
    # probe=...,        # instead of a timer, when the source can be asked
                        # cheaply what its version is. Must return a TIME.
    # cache=False,      # when the dataframe is a delta pushed elsewhere,
                        # not this dataset's product
    refs=[],
    extractors=[extract],
    transforms=[transform],
    validators=[validate],
    extras=[publish],
    artifacts=[report],
)


if __name__ == "__main__":
    dts.info()
'''


def _target(name: str) -> Dataset:
    try:
        return registry.get(name)
    except KeyError as e:
        sys.exit(e.args[0])  # not str(e): that is the repr, backslashes and all


def _freshness(dts: Dataset) -> str:
    if dts.probe:
        return "probe — the source is asked what its version is"
    if dts.threshold is not None:
        return f"timer, at most once every {dts.threshold}s"
    return "refs only — it moves when its inputs do"


def _warn_about(scan) -> None:
    """A module that would not import is worth saying out loud, on stderr."""
    for path, why in scan.failures.items():
        print(f"! {path} could not be imported — {why}", file=sys.stderr)


def _emit(payload, as_json: bool, text: str) -> None:
    print(json.dumps(payload, indent=2, default=str, ensure_ascii=False) if as_json else text)


# --- commands ----------------------------------------------------------------

def cmd_ls(args) -> None:
    scan = registry.discover()
    _warn_about(scan)
    found = scan.found
    pool = [f.dataset for f in found.values()]
    depth, counts = registry.layers(pool), registry.dependants(pool)

    rows = [
        {
            "key": key, "layer": depth[key], "dependants": counts[key],
            "refs": [r.key for r in f.dataset.refs],
            "class": type(f.dataset).__name__, "module": f.module,
            "cache": f.dataset.cache, "threshold": f.dataset.threshold,
            "probe": bool(f.dataset.probe),
            "doc": f.doc.splitlines()[0] if f.doc else "",
        }
        for key, f in sorted(found.items(), key=lambda kv: (depth[kv[0]], kv[0]))
    ]
    width = max((len(r["key"]) for r in rows), default=0)
    text = "\n".join(f"  {r['key']:<{width}}  {r['doc']}" for r in rows)
    _emit(rows, args.json, text or "no datasets found")


def cmd_show(args) -> None:
    dts = _target(args.name)
    found = registry.discover().found.get(dts.key)
    meta = cache.read_meta(dts)

    payload = {
        "key": dts.key,
        "module": found.module if found else None,
        "doc": found.doc if found else "",
        "class": type(dts).__name__,
        "refs": [r.key for r in dts.refs],
        "threshold": dts.threshold,
        "probe": bool(dts.probe),
        "cache": dts.cache,
        "changed": meta.changed,
        "timestamp": meta.timestamp,
        "rows": meta.shape[0] if meta.shape else 0,
        "columns": meta.schema,
        "stored": cache.data_path(dts).is_file(),
        "phases": {
            phase: [f.__name__ for f in getattr(dts, phase)]
            for phase in ("extractors", "transforms", "validators", "extras", "utilities", "artifacts")
        },
    }

    # The whole docstring, not its first line: in this convention it is the
    # dataset's documentation, and the column semantics a reader needs are in
    # the part that used to be cut off.
    doc = payload["doc"].splitlines() or ["(no docstring)"]
    lines = [
        f"{dts.key}   [{type(dts).__name__}]",
        *(f"  {line}" for line in doc),
        "",
        f"  module     {payload['module']}",
        f"  refs       {', '.join(payload['refs']) or '—'}",
        f"  freshness  {_freshness(dts)}",
        f"  cache      {'parquet' if dts.cache else 'metadata only (the frame is a delta)'}",
        f"  stored     {payload['rows']} rows, {len(meta.schema)} columns"
        + ("" if payload["stored"] else "  (nothing on disk yet)"),
    ]
    for phase, names in payload["phases"].items():
        if names:
            lines.append(f"  {phase:<10} {', '.join(names)}")
    if meta.schema:
        lines += ["", "  columns:"] + [f"    {c:<24} {t}" for c, t in meta.schema.items()]

    if args.rows and payload["stored"]:
        # Nothing hidden: pandas truncates to the terminal by default, and a
        # row of "..." tells a reader nothing about the data.
        with pd.option_context("display.max_columns", None, "display.width", None,
                               "display.max_colwidth", 40):
            lines += ["", dts.dataframe.head(args.rows).to_string()]

    _emit(payload, args.json, "\n".join(lines))


def cmd_plan(args) -> None:
    dts = _target(args.name)
    decisions = plan_run(dts, force=args.force)
    payload = [
        {"key": d.dataset.key, "extract": d.extract, "transform": d.transform, "reason": d.reason}
        for d in decisions
    ]
    _emit(payload, args.json, render(decisions, dts.key))


def cmd_run(args) -> None:
    dts = _target(args.name)
    decisions = run_pipeline(dts, force=args.force)
    payload = [
        {"key": d.dataset.key, "worked": d.works, "elapsed": d.elapsed,
         "reason": d.reason, "extras": d.extras_ran}
        for d in decisions
    ]
    _emit(payload, args.json, render(decisions, dts.key))


def cmd_reset(args) -> None:
    dts = _target(args.name)
    stored = cache.data_path(dts).is_file()
    dts.reset()
    _emit({"key": dts.key, "dropped": stored}, args.json,
          f"{dts.key}: cache dropped" if stored else f"{dts.key}: nothing was stored")


def cmd_graph(args) -> None:
    pool = [n for n in graph.topological(_target(args.name))] if args.name else registry.datasets()
    edges = [(r.key, d.key) for d in pool for r in d.refs if r.key in {p.key for p in pool}]

    if args.format == "mermaid":
        ids = {d.key: f"n{i}" for i, d in enumerate(pool)}
        text = "\n".join(
            ["graph TD"]
            + [f'    {ids[d.key]}["{d.key}"]' for d in pool]
            + [f"    {ids[a]} --> {ids[b]}" for a, b in edges]
        )
    else:
        depth = registry.layers(pool)
        text = "\n".join(f"  {'  ' * depth[d.key]}{d.key}" for d in sorted(pool, key=lambda d: (depth[d.key], d.key)))

    _emit({"nodes": [d.key for d in pool], "edges": edges}, args.json, text)


def cmd_check(args) -> None:
    scan = registry.discover()
    problems = [{"key": path, "problem": f"cannot be imported — {why}"}
                for path, why in scan.failures.items()]
    pool = [_target(args.name)] if args.name else [f.dataset for f in scan.found.values()]

    def note(dts, message):
        problems.append({"key": dts.key, "problem": message})

    # Only things that are actually wrong. Two checks the spec asked for were
    # written, run, and removed: "cache=False with dependants" fired on every
    # loader whose dependants read the database directly and keep the ref for
    # ordering alone, and
    # "extractor on a timer with no probe" described half the depot — it
    # predates the content digest, which is what answers it. Sixteen findings,
    # sixteen false. A check that fires on correct code teaches people to
    # ignore checks.
    for dts in pool:
        if not dts.probe and dts.threshold is None and not dts.refs:
            note(dts, "no probe, no timer and no refs: nothing will ever make it run")
        if dts.probe and dts.threshold is not None:
            note(dts, "a probe and a timer together: the probe already answers exactly")
        if dts.cache and not dts.extractors and not dts.transforms:
            note(dts, "caches a dataframe but has no phase that produces one")
        if dts.probe:
            try:
                value = dts.probe(dts)
            except Exception as e:
                note(dts, f"probe raised {type(e).__name__}: {e}")
            else:
                if value and value < 1_000_000_000:  # not a unix time
                    note(dts, f"probe returned {value!r}, which is not a time — a dependent "
                              "versions itself by the clock and will never see it move")

    try:
        for dts in pool:
            graph.topological(dts)
    except graph.CycleError as e:
        problems.append({"key": None, "problem": str(e)})

    text = "\n".join(f"  {p['key'] or 'graph'}: {p['problem']}" for p in problems) or "  no problems found"
    _emit(problems, args.json, text)


def cmd_template(args) -> None:
    print(TEMPLATE)


# --- entry point --------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="depot", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    # --json belongs to every command rather than to the program, so it can be
    # written where it reads naturally: `depot ls --json`, not `depot --json ls`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")

    def add(name, help, fn, target="optional"):
        p = sub.add_parser(name, help=help, parents=[common])
        if target == "required":
            p.add_argument("name", help="dataset, as type:name or just name")
        elif target == "optional":
            p.add_argument("name", nargs="?", help="dataset; the whole depot if omitted")
        p.set_defaults(fn=fn)
        return p

    add("ls", "every dataset, one line each", cmd_ls, target=None)
    add("show", "everything known about one dataset", cmd_show, "required").add_argument(
        "--rows", type=int, default=0, help="also print this many rows")
    forced = "recompute regardless of freshness — reaches the refs too, so a source on the far end will be fetched again"
    add("plan", "what a run would do, and why", cmd_plan, "required").add_argument(
        "--force", action="store_true", help=forced)
    add("run", "bring a dataset and its refs up to date", cmd_run, "required").add_argument(
        "--force", action="store_true", help=forced)
    add("reset", "drop what is stored, so the next run rebuilds", cmd_reset, "required")
    add("graph", "the shape of the depot, or of one subgraph", cmd_graph).add_argument(
        "--format", choices=["text", "mermaid"], default="text")
    add("check", "what looks wrong in a declaration", cmd_check)
    add("template", "a canonical dataset to copy", cmd_template, target=None)

    args = parser.parse_args(argv)

    # An entry point, so it reads .env the way the other entry points do. The
    # library never does this: a caller that has already configured itself
    # keeps whatever it set.
    #
    # The search starts at the working directory, not at this file. Bare
    # load_dotenv() walks up from the module that called it, which lands in the
    # installed package's own tree — a directory that has nothing to do with the
    # project being run and, once this package is installed from elsewhere, never
    # reaches the project's .env at all.
    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    # The banner is printed on every run, so it is the one place where a
    # misconfigured source costs nothing to notice: a root that is not there
    # and an empty one both answer "no datasets found", and only this line can
    # tell them apart. Whether the path is right is the caller's business —
    # saying what was actually read is ours.
    source = config.source()
    missing = "" if source.is_dir() else "  (no such directory)"
    print(f"# source {source}{missing} · cache {config.cache_dir()}", file=sys.stderr)
    args.fn(args)
