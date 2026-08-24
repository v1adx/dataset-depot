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
