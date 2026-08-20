# dataset-depot

One module declares one dataset. The framework works out what depends on what,
what has gone stale, and what it costs to find out — then recomputes only that.

- `depot` — the framework and its command line.
- `depot_gui` — an optional web interface onto a depot: the graph, the tables,
  the run panel.

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

A dataset's identity is its path under the datasets root: folders are the type,
the file is the name, a colon between them. `datasets/reports/balance.py` is
`reports:balance`, and the folders nest as deep as you like.

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

The runner walks the refs in topological order, brings each one up to date
exactly once, and stores the result only when the version actually moved. A
dataset with no extractors takes its version from its refs, so recomputing from
unchanged inputs stirs nothing downstream.

## Commands

```
depot ls                     every dataset, one line each
depot show <name>            refs, freshness, columns, phases
depot show <name> --rows 5   and some data
depot graph <name>           the shape of a subgraph (--format mermaid)
depot check <name>           what looks wrong in a declaration
depot plan <name>            what a run would do, and why — no side effects
depot run <name>             do it
depot reset <name>           drop what is stored
depot template               a canonical dataset to copy
```

Every command takes `--json`. `plan` and `run` take `--force`, which recomputes
regardless of freshness and reaches the refs as well.

## Configuration

| | | |
|---|---|---|
| `DEPOT_SOURCE` | the datasets root | `datasets` |
| `DEPOT_CACHE` | where parquet and metadata are written | `.depot/cache` |

Both are read from the environment; the command line also loads a `.env` found
from the working directory. A library caller that has already configured itself
keeps whatever it set — `depot.config` is never overridden behind its back.

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
