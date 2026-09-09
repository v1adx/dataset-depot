# TODO

Three things the agent-facing side of the framework is missing. Found while
rewriting the operating skill that drives the CLI from an island governor
(`/mnt/data/skills/depot/`).

## 1. A command that runs an artifact or a utility

`artifacts` and `utilities` are the two phases the pipeline never runs, and the
CLI has no command for either. `show` lists them by name and then leaves the
caller to write python:

```bash
uv run python -c "
from datasets.reports.staff_profitability import dts, profitability_chart
dts.pipeline()
print(profitability_chart(dts))
"
```

Every consumer — a skill, a bot, a person — has to know the dotted module path,
remember `pipeline()` before the call, and source `.env` itself, because this is
not the CLI and nothing loads it. That is a whole section of instructions
standing in for one command:

```
depot artifact <key>            list the artifacts this dataset declares
depot artifact <key> <name>     run one, print the path it returned
depot util <key> <name>         run a utility
```

It fits the module's own premise — "every command is a thin renderer" — and
`registry.get` plus `getattr` on the phase list is most of it.

## 2. `python-dotenv` as a real dependency

`cli.main` loads `.env` under `try: from dotenv import ...` / `except
ImportError: pass`. The README states the behaviour without the condition, and
so does every instruction written on top of it. Right now it holds only because
`nicegui` happens to pull dotenv in — a project that installs `dataset-depot`
without the `gui` extra gets a CLI that silently ignores `.env` and then reports
`no datasets found`.

Either declare the dependency, or say in the README that `.env` loading needs
`dataset-depot[dotenv]`.

## 3. Stop copying `SKILL.md` into projects

`depot/SKILL.md` ships inside the wheel, but Claude Code cannot see into an
installed package, so each project keeps a copy at `.claude/skills/depot/`.
The copy in `depot/airenoble` already carries a comment asking whoever reads it
to refresh the file when the contract changes — a TODO addressed to nobody.

A symlink to the installed package's own copy holds automatically, and works
the same whether the framework is installed editable from a sibling folder or
pinned from git:

```sh
ln -s "$(uv run python -c 'import depot,pathlib;print(pathlib.Path(depot.__file__).parent/"SKILL.md")')" \
      .claude/skills/depot/SKILL.md
```

Worth a line in the README under "Writing datasets", next to the sentence that
already points at the file.

## 4. `_caller_file` mistakes a user-side template for the declaring module

`_caller_file` skips frames inside the depot package, so a template living in
`depot/templates/` correctly attributes the dataset to the module that called
it. A template written *by a project* — a `Dataset` subclass whose
`__post_init__` inserts its own extractor — is not in the package, so the walk
stops at the subclass's own frame and every instance takes its identity from
the file that defines the class rather than the file that declares the dataset:

```
datasets:__init__        clients.contracts.dts_raw, entities.dts_businesses, entities.dts_contractors
datasets/helper:__init__ clients.invoices.dts_raw, suppliers.contracts.dts_raw, suppliers.invoices.dts_raw
```

Six datasets, two keys. They share a parquet, `reachable()` collapses them into
one node when two land in the same run, and `registry.discover` drops all of
them on `obj.source != declared_in`, so none appear in the catalog. The failure
is silent in every direction — the only loud case is a declaration and its
helper in one module, which reads back as a self-ref and raises `CycleError`.

The fix is to skip the files that define the dataset's own class chain, not
only the package:

```python
def _declaring_files(cls) -> frozenset[Path]:
    """Files defining the class itself: a user-side template is not the
    module that declared the dataset."""
    out = set()
    for klass in cls.__mro__:
        try:
            out.add(Path(inspect.getfile(klass)).resolve())
        except (TypeError, OSError):
            pass
    return frozenset(out)

# _caller_file(skip): ... if _PKG_DIR not in resolved.parents and resolved not in skip
# __post_init__:      self.source = _caller_file(_declaring_files(type(self)))
```

Until then a project template has to set `name`/`type` itself before calling
`super().__post_init__()`, which is what `depot/exdst` now does.
