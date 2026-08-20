---
name: depot-datasets
description: Use when adding, changing or debugging a dataset in a depot project — the commands to explore with, and the handful of rules that are not visible in the code.
---

# Datasets on depot

A dataset is one module ending in `dts = Dataset(...)`. Its identity is its
path — the folders are the type, the file is the name, a colon between them:
`datasets/reports/sales.py` is `reports:sales`, and the folders nest as deep as
you like — `datasets/store/helper/agents.py` is `store/helper:agents`. One file,
one dataset — pass `name`/`type` only when you need a second one in a module.

A project's folders are its own vocabulary. Run `depot ls` and put your module
where its neighbours live: the ones that fetch from a system usually sit under
that system's name, the ones that shape data for others under something like
`staging`, the ones a human reads under something like `reports`.

## Look before you write

    depot ls                     everything, one line each
    depot show <name>            refs, freshness, columns, phases
    depot show <name> --rows 5   and some data
    depot graph <name>           what it depends on
    depot template               a module to copy

`show` reads the metafile, so columns and row counts cost nothing. **Never
guess a ref's columns** — `depot show <ref>` lists them with their types.

## The phases

Each is a list of functions taking the dataset. They run in this order:

| | |
|---|---|
| `extractors` | go to the source. The only phase that fetches. |
| `transforms` | compute from the refs. |
| `validators` | raise if the result is wrong. Runs before anything is stored. |
| `extras` | send it outward: files, a database, a spreadsheet. |
| `utilities` | manual actions, never run by the pipeline. |
| `artifacts` | manual too, but they return path to generated file (html report, chart image, etc.). |

## Rules you cannot read off the code

**Read a ref with `ref.dataframe`, never `ref.load()`.** By the time your
transform runs, the runner has already brought every ref up to date, in
topological order, each one exactly once. `load()` re-enters the runner and
undoes that.

**A probe must return a time**, on the same wall clock as everything else — a
file's mtime, a `max(updated_at)`. Its answer becomes this dataset's version,
and a dependent compares it against its own, which is a clock reading. A probe
answering with an ETag or a row count hands out a version so small that the
dependent silently stops recomputing forever. If the source cannot be asked
cheaply what its version is, give it a `threshold` and no probe.

**Use a probe when learning the version is cheaper than fetching the data.** A
timer when learning the version *is* fetching it, which is most APIs.

**`cache=False` means the dataframe is a delta, not a product** — the data went
to a database or a spreadsheet and only metadata is kept. Then an empty dataframe says
"went to the source, nothing had changed": the version holds and nothing
downstream wakes. The runner empties it before every extract, so an empty frame
always means this run.

**A dataset with no extractors takes its version from its refs.** It fetched
nothing, so its data is its inputs rearranged. Recomputing from unchanged
inputs lands on the same version and stirs nothing. You do not set versions by
hand; the runner does, and only an extractor that knows better than the
framework should assign `d.changed` itself.

**Extras fire only when the version actually moved.** Whether an extra retries,
or writes idempotently, or shrugs, is its own business — the framework has no
delivery policy and will not call it twice.

**An artifact is a file — return its path, and nothing else.** Where it goes
and how it is written are yours; use it only when the request specifically 
requires a file output; otherwise, use the standard pipeline methods or utilities.

**Nothing runs at import time.** A module builds `dts` and stops. Running
belongs under `if __name__ == "__main__":`.

## Then check it

    depot check <name>     what looks wrong in the declaration
    depot plan <name>      what a run would do, and why — no side effects
    depot run <name>       do it
    depot reset <name>     throw away what is stored

`--force` on plan or run recomputes regardless of freshness. It reaches the
refs as well, so forcing a report can send a source at the far end back to its
API. Prefer letting the freshness rules decide.

**`--force` redoes the work; it does not make the answer newer.** A version
moves only when the inputs moved, so forcing over inputs that stood still
recomputes and then stores nothing — the run says `recomputed, not stored`.
That matters when a transform reads something the graph does not own: the new
answer has no version to arrive under, and forcing will not save it. Declare
that input as a ref and the question does not arise. To replace what is stored
regardless, drop it first: `reset`, then `run`.

You rarely need `reset`. Editing a module is noticed on its own — the runner
fingerprints the file that declared the dataset, so changing it is a reason to
recompute, and if the output comes out identical the version stays put and
nothing downstream is disturbed. `reset` is for throwing away a result you know
to be wrong.

`plan` distinguishes `run` from `maybe`: a timer or probe firing is certain,
while a dataset woken only by a ref may find its ref produced the same content
and never move.

Every command takes `--json`.

## Writing the module

Start from `depot template`. Keep the module docstring: it is what `ls` and
`show` report, and in this convention it *is* the dataset's documentation. Say
what the dataset is, where the data comes from, and anything a reader would
otherwise have to reverse-engineer — why the source is consulted this way, what
an odd column means, what it deliberately leaves out.
