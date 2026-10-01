# dataset-depot

One module declares one dataset. The framework works out what depends on what,
what has gone stale, and what it costs to find out — then recomputes only that.

- `depot` — the framework and its command line.
- `depot_gui` — an optional web interface onto a depot: the graph, the tables,
  the run panel. A demonstration of what the framework offers, not part of its
  contract.

## The promise

**Load a dataset and you get current data.** Nobody who uses a depot — a
person, an agent, or the author coming back a year later — has to know what to
refresh, in what order, or whether it already ran today. Asking for a dataset
runs every step its data needs, and only those.

**The cache makes that fast; it never makes it stale.** Whatever can be reused
is reused: stored results, metadata, answers from sources that have not moved.
But the cache only ever answers when it provably holds what a run would produce
right now. If it cannot prove that, the work runs.

Everything below serves those two sentences.

## Principles

**One module, one dataset.** A dataset is a Python module ending in
`dts = Dataset(...)`. Its identity is its path under the datasets root —
`datasets/reports/balance.py` is `reports:balance` — so the folder tree is the
catalogue. Its refs are ordinary imports, so the dependency graph is read from
the code rather than written beside it. Its module docstring is its
documentation.

**Versions move only when inputs move.** Every dataset carries a version, and a
dependant recomputes when a ref's version is newer than its own. A version
moves when the source says it moved, when fresh data arrives, or when the code
that produces it changes — never because work merely ran. A result identical
to the stored one keeps its version, so nothing downstream wakes for it.

**Freshness is declared, not scheduled.** Each dataset says how to tell it has
gone stale: a *probe* asks the source cheaply for its version (a file's mtime, a
`max(updated_at)`); a *threshold* consults a source that cannot be asked
cheaply at most once every N seconds; a dataset with neither moves when its
refs do. The framework decides what to do when it is asked; when to ask is up
to you — a page opening, an agent's command, a cron line.

**The code is an input too.** The module that declares a dataset is
fingerprinted. Editing it is a reason to recompute; a cosmetic edit that yields
the same output moves nothing. A long-lived process that imported the module
before the edit refuses to run it and asks to be restarted, rather than
storing the old code's output as current.

**Decide from metadata, open data only when asked.** Each stored dataset has a
small metafile beside its parquet: version, schema, shape, fingerprints. A
whole graph is judged from those alone; a dataframe is read only when someone
reads it.

**See before you do.** `plan` reports what a run would do, and why, without
running anything; `run` prints the same log after the fact. What will touch an
external system is visible before it does.

**Safe to share.** One runner per dataset at a time, across threads and
processes, so two callers never fetch the same source twice or write the same
files at once. Writes are atomic. A process that holds a dataset in memory
picks up what another process stored since.

**Written for agents as much as people.** Every command answers in `--json`.
`show` describes a dataset — columns, types, freshness — without loading it.
`depot/SKILL.md` carries the rules that are not visible in the code.

### What it is not

- **Not a scheduler.** It decides *what* is stale when asked; it does not wake
  itself up.
- **Not a distributed system.** One machine, one cache directory, files on
  disk.
- **Not a delivery system.** Extras push data outward once per new version;
  retries and idempotency are theirs to own.

## Install

Not on PyPI yet. Install from git:

```sh
uv add "dataset-depot @ git+https://github.com/v1adx/dataset-depot"
uv add "dataset-depot[gui] @ git+https://github.com/v1adx/dataset-depot"
```

**The distribution is `dataset-depot`; the import is `depot`.** The names differ
because the `depot` distribution on PyPI is an abandoned 2014 package and the
`depot` import namespace belongs to the live `filedepot`. Do not install
`filedepot` into the same environment.

## A dataset

```python
"""What every account currently holds, standard and virtual side by side."""

from depot import Dataset

## Refs
from datasets.store.transactions import dts as transactions


def transform(d: Dataset) -> None:
    df = transactions.dataframe          # the runner already brought the ref up to date
    d.dataframe = df.groupby("account", as_index=False)["amount"].sum()


dts = Dataset(
    refs=[transactions],
    transforms=[transform],
)


if __name__ == "__main__":
    dts.info()
```

```sh
depot run reports:balance
```

The phases run in this order, each a list of functions taking the dataset:

| | |
|---|---|
| `extractors` | go to the source — the only phase that fetches |
| `transforms` | compute from the refs |
| `validators` | raise if the result is wrong; runs before anything is stored |
| `extras` | send the result outward; only when the version moved |
| `utilities` | manual actions, never run by the pipeline |
| `artifacts` | manual too; they return the path of a file they made |

`cache=True` (the default) stores the dataframe as parquet. `cache=False`
keeps only metadata — for data whose product lives elsewhere, such as rows
pushed into a database; reading its dataframe outside a run fetches it anew.

## Commands

```
depot ls                     every dataset, one line each
depot show <name>            refs, freshness, columns, phases
depot show <name> --rows 5   and some data
depot graph <name>           the shape of a subgraph (--format mermaid)
depot check <name>           what looks wrong in a declaration
depot plan <name>            what a run would do, and why — no side effects
depot run <name>             bring it and its refs up to date
depot run                    the whole depot
depot reset <name>           drop what is stored
depot template               a canonical dataset to copy
```

Every command takes `--json`. `plan` and `run` take `--force`, which recomputes
regardless of freshness and reaches the refs as well.

A run of one dataset stops at the first failure: nothing past it could be
current. A run of the whole depot keeps going — a failing dataset stops only
what depends on it — then reports what failed and exits with 1.

## Configuration

| | | |
|---|---|---|
| `DEPOT_SOURCE` | the datasets root | `datasets` |
| `DEPOT_CACHE` | where parquet and metadata are written | `.depot/cache` |

Both are read from the environment; the command line also loads a `.env` found
from the working directory. A library caller that has already configured itself
keeps whatever it set — `depot.config` is never overridden behind its back.
Relative paths resolve against the working directory, so every process sharing
a depot must start from the same one.

The root's **parent** goes on `sys.path`, because refs are declared as ordinary
imports (`from datasets.store.transactions import dts as ...`). The root must
therefore be an importable package: give it an `__init__.py`.

Never point `DEPOT_CACHE` inside a folder a dataset watches — writing the cache
would count as a change to the source.

## The interface

```python
from pathlib import Path
import depot_gui

settings = depot_gui.Settings(
    datasets=Path("datasets"),
    state=Path("state"),
    artifacts=Path("artifacts"),
    colors={"source": "#519DCF", "reports": "#F04561"},
    title="Datasets",
    port=9000,
)

if __name__ in {"__main__", "__mp_main__"}:
    depot_gui.start(settings)
```

Everything project-specific enters through `Settings` and nowhere else. The
tables are served from CDN assets, so nothing is vendored into the package.
Opening a dataset runs its pipeline first — the promise above, kept by the
interface too.

Set `DEPOT_GUI_PASSWORD` (or `Settings.password`) and every page asks for it —
the browser's own prompt, any user name. Unset, the interface is open to
whoever can reach the port, and its chart editor runs Python.

## Writing datasets

`depot/SKILL.md` is the working instruction — the phases, and the handful of
rules that are not visible in the code. It is written for an agent, and reads
just as well for a person.

## Development

```sh
uv sync --all-extras
uv run pytest
```

## License

MIT.
